from __future__ import annotations

from dataclasses import replace

from ..constants import POLICY_VERSION
from ..contracts import (
    CaseInput,
    DeliveryHandoff,
    OrderSellerHandoff,
    PaymentHandoff,
    PolicyDecision,
)
from ..domain.policy import DeterministicPolicyEngine
from ..ports import PolicyProposer, TraceSink


class PolicyAgent:
    def __init__(
        self,
        gateway: PolicyProposer,
        trace: TraceSink,
        engine: DeterministicPolicyEngine | None = None,
    ) -> None:
        self._gateway = gateway
        self._trace = trace
        self._engine = engine or DeterministicPolicyEngine()

    @staticmethod
    def _candidate_matches(candidate: dict | None, expected: PolicyDecision) -> bool:
        required_keys = {"primary_issue", "cause_code", "party_type", "party_id"}
        if not isinstance(candidate, dict) or set(candidate) != required_keys:
            return False
        if candidate["primary_issue"] != expected.primary_issue:
            return False
        if candidate["cause_code"] != expected.cause_code:
            return False
        parties = expected.responsible_parties
        if not parties:
            return candidate["party_type"] is None and candidate["party_id"] is None
        if len(parties) != 1:
            return False
        return (
            candidate["party_type"] == parties[0]["party_type"]
            and candidate["party_id"] == parties[0]["party_id"]
        )

    def decide(
        self,
        case: CaseInput,
        order: OrderSellerHandoff,
        payment: PaymentHandoff,
        delivery: DeliveryHandoff,
    ) -> PolicyDecision:
        expected = self._engine.decide(order, payment, delivery)
        llm_input = {
            "policy_version": POLICY_VERSION,
            "order": {"status": order.order_status},
            "payment": {
                "row_count": payment.payment_count,
                "payment_total_brl": payment.payment_total_brl,
                "reconciled": payment.reconciled,
            },
            "delivery": {
                "late_delivery": delivery.late_delivery,
                "seller_handoff_after_limit_ids": delivery.seller_handoff_after_limit_ids,
                "carrier_handoff_verified_on_time": delivery.carrier_handoff_verified_on_time,
            },
        }
        candidate, model_meta = self._gateway.propose_policy(llm_input)
        accepted = self._candidate_matches(candidate, expected)
        if accepted:
            decision = replace(
                expected, decision_source="qwen_validated", fallback_reason=None
            )
        else:
            fallback_reason = (
                str(model_meta.get("reason") or "model_unavailable")
                if candidate is None
                else "model_candidate_conflicted_with_deterministic_policy"
            )
            decision = replace(
                expected,
                decision_source="deterministic_fallback",
                fallback_reason=fallback_reason,
            )
        self._trace.emit(
            case.case_id,
            "PolicyAgent",
            "decision",
            {
                "llm_input": llm_input,
                "model_candidate": candidate,
                "model_meta": model_meta,
                "accepted": accepted,
                "authoritative_decision": decision,
            },
            from_agent="PolicyAgent",
            to_agent="CoordinatorAgent",
        )
        return decision

