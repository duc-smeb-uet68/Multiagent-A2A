from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class CaseInput:
    case_id: str
    opened_at: str
    language: str
    message: str
    claimed_order_id: str
    policy_version: str
    source_filename: str


@dataclass(frozen=True, slots=True)
class ItemFact:
    item_id: str
    order_item_id: str
    seller_id: str
    shipping_limit_date: str
    price_brl: Decimal
    freight_brl: Decimal


@dataclass(frozen=True, slots=True)
class OrderSellerHandoff:
    order_id: str
    order_status: str
    order_delivered_carrier_date: str
    order_delivered_customer_date: str
    order_estimated_delivery_date: str
    items: tuple[ItemFact, ...]
    seller_ids: tuple[str, ...]
    item_total_brl: Decimal
    freight_total_brl: Decimal


@dataclass(frozen=True, slots=True)
class PaymentFact:
    payment_id: str
    payment_sequential: str
    payment_type: str
    payment_value_brl: Decimal


@dataclass(frozen=True, slots=True)
class PaymentHandoff:
    order_id: str
    payments: tuple[PaymentFact, ...]
    payment_count: int
    payment_total_brl: Decimal
    expected_item_plus_freight_brl: Decimal
    difference_brl: Decimal
    reconciled: bool


@dataclass(frozen=True, slots=True)
class DeliveryItemScope:
    seller_id: str
    shipping_limit_date: str


@dataclass(frozen=True, slots=True)
class DeliveryScope:
    order_id: str
    order_delivered_carrier_date: str
    order_delivered_customer_date: str
    order_estimated_delivery_date: str
    items: tuple[DeliveryItemScope, ...]


@dataclass(frozen=True, slots=True)
class DeliveryHandoff:
    order_id: str
    late_delivery: bool | None
    seller_handoff_after_limit_ids: tuple[str, ...]
    timestamps_complete_for_delivery: bool
    carrier_timestamp_available: bool
    shipping_limits_complete: bool
    carrier_handoff_verified_on_time: bool


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    primary_issue: str
    cause_code: str
    responsible_parties: tuple[JsonObject, ...]
    recommended_refund_brl: Decimal
    resolution_action: str
    case_status: str
    decision_source: str = "deterministic_fallback"
    fallback_reason: str | None = None


@dataclass(frozen=True, slots=True)
class CaseRunInfo:
    decision_source: str
    fallback_reason: str | None
    verifier_repaired: bool
    elapsed_ms: float


@dataclass(frozen=True, slots=True)
class ProcessedCase:
    output: JsonObject
    run_info: CaseRunInfo


@dataclass(frozen=True, slots=True)
class QaReport:
    issue_counts: dict[str, int]
    status_counts: dict[str, int]
    confidence_counts: dict[str, int]
    aggregate_totals: dict[str, Decimal]
    output_count: int
    zip_path: Path

    def to_dict(self) -> JsonObject:
        return {
            "issue_counts": dict(self.issue_counts),
            "status_counts": dict(self.status_counts),
            "confidence_counts": dict(self.confidence_counts),
            "aggregate_totals": {key: str(value) for key, value in self.aggregate_totals.items()},
            "output_count": self.output_count,
            "zip_path": str(self.zip_path),
        }


@dataclass(frozen=True, slots=True)
class RunReport:
    cases_processed: int
    duration_seconds: float
    qwen_validated_cases: int
    deterministic_fallback_cases: int
    verifier_repairs: int
    output_dir: Path
    trace_path: Path
    metadata_path: Path
    submission_zip: Path
    qa: QaReport

    def to_dict(self) -> JsonObject:
        return {
            "cases_processed": self.cases_processed,
            "duration_seconds": self.duration_seconds,
            "qwen_validated_cases": self.qwen_validated_cases,
            "deterministic_fallback_cases": self.deterministic_fallback_cases,
            "verifier_repairs": self.verifier_repairs,
            "output_dir": str(self.output_dir),
            "trace_path": str(self.trace_path),
            "metadata_path": str(self.metadata_path),
            "submission_zip": str(self.submission_zip),
            "qa": self.qa.to_dict(),
        }
