from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import pandas as pd

from ..ports import OrderSellerReadPort, PaymentReadPort


_CSV_FILES = {
    "orders": "olist_orders_dataset.csv",
    "items": "olist_order_items_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
}

_REQUIRED_COLUMNS = {
    "orders": {
        "order_id",
        "order_status",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    },
    "items": {
        "order_id",
        "order_item_id",
        "seller_id",
        "shipping_limit_date",
        "price",
        "freight_value",
    },
    "payments": {
        "order_id",
        "payment_sequential",
        "payment_type",
        "payment_installments",
        "payment_value",
    },
    "sellers": {"seller_id"},
}

_OLIST_TIMESTAMP_PATTERN = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
_ORDER_TIMESTAMP_COLUMNS = (
    "order_delivered_carrier_date",
    "order_delivered_customer_date",
    "order_estimated_delivery_date",
)


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    except (OSError, pd.errors.ParserError, UnicodeError) as exc:
        raise ValueError(f"Cannot read Olist CSV {path.name}: {exc}") from exc


def _validate_columns(name: str, frame: pd.DataFrame) -> None:
    missing = sorted(_REQUIRED_COLUMNS[name] - set(frame.columns))
    if missing:
        raise ValueError(f"{name} is missing required columns: {missing}")


def _validate_non_empty(frame: pd.DataFrame, columns: Sequence[str], *, name: str) -> None:
    for column in columns:
        empty = frame[column].str.strip().eq("")
        if empty.any():
            row_numbers = [int(index) + 2 for index in frame.index[empty][:5]]
            raise ValueError(
                f"{name}.{column} contains empty key values at CSV rows {row_numbers}"
            )


def _validate_unique(frame: pd.DataFrame, columns: Sequence[str], *, name: str) -> None:
    duplicates = frame.duplicated(list(columns), keep=False)
    if duplicates.any():
        examples = (
            frame.loc[duplicates, list(columns)]
            .drop_duplicates()
            .head(5)
            .to_dict(orient="records")
        )
        joined = ", ".join(columns)
        raise ValueError(f"{name} key ({joined}) must be unique; examples={examples}")


def _validate_positive_integer_key(frame: pd.DataFrame, column: str, *, name: str) -> None:
    values = frame[column].str.strip()
    valid = values.str.fullmatch(r"[1-9]\d*")
    if not valid.all():
        examples = values.loc[~valid].head(5).tolist()
        raise ValueError(f"{name}.{column} must contain positive integers; examples={examples}")


def _validate_timestamp_precision(
    frame: pd.DataFrame, columns: Sequence[str], *, name: str
) -> None:
    """Reject spreadsheet-style rewrites that silently discard timestamp seconds."""

    for column in columns:
        values = frame[column].str.strip()
        populated = values.ne("")
        canonical = values.str.fullmatch(_OLIST_TIMESTAMP_PATTERN, na=False)
        parsed = pd.to_datetime(
            values.where(populated),
            format="%Y-%m-%d %H:%M:%S",
            errors="coerce",
        )
        invalid = populated & (~canonical | parsed.isna())
        if invalid.any():
            examples = values.loc[invalid].head(5).tolist()
            raise ValueError(
                f"{name}.{column} must preserve canonical Olist timestamps "
                f"(YYYY-MM-DD HH:MM:SS); examples={examples}"
            )


def _normalized_order_ids(order_ids: Iterable[str]) -> frozenset[str]:
    if isinstance(order_ids, (str, bytes)):
        raise TypeError("order_ids must be an iterable of order ID strings, not one string")
    result: set[str] = set()
    for order_id in order_ids:
        if not isinstance(order_id, str) or not order_id.strip():
            raise ValueError("Every requested order_id must be a non-empty string")
        result.add(order_id)
    return frozenset(result)


def _rows_by_order(
    frame: pd.DataFrame, *, sequence_column: str
) -> dict[str, tuple[dict[str, str], ...]]:
    grouped: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    for record in frame.to_dict(orient="records"):
        grouped[record["order_id"]].append(record)
    return {
        order_id: tuple(sorted(rows, key=lambda row: int(row[sequence_column])))
        for order_id, rows in grouped.items()
    }


