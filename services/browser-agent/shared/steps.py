"""§12 trace-step records the service emits (CONTRACT §4 trace-step + §12 additions). One JSONL line per step."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")



def new_step_id() -> str:
    """A step id allocated before the step is written, so the model calls it makes carry it (X-BA-Step-Id)."""
    return f"step:{uuid.uuid4().hex}"

class StepLog:
    """Append-only JSONL sink for one session's trace steps (the engine collects them into trace.live.jsonl)."""

    def __init__(self, path: Path, run_id: str, session_id: str, source_id: str | None = None,
                 objective_id: str | None = None, tdd_path: str | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.base = {"run_id": run_id, "phase": 5, "source_id": source_id, "objective_id": objective_id,
                     "tdd_path": tdd_path}
        self.session_id = session_id
        self._lock = threading.Lock()

    def emit(self, *, mode: str, observed, requested, executed, evaluated, parent_step_id: str | None = None,
             value_ids: list[str] | None = None, event: str | None = None, generated_by: dict | None = None,
             step_id: str | None = None, **extra) -> dict:
        """Write one step. `extra` carries §12 objects: verify / repair / gate, and screenshot_key."""
        step = {"step_id": step_id or new_step_id(), **self.base, "mode": mode, "observed": observed,
                "requested": requested, "executed": executed, "evaluated": evaluated,
                "parent_step_id": parent_step_id, "value_ids": value_ids or [], "ts": now(),
                "event": event, "session_id": self.session_id,
                **({"generated_by": generated_by} if generated_by else {}),
                **{k: v for k, v in extra.items() if v is not None}}
        with self._lock, self.path.open("a") as fh:
            fh.write(json.dumps(step, ensure_ascii=False) + "\n")
        return step
