from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

from multiagent_a2a.config import RunConfig
from multiagent_a2a.llm import (
    ModelOutputParseError,
    QwenGateway,
    parse_first_json_object,
)
import multiagent_a2a.llm.qwen as qwen_module


HEAVY_MODEL_MODULES = {"accelerate", "bitsandbytes", "torch", "transformers"}


def _run_config(tmp_path: Path, **overrides: object) -> RunConfig:
    values: dict[str, object] = {
        "data_dir": tmp_path / "data",
        "input_dir": tmp_path / "input",
        "work_root": tmp_path / "work",
        "enable_llm": False,
    }
    values.update(overrides)
    return RunConfig(**values)  # type: ignore[arg-type]


def _forbid_heavy_model_imports(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    imported: list[str] = []
    original_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: dict | None = None,
        locals: dict | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ):
        root_name = name.split(".", 1)[0]
        if root_name in HEAVY_MODEL_MODULES:
            imported.append(name)
            raise AssertionError(f"unexpected model runtime import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    return imported


def test_parse_plain_json_object() -> None:
    assert parse_first_json_object('{"primary_issue":"valid_split_payment"}') == {
        "primary_issue": "valid_split_payment"
    }


def test_parse_json_inside_markdown_fence() -> None:
    response = '```json\n{"party_type": null, "party_id": null}\n```\nDone.'
    assert parse_first_json_object(response) == {"party_type": None, "party_id": None}


def test_parse_json_after_thinking_block() -> None:
    response = (
        "<think>Do not expose this reasoning.</think>\n"
        '{"cause_code":"DELIVERY_WITHIN_ESTIMATE"}'
    )
    assert parse_first_json_object(response) == {
        "cause_code": "DELIVERY_WITHIN_ESTIMATE"
    }


@pytest.mark.parametrize(
    "response",
    [
        "not JSON",
        "```json\n{missing quotes: true}\n```",
        "[1, 2, 3]",
        "<think>unfinished reasoning only",
    ],
)
def test_parse_malformed_response_raises_typed_error(response: str) -> None:
    with pytest.raises(ModelOutputParseError, match="No JSON object"):
        parse_first_json_object(response)


def test_disabled_gateway_never_imports_or_loads_model_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    imported = _forbid_heavy_model_imports(monkeypatch)
    gateway = QwenGateway(_run_config(tmp_path, enable_llm=False))

    assert gateway.load() is gateway
    assert gateway.status == "disabled_deterministic_fallback"
    candidate, metadata = gateway.propose_policy({"order": {"status": "delivered"}})

    assert candidate is None
    assert metadata["ok"] is False
    assert metadata["error"]["code"] == "disabled"
    assert metadata["backend"] == "deterministic_fallback"
    assert imported == []

    snapshot = gateway.snapshot()
    assert snapshot["ready"] is False
    assert snapshot["effective_model"] is None
    assert snapshot["network_permitted"] is False
    assert snapshot["offline_enforced"] is True


def test_qwen_source_has_no_download_path_and_hard_codes_local_loading() -> None:
    source_path = Path(qwen_module.__file__).resolve()
    source = source_path.read_text(encoding="utf-8")
    compact_source = "".join(source.split())

    assert "snapshot_download" not in source
    assert "pip install" not in source.lower()
    assert "local_files_only=False" not in compact_source

    tree = ast.parse(source, filename=str(source_path))
    from_pretrained_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "from_pretrained"
    ]
    assert from_pretrained_calls, "gateway must load tokenizer/model through from_pretrained"

    for call in from_pretrained_calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        local_only = keywords.get("local_files_only")
        trust_remote_code = keywords.get("trust_remote_code")
        assert isinstance(local_only, ast.Constant) and local_only.value is True
        assert (
            isinstance(trust_remote_code, ast.Constant)
            and trust_remote_code.value is False
        )


def test_invalid_explicit_asset_returns_typed_fallback_without_model_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    imported = _forbid_heavy_model_imports(monkeypatch)
    invalid_model_dir = tmp_path / "not-a-model"
    invalid_model_dir.mkdir()
    config = _run_config(
        tmp_path,
        enable_llm=True,
        model_path=invalid_model_dir,
    )

    gateway = QwenGateway(config).load()
    assert gateway.status == "model_asset_fallback"
    assert gateway.ready is False
    assert imported == []

    snapshot = gateway.snapshot()
    assert snapshot["error_counts"] == {"asset_invalid": 1}
    assert snapshot["errors"][0] == {
        "code": "asset_invalid",
        "phase": "discovery",
        "message": "config.json is missing",
        "recoverable": True,
        "source_kind": "explicit_path",
    }

    candidate, metadata = gateway.propose_policy({})
    assert candidate is None
    assert metadata["reason"] == "asset_invalid"
    assert metadata["error"] == snapshot["errors"][0]
    assert metadata["backend"] == "deterministic_fallback"


def test_non_object_model_config_falls_back_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    imported = _forbid_heavy_model_imports(monkeypatch)
    invalid_model_dir = tmp_path / "malformed-model"
    invalid_model_dir.mkdir()
    (invalid_model_dir / "config.json").write_text("[]\n", encoding="utf-8")

    gateway = QwenGateway(
        _run_config(
            tmp_path,
            enable_llm=True,
            model_path=invalid_model_dir,
        )
    ).load()

    assert gateway.status == "model_asset_fallback"
    assert gateway.snapshot()["error_counts"] == {"asset_invalid": 1}
    assert "JSON object" in gateway.snapshot()["errors"][0]["message"]
    assert imported == []