class OlistRepository(OrderSellerReadPort, PaymentReadPort):
    """A target-filtered, read-only view over the four policy-relevant Olist CSVs."""

    def __init__(
        self,
        *,
        orders: dict[str, dict[str, str]],
        items_by_order: dict[str, tuple[dict[str, str], ...]],
        payments_by_order: dict[str, tuple[dict[str, str], ...]],
        known_seller_ids: frozenset[str],
        source_row_counts: Mapping[str, int],
        retained_row_counts: Mapping[str, int],
    ) -> None:
        self._orders = orders
        self._items_by_order = items_by_order
        self._payments_by_order = payments_by_order
        self._known_seller_ids = known_seller_ids
        self._source_row_counts = dict(source_row_counts)
        self._retained_row_counts = dict(retained_row_counts)

    @classmethod
    def load_for_orders(
        cls, data_dir: str | Path, order_ids: Iterable[str]
    ) -> "OlistRepository":
        directory = Path(data_dir).expanduser().resolve()
        if not directory.exists():
            raise FileNotFoundError(f"Data directory does not exist: {directory}")
        if not directory.is_dir():
            raise NotADirectoryError(f"Data path is not a directory: {directory}")

        missing_files = [
            filename for filename in _CSV_FILES.values() if not (directory / filename).is_file()
        ]
        if missing_files:
            raise FileNotFoundError(f"Missing required Olist CSV files: {sorted(missing_files)}")

        targets = _normalized_order_ids(order_ids)
        frames = {
            name: _read_csv(directory / filename) for name, filename in _CSV_FILES.items()
        }
        for name, frame in frames.items():
            _validate_columns(name, frame)

        orders = frames["orders"]
        items = frames["items"]
        payments = frames["payments"]
        sellers = frames["sellers"]

        _validate_non_empty(orders, ("order_id",), name="orders")
        _validate_non_empty(
            items, ("order_id", "order_item_id", "seller_id"), name="items"
        )
        _validate_non_empty(
            payments, ("order_id", "payment_sequential"), name="payments"
        )
        _validate_non_empty(sellers, ("seller_id",), name="sellers")
        _validate_unique(orders, ("order_id",), name="orders")
        _validate_unique(items, ("order_id", "order_item_id"), name="items")
        _validate_unique(
            payments, ("order_id", "payment_sequential"), name="payments"
        )
        _validate_unique(sellers, ("seller_id",), name="sellers")
        _validate_positive_integer_key(items, "order_item_id", name="items")
        _validate_positive_integer_key(payments, "payment_sequential", name="payments")

        source_order_ids = set(orders["order_id"])
        missing_orders = sorted(targets - source_order_ids)
        if missing_orders:
            preview = missing_orders[:10]
            suffix = "..." if len(missing_orders) > len(preview) else ""
            raise KeyError(f"Requested order IDs are absent from orders: {preview}{suffix}")

        item_order_orphans = sorted(set(items["order_id"]) - source_order_ids)
        payment_order_orphans = sorted(
            set(payments["order_id"]) - source_order_ids
        )
        if item_order_orphans:
            raise ValueError(
                f"items contains order_id values absent from orders: {item_order_orphans[:10]}"
            )
        if payment_order_orphans:
            raise ValueError(
                f"payments contains order_id values absent from orders: {payment_order_orphans[:10]}"
            )

        known_seller_ids = frozenset(sellers["seller_id"])
        unknown_item_sellers = sorted(set(items["seller_id"]) - known_seller_ids)
        if unknown_item_sellers:
            raise ValueError(
                f"items contains seller_id values absent from sellers: {unknown_item_sellers[:10]}"
            )

        # Filter before creating per-order groups. This keeps the repository proportional
        # to the requested case set and never joins the item and payment relations.
        retained_orders = orders.loc[orders["order_id"].isin(targets)].copy()
        retained_items = items.loc[items["order_id"].isin(targets)].copy()
        retained_payments = payments.loc[payments["order_id"].isin(targets)].copy()
        retained_seller_ids = set(retained_items["seller_id"])
        _validate_timestamp_precision(
            retained_orders, _ORDER_TIMESTAMP_COLUMNS, name="orders"
        )
        _validate_timestamp_precision(
            retained_items, ("shipping_limit_date",), name="items"
        )

        order_lookup = {
            record["order_id"]: record for record in retained_orders.to_dict(orient="records")
        }
        items_by_order = _rows_by_order(
            retained_items, sequence_column="order_item_id"
        )
        payments_by_order = _rows_by_order(
            retained_payments, sequence_column="payment_sequential"
        )

        source_counts = {name: int(len(frame)) for name, frame in frames.items()}
        retained_counts = {
            "orders": int(len(retained_orders)),
            "items": int(len(retained_items)),
            "payments": int(len(retained_payments)),
            "sellers": len(retained_seller_ids),
        }
        return cls(
            orders=order_lookup,
            items_by_order=items_by_order,
            payments_by_order=payments_by_order,
            known_seller_ids=known_seller_ids,
            source_row_counts=source_counts,
            retained_row_counts=retained_counts,
        )

    @property
    def source_row_counts(self) -> dict[str, int]:
        return dict(self._source_row_counts)

    @property
    def retained_row_counts(self) -> dict[str, int]:
        return dict(self._retained_row_counts)

    def get_order(self, order_id: str) -> Mapping[str, str]:
        try:
            return dict(self._orders[order_id])
        except KeyError as exc:
            raise KeyError(f"Order was not loaded into this repository: {order_id}") from exc

    def get_items(self, order_id: str) -> Sequence[Mapping[str, str]]:
        return tuple(dict(row) for row in self._items_by_order.get(order_id, ()))

    def get_payments(self, order_id: str) -> Sequence[Mapping[str, str]]:
        return tuple(dict(row) for row in self._payments_by_order.get(order_id, ()))

    def is_known_seller(self, seller_id: str) -> bool:
        return seller_id in self._known_seller_ids
