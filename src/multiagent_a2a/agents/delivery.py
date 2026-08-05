from __future__ import annotations

from ..contracts import CaseInput, DeliveryHandoff, DeliveryScope
from ..domain.timestamps import parsed_timestamp
from ..ports import TraceSink


class DeliveryAgent:
    def __init__(self, trace: TraceSink) -> None:
        self._trace = trace

    def analyze(self, case: CaseInput, scope: DeliveryScope) -> DeliveryHandoff:
        delivered = parsed_timestamp(scope.order_delivered_customer_date)
        estimated = parsed_timestamp(scope.order_estimated_delivery_date)
        carrier = parsed_timestamp(scope.order_delivered_carrier_date)
        late_delivery = (
            None if delivered is None or estimated is None else bool(delivered > estimated)
        )

        shipping_limits = [parsed_timestamp(item.shipping_limit_date) for item in scope.items]
        violating_sellers: list[str] = []
        if carrier is not None:
            for item, shipping_limit in zip(scope.items, shipping_limits, strict=True):
                if shipping_limit is not None and carrier > shipping_limit:
                    violating_sellers.append(item.seller_id)

        shipping_limits_complete = bool(scope.items) and all(
            limit is not None for limit in shipping_limits
        )
        carrier_handoff_verified_on_time = (
            carrier is not None
            and shipping_limits_complete
            and all(carrier <= limit for limit in shipping_limits if limit is not None)
        )
        handoff = DeliveryHandoff(
            order_id=scope.order_id,
            late_delivery=late_delivery,
            seller_handoff_after_limit_ids=tuple(dict.fromkeys(violating_sellers)),
            timestamps_complete_for_delivery=delivered is not None and estimated is not None,
            carrier_timestamp_available=carrier is not None,
            shipping_limits_complete=shipping_limits_complete,
            carrier_handoff_verified_on_time=carrier_handoff_verified_on_time,
        )
        self._trace.emit(
            case.case_id,
            "DeliveryAgent",
            "handoff",
            handoff,
            from_agent="DeliveryAgent",
            to_agent="CoordinatorAgent",
        )
        return handoff

