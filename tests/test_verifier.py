from __future__ import annotations

from copy import deepcopy

from multiagent_a2a.agents.verifier import VerifierAgent
from multiagent_a2a.domain.policy import DeterministicPolicyEngine, assemble_output
from multiagent_a2a.observability import TraceRecorder

from test_domain_policy import make_delivery, make_order, make_payment
from multiagent_a2a.contracts import CaseInput


def test_verifier_repairs_fabricated_evidence():
    trace = TraceRecorder()
    verifier = VerifierAgent(trace)
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
    canonical = assemble_output(case, order, payment, decision)
    candidate = deepcopy(canonical)
    candidate["evidence_ids"].append("seller:fabricated")

    result, repaired = verifier.verify_or_fallback(
        case, candidate, canonical, order, payment
    )

    assert repaired is True
    assert result == canonical
    assert trace.events[-1]["payload"]["candidate_errors"] == [
        "canonical_business_mismatch",
        "evidence_false_positive",
    ]


def test_verifier_rejects_overconfident_payload():
    trace = TraceRecorder()
    verifier = VerifierAgent(trace)
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
    canonical = assemble_output(case, order, payment, decision)
    candidate = deepcopy(canonical)
    candidate["assessment"]["confidence"] = 0.99

    result, repaired = verifier.verify_or_fallback(
        case, candidate, canonical, order, payment
    )

    assert repaired is True
    assert result == canonical
    assert trace.events[-1]["payload"]["candidate_errors"] == [
        "canonical_business_mismatch",
        "confidence_policy_mismatch",
    ]
