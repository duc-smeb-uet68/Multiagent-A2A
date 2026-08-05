"""Offline-only Qwen3-8B inference gateway.

The gateway deliberately has no code path that downloads a model or installs a
package.  It can use an explicit local directory, a Qwen3-8B asset attached
under ``/kaggle/input``, or an already-existing Hugging Face cache snapshot.
Business-policy fallback remains outside this module; callers receive typed,
recoverable metadata whenever local inference is unavailable.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
import gc
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping

from ..config import RunConfig
from ..constants import MODEL_ID
from .parsing import ModelOutputParseError, parse_first_json_object


_MAX_INPUT_TOKENS = 2_048
_EXPECTED_CONFIG: dict[str, Any] = {
    "model_type": "qwen3",
    "hidden_size": 4096,
    "intermediate_size": 12288,
    "num_hidden_layers": 36,
    "num_attention_heads": 32,
    "num_key_value_heads": 8,
    "vocab_size": 151936,
    # These two fields distinguish the requested post-trained Qwen3-8B from
    # Qwen3-8B-Base, which otherwise has the same layer dimensions.
    "eos_token_id": 151645,
    "max_position_embeddings": 40960,
}
_OFFLINE_ENVIRONMENT = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
}


class GatewayErrorCode(str, Enum):
    """Stable failure codes consumed by traces and deterministic fallback."""

    DISABLED = "disabled"
    NOT_READY = "not_ready"
    CLOSED = "closed"
    MODEL_ID_MISMATCH = "model_id_mismatch"
    ASSET_NOT_FOUND = "asset_not_found"
    ASSET_INVALID = "asset_invalid"
    DEPENDENCY_MISSING = "dependency_missing"
    DEPENDENCY_INCOMPATIBLE = "dependency_incompatible"
    CUDA_UNAVAILABLE = "cuda_unavailable"
    QUANTIZATION_UNAVAILABLE = "quantization_unavailable"
    LOAD_OOM = "load_oom"
    LOAD_FAILED = "load_failed"
    PROMPT_TOO_LARGE = "prompt_too_large"
    INFERENCE_OOM = "inference_oom"
    INFERENCE_FAILED = "inference_failed"
    OUTPUT_PARSE_FAILED = "output_parse_failed"


@dataclass(frozen=True, slots=True)
class GatewayError:
    """Sanitized, JSON-ready error information."""

    code: GatewayErrorCode
    phase: str
    message: str
    recoverable: bool = True
    source_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "phase": self.phase,
            "message": self.message,
            "recoverable": self.recoverable,
            "source_kind": self.source_kind,
        }


@dataclass(frozen=True, slots=True)
class _ModelSource:
    path: Path
    kind: str
    revision: str | None = None


def _version_tuple(value: str) -> tuple[int, int, int]:
    numbers = re.findall(r"\d+", str(value))
    padded = (numbers + ["0", "0", "0"])[:3]
    return tuple(int(part) for part in padded)  # type: ignore[return-value]


def _sanitized_exception(exc: BaseException, *, limit: int = 400) -> str:
    """Bound exception text and remove common URL credential forms."""

    text = re.sub(r"(?i)(token|authorization|api[_-]?key)=([^\s&]+)", r"\1=<redacted>", str(exc))
    text = re.sub(r"https?://[^\s]+", "<url>", text)
    return f"{type(exc).__name__}: {text}"[:limit]


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    if hasattr(value, "item") and callable(value.item):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class QwenGateway:
    """One lifecycle-managed, offline-only Qwen3-8B policy proposer."""

    def __init__(self, config: RunConfig):
        self.config = config
        self.status = "not_initialized"
        self.ready = False
        self.model_source: str | None = None
        self.model_source_kind: str | None = None
        self.model_revision: str | None = None
        self.quantization: str | None = None
        self.calls = 0
        self.responses_parsed = 0
        self.generation_errors = 0
        self.parse_errors = 0
        self.load_errors: list[dict[str, Any]] = []
        self._errors: list[GatewayError] = []
        self._load_attempted = False
        self._closed = False
        self._torch: Any = None
        self._tokenizer: Any = None
        self._model: Any = None

    @staticmethod
    def _enforce_offline_environment() -> None:
        # Assignment, instead of setdefault, prevents a permissive host setting
        # from weakening the gateway's offline contract.
        for name, value in _OFFLINE_ENVIRONMENT.items():
            os.environ[name] = value

    def _record_error(
        self,
        code: GatewayErrorCode,
        phase: str,
        message: str,
        *,
        source_kind: str | None = None,
    ) -> GatewayError:
        error = GatewayError(
            code=code,
            phase=phase,
            message=str(message)[:400],
            recoverable=True,
            source_kind=source_kind,
        )
        self._errors.append(error)
        if phase in {"configuration", "discovery", "dependency", "load"}:
            self.load_errors.append(error.to_dict())
        return error

    @staticmethod
    def _manifest_model_id(path: Path) -> str | None:
        for name in ("model_manifest.json", "qwen_model_manifest.json"):
            manifest_path = path / name
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return "<invalid-manifest>"
            if not isinstance(manifest, dict):
                return "<invalid-manifest>"
            value = manifest.get("model_id")
            return str(value) if value is not None else "<missing-model-id>"
        return None

    @classmethod
    def _validate_model_directory(cls, path: Path) -> tuple[bool, str, str | None]:
        try:
            resolved = path.expanduser().resolve()
        except OSError as exc:
            return False, _sanitized_exception(exc), None
        if not resolved.is_dir():
            return False, "model path is not an existing directory", None

        config_path = resolved / "config.json"
        if not config_path.is_file():
            return False, "config.json is missing", None
        try:
            model_config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"config.json is unreadable: {_sanitized_exception(exc)}", None
        if not isinstance(model_config, dict):
            return False, "config.json must contain a JSON object", None

        mismatches = [
            f"{key}={model_config.get(key)!r} (expected {expected!r})"
            for key, expected in _EXPECTED_CONFIG.items()
            if model_config.get(key) != expected
        ]
        architectures = model_config.get("architectures")
        if not isinstance(architectures, list) or "Qwen3ForCausalLM" not in architectures:
            mismatches.append("architectures must contain Qwen3ForCausalLM")
        if mismatches:
            return False, "; ".join(mismatches), None

        manifest_model_id = cls._manifest_model_id(resolved)
        if manifest_model_id is not None and manifest_model_id != MODEL_ID:
            return False, f"manifest model_id={manifest_model_id!r}, expected {MODEL_ID!r}", None

        tokenizer_assets = (
            "tokenizer.json",
            "tokenizer.model",
            "vocab.json",
        )
        tokenizer_config_path = resolved / "tokenizer_config.json"
        if not tokenizer_config_path.is_file() or not any(
            (resolved / name).is_file() for name in tokenizer_assets
        ):
            return False, "tokenizer assets are incomplete", None
        try:
            tokenizer_config = json.loads(tokenizer_config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return False, f"tokenizer_config.json is unreadable: {_sanitized_exception(exc)}", None
        if not isinstance(tokenizer_config, dict):
            return False, "tokenizer_config.json must contain a JSON object", None
        if tokenizer_config.get("eos_token") != "<|im_end|>" or not tokenizer_config.get(
            "chat_template"
        ):
            return False, "post-trained Qwen3-8B tokenizer metadata is invalid", None

        has_weights = any(resolved.glob("*.safetensors")) or any(
            resolved.glob("pytorch_model*.bin")
        )
        if not has_weights:
            return False, "local model weights are missing", None

        revision = resolved.name if re.fullmatch(r"[0-9a-fA-F]{7,64}", resolved.name) else None
        return True, "ok", revision

    @staticmethod
    def _cache_roots() -> tuple[Path, ...]:
        roots: list[Path] = []
        explicit_hub_cache = os.getenv("HUGGINGFACE_HUB_CACHE") or os.getenv("HF_HUB_CACHE")
        if explicit_hub_cache:
            roots.append(Path(explicit_hub_cache).expanduser())
        hf_home = os.getenv("HF_HOME")
        if hf_home:
            roots.append(Path(hf_home).expanduser() / "hub")
        transformers_cache = os.getenv("TRANSFORMERS_CACHE")
        if transformers_cache:
            roots.append(Path(transformers_cache).expanduser())
        roots.append(Path.home() / ".cache" / "huggingface" / "hub")

        result: list[Path] = []
        seen: set[Path] = set()
        for root in roots:
            try:
                resolved = root.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            result.append(resolved)
        return tuple(result)

    @classmethod
    def _cached_snapshot_paths(cls) -> list[Path]:
        repo_directory_name = "models--Qwen--Qwen3-8B"
        candidates: list[Path] = []
        for cache_root in cls._cache_roots():
            repo_root = (
                cache_root
                if cache_root.name == repo_directory_name
                else cache_root / repo_directory_name
            )
            snapshots_root = repo_root / "snapshots"
            if not snapshots_root.is_dir():
                continue

            # Prefer the local refs/main snapshot when one exists, without using
            # huggingface_hub or contacting the Hub.
            ref_path = repo_root / "refs" / "main"
            if ref_path.is_file():
                try:
                    revision = ref_path.read_text(encoding="utf-8").strip()
                except OSError:
                    revision = ""
                if revision:
                    candidates.append(snapshots_root / revision)
            try:
                candidates.extend(
                    sorted(
                        (path for path in snapshots_root.iterdir() if path.is_dir()),
                        key=lambda path: path.name,
                        reverse=True,
                    )
                )
            except OSError:
                continue
        return candidates

    @classmethod
    def _kaggle_attached_paths(cls) -> list[Path]:
        kaggle_input = Path("/kaggle/input")
        if not kaggle_input.is_dir():
            return []
        try:
            config_paths = list(kaggle_input.rglob("config.json"))
        except OSError:
            return []
        # The exact config validator is authoritative.  Name hints only make
        # deterministic selection prefer an explicitly named Qwen3-8B asset.
        return [
            path.parent
            for path in sorted(
                config_paths,
                key=lambda path: (
                    0 if "qwen3" in str(path).lower() and "8b" in str(path).lower() else 1,
                    len(path.parts),
                    str(path),
                ),
            )
        ]

    def _discover_source(self) -> _ModelSource | None:
        if self.config.model_id != MODEL_ID:
            self._record_error(
                GatewayErrorCode.MODEL_ID_MISMATCH,
                "configuration",
                f"configured model_id={self.config.model_id!r}; only {MODEL_ID!r} is allowed",
            )
            return None

        if self.config.model_path is not None:
            try:
                explicit = self.config.model_path.expanduser().resolve()
            except OSError as exc:
                self._record_error(
                    GatewayErrorCode.ASSET_INVALID,
                    "discovery",
                    _sanitized_exception(exc),
                    source_kind="explicit_path",
                )
                return None
            try:
                valid, detail, revision = self._validate_model_directory(explicit)
            except Exception as exc:
                valid, detail, revision = (
                    False,
                    f"model asset validation failed: {_sanitized_exception(exc)}",
                    None,
                )
            if not valid:
                self._record_error(
                    GatewayErrorCode.ASSET_INVALID,
                    "discovery",
                    detail,
                    source_kind="explicit_path",
                )
                return None
            return _ModelSource(explicit, "explicit_path", revision)

        seen: set[Path] = set()
        for kind, paths in (
            ("kaggle_attached", self._kaggle_attached_paths()),
            ("huggingface_cache", self._cached_snapshot_paths()),
        ):
            for candidate in paths:
                try:
                    resolved = candidate.expanduser().resolve()
                except OSError:
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    valid, _, revision = self._validate_model_directory(resolved)
                except Exception:
                    continue
                if valid:
                    return _ModelSource(resolved, kind, revision)

        self._record_error(
            GatewayErrorCode.ASSET_NOT_FOUND,
            "discovery",
            "no valid local Qwen3-8B directory was found in an explicit path, "
            "Kaggle attachments, or the existing Hugging Face cache",
        )
        return None

    def _release_resources(self) -> None:
        self._model = None
        self._tokenizer = None
        gc.collect()
        if self._torch is not None:
            try:
                if self._torch.cuda.is_available():
                    self._torch.cuda.empty_cache()
            except Exception:
                pass

    def _is_cuda_oom(self, exc: BaseException) -> bool:
        if "out of memory" in str(exc).lower():
            return True
        if self._torch is None:
            return False
        oom_type = getattr(self._torch.cuda, "OutOfMemoryError", None)
        return isinstance(oom_type, type) and isinstance(exc, oom_type)

    def load(self) -> "QwenGateway":
        """Load one local NF4 model, or enter a recoverable fallback state."""

        if self._closed:
            self.status = "closed"
            return self
        if self._load_attempted:
            return self
        self._load_attempted = True
        self._enforce_offline_environment()

        if not self.config.enable_llm:
            self.status = "disabled_deterministic_fallback"
            self._record_error(GatewayErrorCode.DISABLED, "configuration", "LLM is disabled")
            return self

        source = self._discover_source()
        if source is None:
            self.status = "model_asset_fallback"
            return self

        try:
            import accelerate  # noqa: F401  # lazy, required by device_map/quantized loading
            import bitsandbytes  # noqa: F401  # lazy, required for NF4
            import torch
            import transformers
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        except Exception as exc:
            self.status = "dependency_fallback"
            self._record_error(
                GatewayErrorCode.DEPENDENCY_MISSING
                if isinstance(exc, ImportError)
                else GatewayErrorCode.DEPENDENCY_INCOMPATIBLE,
                "dependency",
                _sanitized_exception(exc),
                source_kind=source.kind,
            )
            return self

        try:
            self._torch = torch
            if _version_tuple(transformers.__version__) < (4, 51, 0):
                self.status = "dependency_fallback"
                self._record_error(
                    GatewayErrorCode.DEPENDENCY_INCOMPATIBLE,
                    "dependency",
                    f"transformers>={4.51} is required; found {transformers.__version__}",
                    source_kind=source.kind,
                )
                return self
            if not torch.cuda.is_available():
                self.status = "cuda_fallback"
                self._record_error(
                    GatewayErrorCode.CUDA_UNAVAILABLE,
                    "dependency",
                    "CUDA GPU is unavailable; CPU model loading is intentionally disabled",
                    source_kind=source.kind,
                )
                return self
            compute_dtype = (
                torch.bfloat16
                if hasattr(torch.cuda, "is_bf16_supported")
                and torch.cuda.is_bf16_supported()
                else torch.float16
            )
        except Exception as exc:
            self.status = "dependency_fallback"
            self._record_error(
                GatewayErrorCode.DEPENDENCY_INCOMPATIBLE,
                "dependency",
                _sanitized_exception(exc),
                source_kind=source.kind,
            )
            return self
        try:
            quantization_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=compute_dtype,
            )
        except Exception as exc:
            self.status = "quantization_fallback"
            self._record_error(
                GatewayErrorCode.QUANTIZATION_UNAVAILABLE,
                "load",
                _sanitized_exception(exc),
                source_kind=source.kind,
            )
            return self

        try:
            tokenizer = AutoTokenizer.from_pretrained(
                str(source.path),
                local_files_only=True,
                trust_remote_code=False,
            )
            if tokenizer.pad_token_id is None:
                tokenizer.pad_token_id = tokenizer.eos_token_id
            model = AutoModelForCausalLM.from_pretrained(
                str(source.path),
                local_files_only=True,
                trust_remote_code=False,
                device_map={"": 0},
                quantization_config=quantization_config,
                low_cpu_mem_usage=True,
                attn_implementation="sdpa",
            )
            model.eval()
        except Exception as exc:
            self._release_resources()
            if self._is_cuda_oom(exc):
                code = GatewayErrorCode.LOAD_OOM
                self.status = "load_oom_fallback"
            else:
                code = GatewayErrorCode.LOAD_FAILED
                self.status = "model_load_fallback"
            self._record_error(
                code,
                "load",
                _sanitized_exception(exc),
                source_kind=source.kind,
            )
            return self

        self._tokenizer = tokenizer
        self._model = model
        self.model_source = str(source.path)
        self.model_source_kind = source.kind
        self.model_revision = source.revision
        self.quantization = "bitsandbytes_nf4_4bit"
        self.status = "ready"
        self.ready = True
        return self

    @staticmethod
    def _input_device(model: Any) -> Any:
        try:
            return model.get_input_embeddings().weight.device
        except (AttributeError, RuntimeError):
            return next(model.parameters()).device

    @staticmethod
    def _policy_prompt() -> str:
        return """You are the Policy Agent for EC_POLICY_V1. Use only the JSON handoffs.
