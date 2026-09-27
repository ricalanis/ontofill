"""Content-addressed capture store for screenshots, laid out like the lake's bronze layer:
<root>/bronze/sha256/<hex> plus <hex>.meta.json (bronze-sidecar shape). Keys are `sha256:<hex>`."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Protocol

from shared.steps import now


class CaptureStore(Protocol):
    def put(self, data: bytes, *, content_type: str, url: str, source_id: str, step_id: str) -> str: ...

    def link(self, key: str, step_id: str) -> None: ...

    def get(self, key: str) -> bytes: ...


class LocalCaptureStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        hexdigest = key.removeprefix("sha256:")
        return self.root / "bronze" / "sha256" / hexdigest

    def put(self, data: bytes, *, content_type: str, url: str, source_id: str, step_id: str) -> str:
        key = "sha256:" + hashlib.sha256(data).hexdigest()
        path = self._path(key)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():  # immutable: the first write wins
                path.write_bytes(data)
                meta = {
                    "content_type": content_type,
                    "url": url,
                    "captured_at": now(),
                    "source_id": source_id,
                    "step_id": step_id,
                }
                path.with_name(path.name + ".meta.json").write_text(json.dumps(meta))
        return key

    def link(self, key: str, step_id: str) -> None:
        """Replace a provisional step id in the sidecar with the id of the step that first cites the capture."""
        meta_path = self._path(key).with_name(self._path(key).name + ".meta.json")
        with self._lock:
            if not meta_path.exists():
                return
            meta = json.loads(meta_path.read_text())
            if str(meta.get("step_id", "")).startswith("pending:"):
                meta["step_id"] = step_id
                meta_path.write_text(json.dumps(meta))

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()
