from __future__ import annotations

from dataclasses import asdict, is_dataclass
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import Any


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, set):
        return [json_safe(item) for item in sorted(value, key=str)]
    if hasattr(value, "isoformat") and callable(value.isoformat):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    if hasattr(value, "item") and callable(value.item):
        return value.item()
    return value


def atomic_write_text(path: str | Path, text: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, destination)


def atomic_write_json(path: str | Path, payload: Any) -> None:
    content = json.dumps(
        json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False
    ) + "\n"
    atomic_write_text(path, content)

