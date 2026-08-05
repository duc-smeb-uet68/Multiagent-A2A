"""Exact BRL arithmetic used by the dispute-resolution policy."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ..constants import MONEY_QUANTUM


def money_decimal(value: object | None) -> Decimal:
    """Normalize a monetary value to two decimal places using half-up rounding."""

    if isinstance(value, Decimal):
        number = value
    else:
        text = "0" if value is None else str(value).strip()
        if text == "" or text.lower() in {"nan", "nat", "none"}:
            text = "0"
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid monetary value: {value!r}") from exc
    return number.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def sum_money(values: Iterable[object]) -> Decimal:
    """Sum monetary rows exactly once and normalize the final total."""

    total = sum(
        (Decimal(str(value).strip() or "0") for value in values),
        Decimal("0"),
    )
    return money_decimal(total)


def money_float(value: object | None) -> float:
    """Convert a normalized monetary value to its JSON numeric representation."""

    return float(money_decimal(value))


__all__ = ["money_decimal", "money_float", "sum_money"]
