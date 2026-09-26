"""JSONL call log: one line per upstream call. Never the key, never the prompt text (lengths and flags only)."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path


class CallLog:
    FIELDS = ("ts", "session_id", "run_id", "step_id", "upstream", "purpose", "model", "status", "input_tokens",
              "output_tokens", "est_usd", "est_tokens", "gate", "latency_ms", "prompt_chars")

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, **record) -> dict:
        rec = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds")}
        rec.update({k: record.get(k) for k in self.FIELDS if k != "ts" and record.get(k) is not None})
        with self._lock, self.path.open("a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def read(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return [json.loads(x) for x in self.path.read_text().splitlines() if x.strip()]
