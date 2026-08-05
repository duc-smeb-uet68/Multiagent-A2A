from __future__ import annotations

from collections.abc import Callable
import json
import time
from typing import Any

from ..agents import (
    CoordinatorAgent,
    DeliveryAgent,
    OrderSellerAgent,
    PaymentAgent,
    PolicyAgent,
    VerifierAgent,
)
from ..artifacts.json_io import atomic_write_json
from ..artifacts.metadata import build_metadata
from ..artifacts.qa import validate_and_package
from ..config import RunConfig
from ..contracts import CaseRunInfo, JsonObject, RunReport
from ..data import OlistRepository, load_cases
from ..llm.qwen import QwenGateway
from ..observability import TraceRecorder

ProgressCallback = Callable[[int, int], None]


def _trace_paths(config: RunConfig) -> list:
    paths = [config.trace_path]
    if config.mirror_logging:
        paths.append(config.mirrored_trace_path)
    return paths


def _metadata_paths(config: RunConfig) -> list:
    paths = [config.metadata_path]
    if config.mirror_logging:
        paths.append(config.mirrored_metadata_path)
    return paths


def run_pipeline(
    config: RunConfig, *, progress_callback: ProgressCallback | None = None
) -> RunReport:
    """Run all configured cases, verify every output and publish atomic artifacts.

    Model failures are recoverable and select deterministic policy fallback. Invalid
    inputs, uncovered policies and QA failures remain hard errors.
    """

    config.prepare_directories()
    if config.submission_zip.exists():
        config.submission_zip.unlink()

    trace = TraceRecorder()
    gateway: QwenGateway | None = None
    try:
        cases = load_cases(config.input_dir, config.expected_case_count)
        repository = OlistRepository.load_for_orders(
            config.data_dir, [case.claimed_order_id for case in cases]
        )
        gateway = QwenGateway(config).load()
        trace.emit(
            None,
            "QwenGateway",
            "backend_initialized",
            gateway.snapshot(),
        )

        coordinator = CoordinatorAgent(
            order_seller=OrderSellerAgent(repository, trace),
            payment=PaymentAgent(repository, trace),
            delivery=DeliveryAgent(trace),
            policy=PolicyAgent(gateway, trace),
            verifier=VerifierAgent(trace),
            trace=trace,
        )

        started = time.perf_counter()
        results: dict[str, JsonObject] = {}
        run_info: dict[str, CaseRunInfo] = {}
        for index, case in enumerate(cases, start=1):
            processed = coordinator.process(case)
            atomic_write_json(config.output_dir / case.source_filename, processed.output)
            results[case.case_id] = processed.output
            run_info[case.case_id] = processed.run_info
            if progress_callback is not None and (
                index % 10 == 0 or index == len(cases)
            ):
                progress_callback(index, len(cases))

        qa = validate_and_package(
            in_memory_results=results,
            output_dir=config.output_dir,
            zip_path=config.submission_zip,
            expected_case_count=config.expected_case_count,
            strict_official_assertions=config.strict_official_assertions,
        )
        duration_seconds = round(time.perf_counter() - started, 3)
        qwen_count = sum(
            info.decision_source == "qwen_validated" for info in run_info.values()
        )
        fallback_count = len(run_info) - qwen_count
        verifier_repairs = sum(info.verifier_repaired for info in run_info.values())
        trace.emit(
            None,
            "CoordinatorAgent",
            "run_completed",
            {
                "cases_processed": len(results),
                "duration_seconds": duration_seconds,
                "qwen_validated_cases": qwen_count,
                "deterministic_fallback_cases": fallback_count,
                "qa_passed": True,
            },
        )

        metadata = build_metadata(
            config=config,
            trace=trace,
            gateway_snapshot=gateway.snapshot(),
            duration_seconds=duration_seconds,
            run_info=run_info,
            qa=qa,
            source_row_counts=repository.source_row_counts,
            retained_row_counts=repository.retained_row_counts,
        )
        trace.flush(_trace_paths(config))
        for path in _metadata_paths(config):
            atomic_write_json(path, metadata)

        return RunReport(
            cases_processed=len(results),
            duration_seconds=duration_seconds,
            qwen_validated_cases=qwen_count,
            deterministic_fallback_cases=fallback_count,
            verifier_repairs=verifier_repairs,
            output_dir=config.output_dir,
            trace_path=config.trace_path,
            metadata_path=config.metadata_path,
            submission_zip=config.submission_zip,
            qa=qa,
        )
    except Exception as exc:
        trace.emit(
            None,
            "CoordinatorAgent",
            "run_failed",
            {"error_type": type(exc).__name__, "error": str(exc)[:1000]},
        )
        trace.flush(_trace_paths(config))
        raise
    finally:
        if gateway is not None:
            gateway.close()

