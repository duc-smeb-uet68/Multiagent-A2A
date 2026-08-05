"""Small, dependency-free helpers for parsing structured model output."""

from __future__ import annotations

import json
import re
from typing import Any


class ModelOutputParseError(ValueError):
    """Raised when a model response does not contain a JSON object."""


def parse_first_json_object(text: str) -> dict[str, Any]:
    """Return the first JSON object embedded in a model response.

    Qwen3 normally runs with thinking disabled in this project.  The defensive
    ``<think>`` and Markdown-fence removal is retained because cached tokenizer
    templates and model revisions can still produce wrappers around the answer.
    A ``JSONDecoder`` is used instead of a greedy regular expression so braces
    inside JSON strings and trailing prose are handled correctly.
    """

    cleaned = re.sub(
        r"<think>.*?</think>",
        "",
        str(text),
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = re.sub(r"```(?:json)?", "", cleaned, flags=re.IGNORECASE)
    decoder = json.JSONDecoder()
    for index, character in enumerate(cleaned):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ModelOutputParseError("No JSON object found in model response")


__all__ = ["ModelOutputParseError", "parse_first_json_object"]
