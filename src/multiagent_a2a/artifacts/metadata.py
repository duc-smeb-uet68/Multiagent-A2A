from __future__ import annotations

from collections import Counter
import platform
from typing import Any, Mapping

import pandas as pd

from ..config import RunConfig
from ..constants import MODEL_PARAMETER_SIZE, POLICY_VERSION
from ..contracts import CaseRunInfo, QaReport
from ..observability.trace import TraceRecorder
from ..runtime.environment import hardware_metadata, installed_version


def build_metadata(
    *,
    config: RunConfig,
    trace: TraceRecorder,
    gateway_snapshot: Mapping[str, Any],
    duration_seconds: float,
    run_info: Mapping[str, CaseRunInfo],
    qa: QaReport,
    source_row_counts: Mapping[str, int],
    retained_row_counts: Mapping[str, int],
) -> dict[str, Any]:
    qwen_count = sum(info.decision_source == "qwen_validated" for info in run_info.values())
    fallback_count = len(run_info) - qwen_count
    fallback_reasons = Counter(
        info.fallback_reason or "unspecified"
        for info in run_info.values()
        if info.decision_source == "deterministic_fallback"
    )
    return {
        "model": {
            "configured_model": config.model_id,
            "parameter_size": MODEL_PARAMETER_SIZE,
            "model_limit_compliance": "8.2B <= 10B per agent",
            "network_permitted": False,
            "download_policy": "forbidden_local_files_only",
            **dict(gateway_snapshot),
        },
        "policy_version": POLICY_VERSION,
        "framework": {
            "orchestration": "custom Python typed structured-handoff multi-agent",
            "inference": "Hugging Face Transformers (optional, local-only)",
            "transformers_version": installed_version("transformers"),
            "accelerate_version": installed_version("accelerate"),
            "bitsandbytes_version": installed_version("bitsandbytes"),
            "pandas_version": pd.__version__,
        },
        "runtime": {
            "environment": "kaggle" if config.is_kaggle else "local",
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "started_at_utc": trace.started_at.isoformat(),
            "duration_seconds": duration_seconds,
            **hardware_metadata(),
        },
        "data": {
            "source_row_counts": dict(source_row_counts),
            "retained_row_counts": dict(retained_row_counts),
        },
        "run": {
            "run_id": trace.run_id,
            "cases_processed": len(run_info),
            "qwen_validated_cases": qwen_count,
            "deterministic_fallback_cases": fallback_count,
            "fallback_cases_by_reason": dict(sorted(fallback_reasons.items())),
            "verifier_repairs": sum(info.verifier_repaired for info in run_info.values()),
            "trace_events": trace.event_count,
            "qa": qa.to_dict(),
        },
    }
