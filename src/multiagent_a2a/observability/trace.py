from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Sequence
import uuid

from ..artifacts.json_io import atomic_write_text, json_safe


class TraceRecorder:
    """In-memory structured trace that atomically overwrites the latest run."""

    def __init__(self) -> None:
        self.run_id = uuid.uuid4().hex
        self.started_at = datetime.now(timezone.utc)
        self.events: list[dict[str, Any]] = []

    @property
    def event_count(self) -> int:
        return len(self.events)

    def emit(
        self,
        case_id: str | None,
        agent: str,
        event: str,
        payload: Any | None = None,
        *,
        from_agent: str | None = None,
        to_agent: str | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "run_id": self.run_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "case_id": case_id,
            "agent": agent,
            "event": event,
        }
        if from_agent is not None:
            record["from_agent"] = from_agent
        if to_agent is not None:
            record["to_agent"] = to_agent
        if payload is not None:
            record["payload"] = json_safe(payload)
        self.events.append(record)

    def flush(self, paths: Sequence[Path]) -> None:
        content = "".join(
            json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"
            for event in self.events
        )
        unique_paths = dict.fromkeys(Path(item) for item in paths)
        for path in unique_paths:
            atomic_write_text(path, content)

