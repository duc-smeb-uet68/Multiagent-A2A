from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from ..constants import ISSUE_SPECS, OFFICIAL_CONFIDENCE
from ..contracts import CaseInput, JsonObject, OrderSellerHandoff, PaymentHandoff
from ..domain.money import money_decimal
from ..ports import TraceSink


class VerifierAgent:
    ROOT_KEYS = {
        "case_id",
        "assessment",
        "affected_entities",
        "root_cause_analysis",
        "evidence_ids",
        "financial_resolution",
        "resolution_actions",
    }

    def __init__(self, trace: TraceSink) -> None:
        self._trace = trace

    @staticmethod
    def _allowed_evidence(
        order: OrderSellerHandoff, payment: PaymentHandoff, cause_code: str
    ) -> set[str]:
        allowed = {f"order:{order.order_id}", f"policy:{cause_code}"}
        allowed.update(f"item:{item.item_id}" for item in order.items)
        allowed.update(f"payment:{row.payment_id}" for row in payment.payments)
        allowed.update(f"seller:{seller_id}" for seller_id in order.seller_ids)
        return allowed

    def validate(
        self,
        candidate: Any,
        canonical: JsonObject,
        order: OrderSellerHandoff,
        payment: PaymentHandoff,
    ) -> list[str]:
        errors: list[str] = []
        if not isinstance(candidate, dict) or set(candidate) != self.ROOT_KEYS:
            return ["root_schema_keys"]
        try:
            json.dumps(candidate, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            errors.append("not_strict_json")
        if candidate != canonical:
            errors.append("canonical_business_mismatch")

        assessment = candidate.get("assessment")
        if not isinstance(assessment, dict):
            return errors + ["assessment_schema"]
        issue = assessment.get("primary_issue")
        if issue not in ISSUE_SPECS:
            return errors + ["primary_issue_enum"]
        confidence = assessment.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            errors.append("confidence_type")
        elif not 0 <= confidence <= 1:
            errors.append("confidence_range")
        elif confidence != OFFICIAL_CONFIDENCE:
            errors.append("confidence_policy_mismatch")

        entities = candidate.get("affected_entities")
        if not isinstance(entities, dict):
            errors.append("affected_entities_schema")
        else:
            for key in ("order_ids", "item_ids", "seller_ids", "payment_ids"):
                value = entities.get(key)
                if not isinstance(value, list) or len(value) > 5:
                    errors.append(f"entity_limit:{key}")

        roots = candidate.get("root_cause_analysis")
        if not isinstance(roots, dict):
            errors.append("root_cause_schema")
            roots = {}
        ranked = roots.get("ranked_causes", [])
        parties = roots.get("responsible_parties", [])
        if not isinstance(ranked, list) or len(ranked) > 3:
            errors.append("root_cause_limit")
        if not isinstance(parties, list) or len(parties) > 3:
            errors.append("responsible_party_limit")

        actions = candidate.get("resolution_actions")
        evidence = candidate.get("evidence_ids")
        if not isinstance(actions, list) or len(actions) > 5:
            errors.append("action_limit")
        if not isinstance(evidence, list) or len(evidence) > 10:
            errors.append("evidence_limit")
            evidence = []

        cause_code = ISSUE_SPECS[issue]["cause_code"]
        if ranked != [{"cause_code": cause_code, "rank": 1}]:
            errors.append("cause_or_rank_mismatch")
        if actions != [ISSUE_SPECS[issue]["action"]]:
            errors.append("action_mismatch")
        allowed_evidence = self._allowed_evidence(order, payment, cause_code)
        if any(not isinstance(item, str) or item not in allowed_evidence for item in evidence):
            errors.append("evidence_false_positive")

        try:
            financial = candidate.get("financial_resolution", {})
            refund = money_decimal(financial.get("recommended_refund_brl", 0))
            expected_status = "action_required" if refund > 0 else "no_action"
            if assessment.get("case_status") != expected_status:
                errors.append("status_refund_mismatch")
        except (AttributeError, TypeError, ValueError):
            errors.append("financial_schema")
        return errors

    def verify_or_fallback(
        self,
        case: CaseInput,
        candidate: JsonObject,
        canonical: JsonObject,
        order: OrderSellerHandoff,
        payment: PaymentHandoff,
    ) -> tuple[JsonObject, bool]:
        errors = self.validate(candidate, canonical, order, payment)
        repaired = bool(errors)
        result = deepcopy(canonical) if repaired else candidate
        final_errors = self.validate(result, canonical, order, payment)
        if final_errors:
            raise ValueError(
                f"Canonical verifier failure for {case.case_id}: {final_errors}"
            )
        self._trace.emit(
            case.case_id,
            "VerifierAgent",
            "verification",
            {"valid": True, "repaired": repaired, "candidate_errors": errors},
            from_agent="VerifierAgent",
            to_agent="CoordinatorAgent",
        )
        return result, repaired
