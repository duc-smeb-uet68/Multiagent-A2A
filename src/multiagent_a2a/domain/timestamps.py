"""Timestamp parsing with the same semantics as the source Olist CSVs."""

from __future__ import annotations

import pandas as pd


def parsed_timestamp(value: object | None) -> pd.Timestamp | None:
    """Parse one timestamp without applying a timezone conversion."""

    if value is None or not str(value).strip():
        return None
    result = pd.to_datetime(str(value), errors="coerce")
    return None if pd.isna(result) else result


__all__ = ["parsed_timestamp"]
