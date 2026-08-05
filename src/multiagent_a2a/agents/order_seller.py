from __future__ import annotations

from ..contracts import CaseInput, ItemFact, OrderSellerHandoff
from ..domain.money import money_decimal, sum_money
from ..ports import OrderSellerReadPort, TraceSink


class OrderSellerAgent:
    def __init__(self, repository: OrderSellerReadPort, trace: TraceSink) -> None:
        self._repository = repository
        self._trace = trace

    def analyze(self, case: CaseInput) -> OrderSellerHandoff:
        order = self._repository.get_order(case.claimed_order_id)
        item_records: list[ItemFact] = []
        for row in self._repository.get_items(case.claimed_order_id):
            seller_id = row["seller_id"]
            if not self._repository.is_known_seller(seller_id):
                raise ValueError(f"Unknown seller {seller_id} for {case.case_id}")
            item_records.append(
                ItemFact(
                    item_id=f"{case.claimed_order_id}:{row['order_item_id']}",
                    order_item_id=str(row["order_item_id"]),
                    seller_id=seller_id,
                    shipping_limit_date=row["shipping_limit_date"],
                    price_brl=money_decimal(row["price"]),
                    freight_brl=money_decimal(row["freight_value"]),
                )
            )
        seller_ids = tuple(dict.fromkeys(item.seller_id for item in item_records))
        handoff = OrderSellerHandoff(
            order_id=case.claimed_order_id,
            order_status=order["order_status"],
            order_delivered_carrier_date=order["order_delivered_carrier_date"],
            order_delivered_customer_date=order["order_delivered_customer_date"],
            order_estimated_delivery_date=order["order_estimated_delivery_date"],
            items=tuple(item_records),
            seller_ids=seller_ids,
            item_total_brl=sum_money(item.price_brl for item in item_records),
            freight_total_brl=sum_money(item.freight_brl for item in item_records),
        )
        self._trace.emit(
            case.case_id,
            "OrderSellerAgent",
            "handoff",
            handoff,
            from_agent="OrderSellerAgent",
            to_agent="CoordinatorAgent",
        )
        return handoff