Apply this exact mapping in strict priority:
1. canceled+paid -> canceled_order_paid | ORDER_CANCELED_AFTER_PAYMENT | platform | OLIST_PLATFORM
2. unavailable+paid -> unavailable_order_paid | ORDER_UNAVAILABLE_AFTER_PAYMENT | platform | OLIST_PLATFORM
3. delivery late + seller handoff late -> late_delivery_seller | SELLER_HANDOFF_AFTER_LIMIT | seller | violating seller ID
4. delivery late + seller handoff on time -> late_delivery_logistics | CARRIER_DELIVERED_AFTER_ESTIMATE | logistics_provider | LOGISTICS_PROVIDER
5. at least two payments + reconciled -> valid_split_payment | MULTIPLE_PAYMENTS_RECONCILED | null | null
6. delivery within estimate + reconciled -> unsupported_late_claim | DELIVERY_WITHIN_ESTIMATE | null | null
Return exactly one compact JSON object with keys primary_issue, cause_code, party_type, party_id.
Use the exact tokens above. Never invent IDs or money."""

    def _failure_metadata(
        self,
        error: GatewayError | None,
        *,
        latency_ms: float = 0.0,
    ) -> dict[str, Any]:
        reason = error.code.value if error is not None else self.status
        return {
            "ok": False,
            "reason": reason,
            "error": error.to_dict() if error is not None else None,
            "latency_ms": latency_ms,
            "backend": "deterministic_fallback",
            "gateway_status": self.status,
        }

    def propose_policy(
        self, handoffs: Mapping[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Ask local Qwen for policy fields, returning typed fallback metadata on failure."""

        if not self.ready or self._model is None or self._tokenizer is None:
            if self._closed:
                error = GatewayError(
                    GatewayErrorCode.CLOSED,
                    "inference",
                    "gateway is closed",
                    recoverable=True,
                    source_kind=self.model_source_kind,
                )
            elif self._errors:
                error = self._errors[-1]
            else:
                error = GatewayError(
                    GatewayErrorCode.NOT_READY,
                    "inference",
                    "load() must complete before inference",
                    recoverable=True,
                    source_kind=self.model_source_kind,
                )
            return None, self._failure_metadata(error)

        messages = [
            {"role": "system", "content": self._policy_prompt()},
            {
                "role": "user",
                "content": json.dumps(
                    handoffs,
                    ensure_ascii=False,
                    allow_nan=False,
                    default=_json_default,
                ),
            },
        ]
        started = time.perf_counter()
        self.calls += 1
        try:
            try:
                prompt = self._tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                    enable_thinking=False,
                )
            except TypeError:
                messages[0]["content"] += " /no_think"
                prompt = self._tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )

            tokenized = self._tokenizer(prompt, return_tensors="pt")
            input_length = int(tokenized["input_ids"].shape[-1])
            if input_length > _MAX_INPUT_TOKENS:
                error = self._record_error(
                    GatewayErrorCode.PROMPT_TOO_LARGE,
                    "inference",
                    f"prompt contains {input_length} tokens; limit is {_MAX_INPUT_TOKENS}",
                    source_kind=self.model_source_kind,
                )
                return None, self._failure_metadata(
                    error,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                )

            input_device = self._input_device(self._model)
            tokenized = {key: value.to(input_device) for key, value in tokenized.items()}
            with self._torch.inference_mode():
                generated = self._model.generate(
                    **tokenized,
                    max_new_tokens=self.config.max_new_tokens,
                    do_sample=False,
                    use_cache=True,
                    eos_token_id=self._tokenizer.eos_token_id,
                    pad_token_id=self._tokenizer.pad_token_id,
                )
            new_tokens = generated[0][input_length:]
            raw_text = self._tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
            candidate = parse_first_json_object(raw_text)
            self.responses_parsed += 1
            return candidate, {
                "ok": True,
                "reason": None,
                "error": None,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "backend": "qwen_local_nf4",
                "gateway_status": self.status,
                "model_source_kind": self.model_source_kind,
                "input_tokens": input_length,
                "output_tokens": int(new_tokens.shape[-1]),
                "raw_response": raw_text[:500],
            }
        except ModelOutputParseError as exc:
            self.parse_errors += 1
            error = self._record_error(
                GatewayErrorCode.OUTPUT_PARSE_FAILED,
                "inference",
                _sanitized_exception(exc),
                source_kind=self.model_source_kind,
            )
            return None, self._failure_metadata(
                error,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except Exception as exc:
            self.generation_errors += 1
            if self._is_cuda_oom(exc):
                code = GatewayErrorCode.INFERENCE_OOM
                self.status = "generation_oom_fallback"
                self.ready = False
                self._release_resources()
            else:
                code = GatewayErrorCode.INFERENCE_FAILED
            error = self._record_error(
                code,
                "inference",
                _sanitized_exception(exc),
                source_kind=self.model_source_kind,
            )
            return None, self._failure_metadata(
                error,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )

    def snapshot(self) -> dict[str, Any]:
        """Return JSON-safe runtime metadata without importing model dependencies."""

        error_counts = Counter(error.code.value for error in self._errors)
        return {
            "requested": self.config.enable_llm,
            "configured_model": self.config.model_id,
            "effective_model": self.config.model_id if self.ready else None,
            "status": self.status,
            "ready": self.ready,
            "offline_enforced": True,
            "network_permitted": False,
            "source": self.model_source,
            "source_kind": self.model_source_kind,
            "revision": self.model_revision,
            "quantization": self.quantization,
            "generation": {
                "enable_thinking": False,
                "do_sample": False,
                "max_input_tokens": _MAX_INPUT_TOKENS,
                "max_new_tokens": self.config.max_new_tokens,
            },
            "calls": self.calls,
            "parsed_responses": self.responses_parsed,
            "parse_errors": self.parse_errors,
            "generation_errors": self.generation_errors,
            "error_counts": dict(error_counts),
            "errors": [error.to_dict() for error in self._errors],
        }

    def close(self) -> None:
        """Release model resources. Calling close repeatedly is safe."""

        if self._closed:
            return
        self.ready = False
        self._release_resources()
        self._closed = True
        self.status = "closed"


__all__ = ["GatewayError", "GatewayErrorCode", "QwenGateway"]
