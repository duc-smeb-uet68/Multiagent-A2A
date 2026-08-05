from __future__ import annotations

import importlib.metadata
import re
from typing import Any


def version_tuple(value: str) -> tuple[int, int, int]:
    numbers = re.findall(r"\d+", str(value))
    parts = (numbers + ["0", "0", "0"])[:3]
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def hardware_metadata() -> dict[str, Any]:
    result: dict[str, Any] = {"gpu_count": 0, "gpus": []}
    try:
        import torch

        result["torch_version"] = torch.__version__
        result["cuda_available"] = torch.cuda.is_available()
        if torch.cuda.is_available():
            result["gpu_count"] = torch.cuda.device_count()
            result["gpus"] = [
                {
                    "name": torch.cuda.get_device_name(index),
                    "memory_gib": round(
                        torch.cuda.get_device_properties(index).total_memory / 1024**3,
                        2,
                    ),
                }
                for index in range(torch.cuda.device_count())
            ]
    except Exception as exc:  # Metadata must never prevent deterministic execution.
        result["torch_error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
    return result

