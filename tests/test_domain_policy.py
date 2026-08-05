from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from multiagent_a2a.constants import ISSUE_SPECS
from multiagent_a2a.contracts import (
    CaseInput,
    DeliveryHandoff,
    ItemFact,
    OrderSellerHandoff,
    PaymentFact,
    PaymentHandoff,
)
from multiagent_a2a.domain.money import money_decimal, sum_money
from multiagent_a2a.domain.policy import (
    DeterministicPolicyEngine,
    PolicyCoverageError,
    assemble_output,
)
from multiagent_a2a.domain.timestamps import parsed_timestamp


def make_order(status: str = "delivered") -> OrderSellerHandoff:
    item = ItemFact(
        item_id="order-1:1",
        order_item_id="1",
        seller_id="seller-1",
        shipping_limit_date="2018-01-02 00:00:00",
        price_brl=Decimal("100.00"),
        freight_brl=Decimal("15.00"),
    )
    return OrderSellerHandoff(
        order_id="order-1",
        order_status=status,
        order_delivered_carrier_date="2018-01-02 00:00:00",
        order_delivered_customer_date="2018-01-04 00:00:00",
        order_estimated_delivery_date="2018-01-03 00:00:00",
        items=(item,),
        seller_ids=("seller-1",),
        item_total_brl=Decimal("100.00"),
        freight_total_brl=Decimal("15.00"),
    )


def make_payment(*, count: int = 1, total: str = "115.00", reconciled: bool = True) -> PaymentHandoff:
    value = Decimal(total) / count
    rows = tuple(
        PaymentFact(
            payment_id=f"order-1:{index}",
            payment_sequential=str(index),
            payment_type="credit_card",
            payment_value_brl=value,
        )
        for index in range(1, count + 1)
    )
    return PaymentHandoff(
        order_id="order-1",
        payments=rows,
        payment_count=count,
        payment_total_brl=Decimal(total),
        expected_item_plus_freight_brl=Decimal("115.00"),
        difference_brl=Decimal("0.00" if reconciled else "1.00"),
        reconciled=reconciled,
    )


def make_delivery(
    *, late: bool | None, late_sellers: tuple[str, ...] = (), on_time_handoff: bool = False
) -> DeliveryHandoff:
    return DeliveryHandoff(
        order_id="order-1",
        late_delivery=late,
        seller_handoff_after_limit_ids=late_sellers,
        timestamps_complete_for_delivery=late is not None,
        carrier_timestamp_available=on_time_handoff or bool(late_sellers),
        shipping_limits_complete=on_time_handoff or bool(late_sellers),
        carrier_handoff_verified_on_time=on_time_handoff,
    )


@pytest.mark.parametrize(
    ("order", "payment", "delivery", "issue"),
    [
        (make_order("canceled"), make_payment(), make_delivery(late=True, late_sellers=("seller-1",)), "canceled_order_paid"),
        (make_order("unavailable"), make_payment(total="25.00", reconciled=False), make_delivery(late=None), "unavailable_order_paid"),
        (make_order(), make_payment(), make_delivery(late=True, late_sellers=("seller-1",)), "late_delivery_seller"),
        (make_order(), make_payment(), make_delivery(late=True, on_time_handoff=True), "late_delivery_logistics"),
        (make_order(), make_payment(count=2), make_delivery(late=False, on_time_handoff=True), "valid_split_payment"),
        (make_order(), make_payment(), make_delivery(late=False, on_time_handoff=True), "unsupported_late_claim"),
    ],
)
def test_policy_branches_and_priority(order, payment, delivery, issue):
    decision = DeterministicPolicyEngine.decide(order, payment, delivery)
    assert decision.primary_issue == issue
    assert decision.cause_code == ISSUE_SPECS[issue]["cause_code"]
    assert decision.resolution_action == ISSUE_SPECS[issue]["action"]


def test_missing_delivery_proof_fails_closed():
    with pytest.raises(PolicyCoverageError):
        DeterministicPolicyEngine.decide(
            make_order(), make_payment(), make_delivery(late=True)
        )


def test_money_is_decimal_half_up_and_timestamps_are_strict():
    assert money_decimal("0.005") == Decimal("0.01")
    assert money_decimal("") == Decimal("0.00")
    assert sum_money(["0.10", "0.20"]) == Decimal("0.30")
    with pytest.raises(ValueError):
        money_decimal("not-money")
    assert parsed_timestamp("") is None
    assert parsed_timestamp("not-a-time") is None
    assert parsed_timestamp("2018-01-01 00:00:00") is not None


def test_canonical_output_uses_only_source_derived_evidence():
    case = CaseInput(
        case_id="EC_001",
        opened_at="2018-01-01T00:00:00-03:00",
        language="vi",
        message="test",
        claimed_order_id="order-1",
        policy_version="EC_POLICY_V1",
        source_filename="EC_001.json",
    )
    order = make_order()
    payment = make_payment()
    decision = DeterministicPolicyEngine.decide(
        order, payment, make_delivery(late=True, late_sellers=("seller-1",))
    )
    payload = assemble_output(case, order, payment, decision)
    assert payload["assessment"]["confidence"] == 0.92
    assert payload["evidence_ids"] == [
        "order:order-1",
        "item:order-1:1",
        "payment:order-1:1",
        "seller:seller-1",
        "policy:SELLER_HANDOFF_AFTER_LIMIT",
    ]
    assert payload["financial_resolution"]["recommended_refund_brl"] == 15.0
