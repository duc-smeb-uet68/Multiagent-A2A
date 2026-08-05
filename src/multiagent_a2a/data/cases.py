from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..constants import POLICY_VERSION
from ..contracts import CaseInput


class _DuplicateJsonKey(ValueError):
    """Raised internally when an input object contains the same key twice."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _require_exact_keys(
    value: dict[str, Any], expected: set[str], *, location: str, filename: str
) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    raise ValueError(
        f"Invalid schema in {filename} at {location}: missing={missing}, extra={extra}"
    )


def _required_string(value: Any, *, field: str, filename: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Invalid schema in {filename}: {field} must be a non-empty string")
    return value


def load_cases(input_dir: str | Path, expected_count: int) -> tuple[CaseInput, ...]:
    """Load and validate the exact ``EC_*.json`` input set without mutating it.

    The returned contracts are immutable and ordered by their canonical filename.
    Files unrelated to JSON (for example ``.gitkeep``) are ignored, while every JSON
    file in the directory must be one of the expected ``EC_001`` ... names.
    """

    if isinstance(expected_count, bool) or not isinstance(expected_count, int):
        raise TypeError("expected_count must be an integer")
    if expected_count <= 0:
        raise ValueError("expected_count must be greater than zero")

    directory = Path(input_dir).expanduser().resolve()
    if not directory.exists():
        raise FileNotFoundError(f"Input directory does not exist: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {directory}")

    expected_names = {f"EC_{index:03d}.json" for index in range(1, expected_count + 1)}
    json_paths = [
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() == ".json"
    ]
    actual_names = {path.name for path in json_paths}
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        raise ValueError(f"Input set mismatch: missing={missing}, extra={extra}")

    top_level_keys = {"case_id", "opened_at", "customer_request", "policy_version"}
    request_keys = {"language", "message", "claimed_order_id"}
    cases: list[CaseInput] = []
    seen_case_ids: set[str] = set()
    seen_order_ids: set[str] = set()

    for filename in sorted(expected_names):
        path = directory / filename
        try:
            payload = json.loads(
                path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
            )
        except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateJsonKey) as exc:
            raise ValueError(f"Cannot parse {filename} as strict UTF-8 JSON: {exc}") from exc

        if not isinstance(payload, dict):
            raise ValueError(f"Invalid schema in {filename}: top-level value must be an object")
        _require_exact_keys(payload, top_level_keys, location="$", filename=filename)

        request = payload["customer_request"]
        if not isinstance(request, dict):
            raise ValueError(
                f"Invalid schema in {filename}: customer_request must be an object"
            )
        _require_exact_keys(
            request,
            request_keys,
            location="$.customer_request",
            filename=filename,
        )

        case_id = _required_string(payload["case_id"], field="case_id", filename=filename)
        opened_at = _required_string(
            payload["opened_at"], field="opened_at", filename=filename
        )
        policy_version = _required_string(
            payload["policy_version"], field="policy_version", filename=filename
        )
        language = _required_string(
            request["language"], field="customer_request.language", filename=filename
        )
        message = _required_string(
            request["message"], field="customer_request.message", filename=filename
        )
        claimed_order_id = _required_string(
            request["claimed_order_id"],
            field="customer_request.claimed_order_id",
            filename=filename,
        )

        if case_id != path.stem:
            raise ValueError(
                f"Filename/case_id mismatch in {filename}: expected {path.stem!r}, got {case_id!r}"
            )
        if policy_version != POLICY_VERSION:
            raise ValueError(
                f"Unsupported policy in {filename}: expected {POLICY_VERSION!r}, "
                f"got {policy_version!r}"
            )
        if case_id in seen_case_ids:
            raise ValueError(f"Duplicate case_id in input set: {case_id}")
        if claimed_order_id in seen_order_ids:
            raise ValueError(f"Duplicate claimed_order_id in input set: {claimed_order_id}")

        seen_case_ids.add(case_id)
        seen_order_ids.add(claimed_order_id)
        cases.append(
            CaseInput(
                case_id=case_id,
                opened_at=opened_at,
                language=language,
                message=message,
                claimed_order_id=claimed_order_id,
                policy_version=policy_version,
                source_filename=filename,
            )
        )

    return tuple(cases)
