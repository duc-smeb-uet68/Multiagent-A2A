from __future__ import annotations

from collections import Counter
from decimal import Decimal
import json
import os
from pathlib import Path
import zipfile

from ..constants import (
    GOLDEN_ISSUE_COUNTS,
    GOLDEN_STATUS_COUNTS,
    GOLDEN_TOTALS,
    OFFICIAL_CONFIDENCE,
)
from ..contracts import JsonObject, QaReport
from ..domain.money import money_decimal


class ArtifactQaError(AssertionError):
    pass


def _aggregate(
    results: dict[str, JsonObject],
) -> tuple[Counter[str], Counter[str], Counter[Decimal], dict[str, Decimal]]:
    issue_counts = Counter(
        payload["assessment"]["primary_issue"] for payload in results.values()
    )
    status_counts = Counter(
        payload["assessment"]["case_status"] for payload in results.values()
    )
    confidence_counts: Counter[Decimal] = Counter()
    for case_id, payload in results.items():
        confidence = payload["assessment"].get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ArtifactQaError(f"Invalid confidence type in {case_id}: {confidence!r}")
        normalized = Decimal(str(confidence))
        if not Decimal("0") <= normalized <= Decimal("1"):
            raise ArtifactQaError(f"Confidence outside [0, 1] in {case_id}: {confidence!r}")
        confidence_counts[normalized] += 1
    keys = (
        "item_total_brl",
        "freight_total_brl",
        "payment_total_brl",
        "recommended_refund_brl",
    )
    totals = {
        key: money_decimal(
            sum(
                (
                    Decimal(str(payload["financial_resolution"][key]))
                    for payload in results.values()
                ),
                Decimal("0"),
            )
        )
        for key in keys
    }
    return issue_counts, status_counts, confidence_counts, totals


def validate_and_package(
    *,
    in_memory_results: dict[str, JsonObject],
    output_dir: Path,
    zip_path: Path,
    expected_case_count: int,
    strict_official_assertions: bool,
) -> QaReport:
    expected_names = {
        f"EC_{index:03d}.json" for index in range(1, expected_case_count + 1)
    }
    output_paths = sorted(output_dir.glob("EC_*.json"))
    actual_names = {path.name for path in output_paths}
    if actual_names != expected_names:
        raise ArtifactQaError(
            "Output filenames mismatch: "
            f"missing={sorted(expected_names - actual_names)}, "
            f"extra={sorted(actual_names - expected_names)}"
        )

    disk_results: dict[str, JsonObject] = {}
    for path in output_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("case_id") != path.stem:
            raise ArtifactQaError(f"case_id mismatch in {path.name}")
        if payload != in_memory_results.get(path.stem):
            raise ArtifactQaError(f"Disk/in-memory mismatch in {path.name}")
        disk_results[path.stem] = payload

    issue_counts, status_counts, confidence_counts, totals = _aggregate(disk_results)
    if strict_official_assertions:
        if expected_case_count != 50:
            raise ArtifactQaError("Official golden assertions require exactly 50 cases")
        if issue_counts != Counter(GOLDEN_ISSUE_COUNTS):
            raise ArtifactQaError(
                f"Issue-count mismatch: actual={dict(issue_counts)}, expected={GOLDEN_ISSUE_COUNTS}"
            )
        if status_counts != Counter(GOLDEN_STATUS_COUNTS):
            raise ArtifactQaError(
                f"Status-count mismatch: actual={dict(status_counts)}, expected={GOLDEN_STATUS_COUNTS}"
            )
        expected_confidence_counts = Counter(
            {Decimal(str(OFFICIAL_CONFIDENCE)): expected_case_count}
        )
        if confidence_counts != expected_confidence_counts:
            raise ArtifactQaError(
                "Confidence-profile mismatch: "
                f"actual={dict(confidence_counts)}, expected={dict(expected_confidence_counts)}"
            )
        if totals != GOLDEN_TOTALS:
            raise ArtifactQaError(
                f"Aggregate-total mismatch: actual={totals}, expected={GOLDEN_TOTALS}"
            )

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_zip = zip_path.with_name(zip_path.name + ".tmp")
    with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in output_paths:
            archive.write(path, arcname=path.name)
    with zipfile.ZipFile(temporary_zip, "r") as archive:
        zip_names = archive.namelist()
        if len(zip_names) != expected_case_count or set(zip_names) != expected_names:
            raise ArtifactQaError("submission.zip must contain exactly the expected EC_*.json files")
        if any("/" in name or "\\" in name for name in zip_names):
            raise ArtifactQaError("JSON files must be stored at the ZIP root")
    os.replace(temporary_zip, zip_path)

    return QaReport(
        issue_counts=dict(issue_counts),
        status_counts=dict(status_counts),
        confidence_counts={str(key): value for key, value in confidence_counts.items()},
        aggregate_totals=totals,
        output_count=len(disk_results),
        zip_path=zip_path,
    )
