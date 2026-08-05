from __future__ import annotations

from copy import deepcopy
import time

from ..contracts import (
    CaseInput,
    CaseRunInfo,
    DeliveryItemScope,
    DeliveryScope,
    ProcessedCase,
)
from ..domain.policy import assemble_output
from ..ports import TraceSink
from .delivery import DeliveryAgent
from .order_seller import OrderSellerAgent
from .payment import PaymentAgent
from .policy import PolicyAgent
from .verifier import VerifierAgent


class CoordinatorAgent:
    def __init__(
        self,
        *,
        order_seller: OrderSellerAgent,
        payment: PaymentAgent,
        delivery: DeliveryAgent,
        policy: PolicyAgent,
        verifier: VerifierAgent,
        trace: TraceSink,
    ) -> None:
        self._order_seller = order_seller
        self._payment = payment
        self._delivery = delivery
        self._policy = policy
        self._verifier = verifier
        self._trace = trace

    def process(self, case: CaseInput) -> ProcessedCase:
        started = time.perf_counter()
        self._trace.emit(
            case.case_id,
            "CoordinatorAgent",
            "case_started",
            {
                "source_file": case.source_filename,
                "claimed_order_id": case.claimed_order_id,
                "policy_version": case.policy_version,
            },
        )
        self._trace.emit(
            case.case_id,
            "CoordinatorAgent",
            "dispatch",
            {"scope": ["case_id", "claimed_order_id"]},
            from_agent="CoordinatorAgent",
            to_agent="OrderSellerAgent",
        )
        order = self._order_seller.analyze(case)

        self._trace.emit(
            case.case_id,
            "CoordinatorAgent",
            "dispatch",
            {"scope": ["freight_total_brl", "item_total_brl", "order_id"]},
            from_agent="CoordinatorAgent",
            to_agent="PaymentAgent",
        )
        payment = self._payment.analyze(case, order)

        delivery_scope = DeliveryScope(
            order_id=order.order_id,
            order_delivered_carrier_date=order.order_delivered_carrier_date,
            order_delivered_customer_date=order.order_delivered_customer_date,
            order_estimated_delivery_date=order.order_estimated_delivery_date,
            items=tuple(
                DeliveryItemScope(
                    seller_id=item.seller_id,
                    shipping_limit_date=item.shipping_limit_date,
                )
                for item in order.items
            ),
        )
        self._trace.emit(
            case.case_id,
            "CoordinatorAgent",
            "dispatch",
            {
                "scope": [
                    "items",
                    "order_delivered_carrier_date",
                    "order_delivered_customer_date",
                    "order_estimated_delivery_date",
                    "order_id",
                ],
                "item_count": len(delivery_scope.items),
            },
            from_agent="OrderSellerAgent",
            to_agent="DeliveryAgent",
        )
        delivery = self._delivery.analyze(case, delivery_scope)
        decision = self._policy.decide(case, order, payment, delivery)
        canonical = assemble_output(case, order, payment, decision)
        verified, verifier_repaired = self._verifier.verify_or_fallback(
            case, deepcopy(canonical), canonical, order, payment
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        run_info = CaseRunInfo(
            decision_source=decision.decision_source,
            fallback_reason=decision.fallback_reason,
            verifier_repaired=verifier_repaired,
            elapsed_ms=elapsed_ms,
        )
        self._trace.emit(
            case.case_id,
            "CoordinatorAgent",
            "case_completed",
            {
                "decision_source": run_info.decision_source,
                "fallback_reason": run_info.fallback_reason,
                "verifier_repaired": run_info.verifier_repaired,
                "elapsed_ms": run_info.elapsed_ms,
                "primary_issue": verified["assessment"]["primary_issue"],
                "recommended_refund_brl": verified["financial_resolution"][
                    "recommended_refund_brl"
                ],
            },
        )
        return ProcessedCase(output=verified, run_info=run_info)
