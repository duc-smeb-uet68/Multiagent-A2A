from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Sequence

from .application.pipeline import run_pipeline
from .application.validation import validate_existing_outputs
from .config import RunConfig, discover_marker_dir, env_flag
from .constants import EXPECTED_CASE_COUNT, MODEL_ID


def _add_boolean(parser: argparse.ArgumentParser, name: str, help_text: str) -> None:
    parser.add_argument(
        f"--{name}",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=help_text,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multiagent-a2a",
        description="Offline-first Olist dispute-resolution pipeline",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Process cases and publish artifacts")
    run_parser.add_argument("--data-dir", type=Path)
    run_parser.add_argument("--input-dir", type=Path)
    run_parser.add_argument("--work-root", type=Path)
    run_parser.add_argument(
        "--model-path",
        type=Path,
        help="Path to an already attached Qwen3-8B asset; never downloaded",
    )
    run_parser.add_argument("--expected-case-count", type=int)
    run_parser.add_argument("--max-new-tokens", type=int)
    _add_boolean(run_parser, "llm", "Use a local Qwen model when one is available")
    _add_boolean(run_parser, "mirror-logging", "Mirror trace/metadata under logging/")
    _add_boolean(
        run_parser,
        "strict-official-assertions",
        "Enforce official 50-case golden totals",
    )

    validate_parser = subparsers.add_parser(
        "validate", help="Validate an existing output directory and rebuild its ZIP"
    )
    validate_parser.add_argument("--data-dir", type=Path)
    validate_parser.add_argument("--input-dir", type=Path)
    validate_parser.add_argument("--output-dir", type=Path, required=True)
    validate_parser.add_argument("--zip-path", type=Path, required=True)
    validate_parser.add_argument(
        "--expected-case-count", type=int, default=EXPECTED_CASE_COUNT
    )
    _add_boolean(
        validate_parser,
        "strict-official-assertions",
        "Enforce official 50-case golden totals",
    )
    return parser


def _run_config(args: argparse.Namespace) -> RunConfig:
    data_dir = discover_marker_dir(
        explicit=args.data_dir or os.getenv("EC_DATA_DIR"),
        marker="olist_orders_dataset.csv",
        local_subdir="data",
    )
    input_dir = discover_marker_dir(
        explicit=args.input_dir or os.getenv("EC_INPUT_DIR"),
        marker="EC_001.json",
        local_subdir="input",
    )
    default_work_root = Path("/kaggle/working") if Path("/kaggle").exists() else Path.cwd()
    work_root = Path(
        args.work_root or os.getenv("EC_WORK_ROOT") or default_work_root
    ).expanduser().resolve()
    raw_model_path = args.model_path or os.getenv("QWEN_MODEL_PATH")
    model_path = Path(raw_model_path).expanduser().resolve() if raw_model_path else None
    enable_llm = (
        args.llm
        if args.llm is not None
        else env_flag("EC_ENABLE_LLM", True)
        and not env_flag("EC_FORCE_RULE_FALLBACK", False)
    )
    mirror_logging = (
        args.mirror_logging
        if args.mirror_logging is not None
        else env_flag("EC_MIRROR_LOGGING", True)
    )
    strict = (
        args.strict_official_assertions
        if args.strict_official_assertions is not None
        else env_flag("EC_STRICT_OFFICIAL_ASSERTIONS", True)
    )
    return RunConfig(
        data_dir=data_dir,
        input_dir=input_dir,
        work_root=work_root,
        model_path=model_path,
        model_id=MODEL_ID,
        enable_llm=enable_llm,
        mirror_logging=mirror_logging,
        strict_official_assertions=strict,
        expected_case_count=args.expected_case_count
        or int(os.getenv("EC_EXPECTED_CASE_COUNT", str(EXPECTED_CASE_COUNT))),
        max_new_tokens=args.max_new_tokens
        or int(os.getenv("QWEN_MAX_NEW_TOKENS", "96")),
    )


def _command_run(args: argparse.Namespace) -> int:
    config = _run_config(args)
    print(json.dumps(config.describe(), ensure_ascii=False, indent=2), file=sys.stderr)
    report = run_pipeline(
        config,
        progress_callback=lambda done, total: print(
            f"Processed {done}/{total} cases", file=sys.stderr
        ),
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _command_validate(args: argparse.Namespace) -> int:
    output_dir = args.output_dir.expanduser().resolve()
    data_dir = discover_marker_dir(
        explicit=args.data_dir or os.getenv("EC_DATA_DIR"),
        marker="olist_orders_dataset.csv",
        local_subdir="data",
    )
    input_dir = discover_marker_dir(
        explicit=args.input_dir or os.getenv("EC_INPUT_DIR"),
        marker="EC_001.json",
        local_subdir="input",
    )
    strict = (
        args.strict_official_assertions
        if args.strict_official_assertions is not None
        else True
    )
    report = validate_existing_outputs(
        data_dir=data_dir,
        input_dir=input_dir,
        output_dir=output_dir,
        zip_path=args.zip_path.expanduser().resolve(),
        expected_case_count=args.expected_case_count,
        strict_official_assertions=strict,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _command_run(args)
        if args.command == "validate":
            return _command_validate(args)
    except (FileNotFoundError, ValueError, KeyError, AssertionError) as exc:
        print(f"ERROR [{type(exc).__name__}]: {exc}", file=sys.stderr)
        return 2
    parser.error(f"Unknown command: {args.command}")
    return 2
