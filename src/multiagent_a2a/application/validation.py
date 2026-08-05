from __future__ import annotations

from pathlib import Path

from ..agents import (
    CoordinatorAgent,
    DeliveryAgent,
    OrderSellerAgent,
    PaymentAgent,
    PolicyAgent,
    VerifierAgent,
)
from ..artifacts.qa import validate_and_package
from ..config import RunConfig
from ..contracts import QaReport
from ..data import OlistRepository, load_cases
from ..llm.qwen import QwenGateway
from ..observability import TraceRecorder


def validate_existing_outputs(
    *,
    data_dir: Path,
    input_dir: Path,
    output_dir: Path,
    zip_path: Path,
    expected_case_count: int,
    strict_official_assertions: bool,
) -> QaReport:
    """Rebuild canonical results from source data and compare every disk output.

    This validation is deliberately model-free. It resolves entity/evidence IDs,
    policy priority, money and all cross-field invariants through the same agents
    and deterministic policy used by the production pipeline.
    """

    config = RunConfig(
        data_dir=data_dir.expanduser().resolve(),
        input_dir=input_dir.expanduser().resolve(),
        work_root=zip_path.expanduser().resolve().parent,
        enable_llm=False,
        mirror_logging=False,
        strict_official_assertions=strict_official_assertions,
        expected_case_count=expected_case_count,
    )
    cases = load_cases(config.input_dir, expected_case_count)
    repository = OlistRepository.load_for_orders(
        config.data_dir, [case.claimed_order_id for case in cases]
    )
    trace = TraceRecorder()
    gateway = QwenGateway(config).load()
    try:
        coordinator = CoordinatorAgent(
            order_seller=OrderSellerAgent(repository, trace),
            payment=PaymentAgent(repository, trace),
            delivery=DeliveryAgent(trace),
            policy=PolicyAgent(gateway, trace),
            verifier=VerifierAgent(trace),
            trace=trace,
        )
        canonical_results = {
            case.case_id: coordinator.process(case).output for case in cases
        }
        return validate_and_package(
            in_memory_results=canonical_results,
            output_dir=output_dir.expanduser().resolve(),
            zip_path=zip_path.expanduser().resolve(),
            expected_case_count=expected_case_count,
            strict_official_assertions=strict_official_assertions,
        )
    finally:
        gateway.close()

