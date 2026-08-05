from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path

from .constants import EXPECTED_CASE_COUNT, MODEL_ID


def env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _validated_marker_dir(path: str | Path, marker: str, label: str) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not (candidate / marker).is_file():
        raise FileNotFoundError(f"{label}={candidate} does not contain {marker}")
    return candidate


def discover_marker_dir(
    *,
    explicit: str | Path | None,
    marker: str,
    local_subdir: str,
    search_root: str | Path | None = None,
) -> Path:
    if explicit:
        return _validated_marker_dir(explicit, marker, "explicit path")

    roots = []
    if search_root is not None:
        roots.append(Path(search_root))
    roots.append(Path.cwd())
    candidates: list[Path] = []
    for root in roots:
        candidates.extend((root / local_subdir, root))
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / marker).is_file():
            return resolved

    kaggle_input = Path("/kaggle/input")
    if kaggle_input.exists():
        matches = sorted(kaggle_input.rglob(marker), key=lambda item: (len(item.parts), str(item)))
        if matches:
            return matches[0].parent.resolve()
    raise FileNotFoundError(
        f"Cannot locate {marker}. Provide an explicit path or attach the project data on Kaggle."
    )


@dataclass(frozen=True, slots=True)
class RunConfig:
    data_dir: Path
    input_dir: Path
    work_root: Path
    model_path: Path | None = None
    model_id: str = MODEL_ID
    enable_llm: bool = True
    mirror_logging: bool = True
    strict_official_assertions: bool = True
    expected_case_count: int = EXPECTED_CASE_COUNT
    max_new_tokens: int = 96

    @property
    def is_kaggle(self) -> bool:
        return Path("/kaggle").exists()

    @property
    def output_dir(self) -> Path:
        return self.work_root / "output"

    @property
    def logging_dir(self) -> Path:
        return self.work_root / "logging"

    @property
    def trace_path(self) -> Path:
        return self.work_root / "trace.jsonl"

    @property
    def metadata_path(self) -> Path:
        return self.work_root / "metadata.json"

    @property
    def mirrored_trace_path(self) -> Path:
        return self.logging_dir / "trace.jsonl"

    @property
    def mirrored_metadata_path(self) -> Path:
        return self.logging_dir / "metadata.json"

    @property
    def submission_zip(self) -> Path:
        return self.work_root / "submission.zip"

    def prepare_directories(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.mirror_logging:
            self.logging_dir.mkdir(parents=True, exist_ok=True)

    def with_overrides(self, **changes: object) -> "RunConfig":
        normalized = {
            key: Path(value).expanduser().resolve()
            if key in {"data_dir", "input_dir", "work_root", "model_path"} and value is not None
            else value
            for key, value in changes.items()
        }
        return replace(self, **normalized)

    @classmethod
    def from_env(cls, *, search_root: str | Path | None = None) -> "RunConfig":
        data_dir = discover_marker_dir(
            explicit=os.getenv("EC_DATA_DIR"),
            marker="olist_orders_dataset.csv",
            local_subdir="data",
            search_root=search_root,
        )
        input_dir = discover_marker_dir(
            explicit=os.getenv("EC_INPUT_DIR"),
            marker="EC_001.json",
            local_subdir="input",
            search_root=search_root,
        )
        default_work_root = Path("/kaggle/working") if Path("/kaggle").exists() else Path.cwd()
        work_root = Path(os.getenv("EC_WORK_ROOT", str(default_work_root))).expanduser().resolve()
        raw_model_path = os.getenv("QWEN_MODEL_PATH")
        model_path = Path(raw_model_path).expanduser().resolve() if raw_model_path else None
        enable_llm = env_flag("EC_ENABLE_LLM", True) and not env_flag(
            "EC_FORCE_RULE_FALLBACK", False
        )
        return cls(
            data_dir=data_dir,
            input_dir=input_dir,
            work_root=work_root,
            model_path=model_path,
            enable_llm=enable_llm,
            mirror_logging=env_flag("EC_MIRROR_LOGGING", True),
            strict_official_assertions=env_flag("EC_STRICT_OFFICIAL_ASSERTIONS", True),
            expected_case_count=int(os.getenv("EC_EXPECTED_CASE_COUNT", str(EXPECTED_CASE_COUNT))),
            max_new_tokens=int(os.getenv("QWEN_MAX_NEW_TOKENS", "96")),
        )

    def describe(self) -> dict[str, object]:
        return {
            "environment": "kaggle" if self.is_kaggle else "local",
            "data_dir": str(self.data_dir),
            "input_dir": str(self.input_dir),
            "work_root": str(self.work_root),
            "model": self.model_id,
            "model_path": str(self.model_path) if self.model_path else None,
            "llm_requested": self.enable_llm,
            "model_downloads_allowed": False,
        }
