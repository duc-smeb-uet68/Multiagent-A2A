from __future__ import annotations

import csv
import json
from pathlib import Path
import sys
from typing import Any, Callable

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from multiagent_a2a.data import OlistRepository, load_cases  # noqa: E402


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def data_dir(project_root: Path) -> Path:
    return project_root / "data"


@pytest.fixture(scope="session")
def input_dir(project_root: Path) -> Path:
    return project_root / "input"


@pytest.fixture(scope="session")
def official_cases(input_dir: Path):
    return load_cases(input_dir, 50)


@pytest.fixture(scope="session")
def cases_by_id(official_cases):
    return {case.case_id: case for case in official_cases}


@pytest.fixture(scope="session")
def official_repository(data_dir: Path, official_cases):
    return OlistRepository.load_for_orders(
        data_dir, [case.claimed_order_id for case in official_cases]
    )


@pytest.fixture
def valid_case_payload() -> dict[str, Any]:
    return {
        "case_id": "EC_001",
        "opened_at": "2018-10-18T00:00:00-03:00",
        "customer_request": {
            "language": "vi",
            "message": "Kiểm tra đơn hàng.",
            "claimed_order_id": "order-1",
        },
        "policy_version": "EC_POLICY_V1",
    }


@pytest.fixture
def case_dir_factory(tmp_path: Path) -> Callable[[list[dict[str, Any]]], Path]:
    def create(payloads: list[dict[str, Any]]) -> Path:
        directory = tmp_path / "case-input"
        directory.mkdir(parents=True, exist_ok=True)
        for payload in payloads:
            filename = f"{payload['case_id']}.json"
            (directory / filename).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return directory

    return create


@pytest.fixture
def olist_dir_factory(tmp_path: Path) -> Callable[..., Path]:
    fields = {
        "olist_orders_dataset.csv": [
            "order_id",
            "order_status",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ],
        "olist_order_items_dataset.csv": [
            "order_id",
            "order_item_id",
            "seller_id",
            "shipping_limit_date",
            "price",
            "freight_value",
        ],
        "olist_order_payments_dataset.csv": [
            "order_id",
            "payment_sequential",
            "payment_type",
            "payment_installments",
            "payment_value",
        ],
        "olist_sellers_dataset.csv": ["seller_id"],
    }

    def create(
        *,
        item_seller: str = "seller-1",
        seller_rows: list[dict[str, str]] | None = None,
        duplicate_item: bool = False,
    ) -> Path:
        directory = tmp_path / "olist-data"
        directory.mkdir(parents=True, exist_ok=True)
        item = {
            "order_id": "order-1",
            "order_item_id": "1",
            "seller_id": item_seller,
            "shipping_limit_date": "2018-01-02 00:00:00",
            "price": "10.00",
            "freight_value": "2.00",
        }
        rows = {
            "olist_orders_dataset.csv": [
                {
                    "order_id": "order-1",
                    "order_status": "delivered",
                    "order_delivered_carrier_date": "2018-01-01 00:00:00",
                    "order_delivered_customer_date": "2018-01-03 00:00:00",
                    "order_estimated_delivery_date": "2018-01-04 00:00:00",
                }
            ],
            "olist_order_items_dataset.csv": [item, dict(item)]
            if duplicate_item
            else [item],
            "olist_order_payments_dataset.csv": [
                {
                    "order_id": "order-1",
                    "payment_sequential": "1",
                    "payment_type": "credit_card",
                    "payment_installments": "1",
                    "payment_value": "12.00",
                }
            ],
            "olist_sellers_dataset.csv": seller_rows
            if seller_rows is not None
            else [{"seller_id": "seller-1"}],
        }
        for filename, fieldnames in fields.items():
            with (directory / filename).open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows[filename])
        return directory

    return create
