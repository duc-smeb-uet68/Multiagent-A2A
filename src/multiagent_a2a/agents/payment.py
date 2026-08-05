from __future__ import annotations

from decimal import Decimal

from ..constants import RECONCILIATION_TOLERANCE
from ..contracts import CaseInput, OrderSellerHandoff, PaymentFact, PaymentHandoff
from ..domain.money import money_decimal, sum_money
from ..ports import PaymentReadPort, TraceSink


class PaymentAgent:
    def __init__(self, repository: PaymentReadPort, trace: TraceSink) -> None:
        self._repository = repository
        self._trace = trace

    def analyze(self, case: CaseInput, financial_scope: OrderSellerHandoff) -> PaymentHandoff:
        records = tuple(
            PaymentFact(
                payment_id=f"{financial_scope.order_id}:{row['payment_sequential']}",
                payment_sequential=str(row["payment_sequential"]),
                payment_type=row["payment_type"],
                payment_value_brl=money_decimal(row["payment_value"]),
            )
            for row in self._repository.get_payments(financial_scope.order_id)
        )
        payment_total = sum_money(record.payment_value_brl for record in records)
        expected_total = money_decimal(
            financial_scope.item_total_brl + financial_scope.freight_total_brl
        )
        difference = money_decimal(abs(payment_total - expected_total))
        handoff = PaymentHandoff(
            order_id=financial_scope.order_id,
            payments=records,
            payment_count=len(records),
            payment_total_brl=payment_total,
            expected_item_plus_freight_brl=expected_total,
            difference_brl=difference,
            reconciled=difference <= RECONCILIATION_TOLERANCE,
        )
        self._trace.emit(
            case.case_id,
            "PaymentAgent",
            "handoff",
            handoff,
            from_agent="PaymentAgent",
            to_agent="CoordinatorAgent",
        )
        return handoff

