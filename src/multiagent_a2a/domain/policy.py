"""Deterministic EC_POLICY_V1 decisions and canonical output assembly."""

from __future__ import annotations

from decimal import Decimal

from ..constants import ISSUE_SPECS, OFFICIAL_CONFIDENCE
from ..contracts import (
    CaseInput,
    DeliveryHandoff,
    JsonObject,
    OrderSellerHandoff,
    PaymentHandoff,
    PolicyDecision,
)
from .money import money_decimal, money_float


class PolicyCoverageError(RuntimeError):
    """Raised when verified facts do not satisfy any EC_POLICY_V1 branch."""


class DeterministicPolicyEngine:
    """Apply EC_POLICY_V1 in its required priority order."""

    @staticmethod
    def decide(
        order: OrderSellerHandoff,
        payment: PaymentHandoff,
        delivery: DeliveryHandoff,
    ) -> PolicyDecision:
        status = order.order_status
        payment_total = payment.payment_total_brl
        late_delivery = delivery.late_delivery
        late_sellers = delivery.seller_handoff_after_limit_ids

        if status == "canceled" and payment_total > 0:
            issue = "canceled_order_paid"
            refund = payment_total
            parties: tuple[JsonObject, ...] = (
                {"party_type": "platform", "party_id": "OLIST_PLATFORM"},
            )
        elif status == "unavailable" and payment_total > 0:
            issue = "unavailable_order_paid"
            refund = payment_total
            parties = (
                {"party_type": "platform", "party_id": "OLIST_PLATFORM"},
            )
        elif late_delivery is True and late_sellers:
            issue = "late_delivery_seller"
            refund = order.freight_total_brl
            parties = tuple(
                {"party_type": "seller", "party_id": seller_id}
                for seller_id in late_sellers[:3]
            )
        elif late_delivery is True and delivery.carrier_handoff_verified_on_time:
            issue = "late_delivery_logistics"
            refund = order.freight_total_brl
            parties = (
                {
                    "party_type": "logistics_provider",
                    "party_id": "LOGISTICS_PROVIDER",
                },
            )
        elif payment.payment_count >= 2 and payment.reconciled:
            issue = "valid_split_payment"
            refund = Decimal("0")
            parties = ()
        elif late_delivery is False and payment.reconciled:
            issue = "unsupported_late_claim"
            refund = Decimal("0")
            parties = ()
        else:
            raise PolicyCoverageError(
                "Case is outside EC_POLICY_V1 coverage; refusing to invent a resolution"
            )

        spec = ISSUE_SPECS[issue]
        normalized_refund = money_decimal(refund)
        return PolicyDecision(
            primary_issue=issue,
            cause_code=str(spec["cause_code"]),
            responsible_parties=parties,
            recommended_refund_brl=normalized_refund,
            resolution_action=str(spec["action"]),
            case_status="action_required" if normalized_refund > 0 else "no_action",
        )


def assemble_output(
    case: CaseInput,
    order: OrderSellerHandoff,
    payment: PaymentHandoff,
    decision: PolicyDecision,
) -> JsonObject:
    """Build the exact canonical JSON object required by the README schema."""

    order_id = order.order_id
    item_ids = [item.item_id for item in order.items][:5]
    seller_ids = list(order.seller_ids[:5])
    payment_ids = [payment_fact.payment_id for payment_fact in payment.payments][:5]

    evidence_ids = [f"order:{order_id}"]
    evidence_ids.extend(f"item:{item_id}" for item_id in item_ids)
    evidence_ids.extend(f"payment:{payment_id}" for payment_id in payment_ids)
    evidence_ids.extend(f"seller:{seller_id}" for seller_id in seller_ids)
    policy_evidence = f"policy:{decision.cause_code}"
    evidence_ids = evidence_ids[:9] + [policy_evidence]

    return {
        "case_id": case.case_id,
        "assessment": {
            "primary_issue": decision.primary_issue,
            "case_status": decision.case_status,
            "confidence": OFFICIAL_CONFIDENCE,
        },
        "affected_entities": {
            "order_ids": [order_id],
            "item_ids": item_ids,
            "seller_ids": seller_ids,
            "payment_ids": payment_ids,
        },
        "root_cause_analysis": {
            "ranked_causes": [{"cause_code": decision.cause_code, "rank": 1}],
            "responsible_parties": [dict(party) for party in decision.responsible_parties],
        },
        "evidence_ids": evidence_ids,
        "financial_resolution": {
            "currency": "BRL",
            "item_total_brl": money_float(order.item_total_brl),
            "freight_total_brl": money_float(order.freight_total_brl),
            "payment_total_brl": money_float(payment.payment_total_brl),
            "recommended_refund_brl": money_float(decision.recommended_refund_brl),
        },
        "resolution_actions": [decision.resolution_action],
    }


__all__ = ["DeterministicPolicyEngine", "PolicyCoverageError", "assemble_output"]
