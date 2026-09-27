"""The runner's state directory, shared with the console.

    <state>/KILL                     present = kill switch on (the console toggles it; the runner obeys)
    <state>/events.jsonl             runner events, append-only (the console inbox reads it)
    <state>/cases/<id>/control.json  written by the console: {"paused": bool, "start_requested": {...} | null}
    <state>/cases/<id>/status.json   written by the runner: state, run_id, checkpoint, times, reason, spend
    <state>/cases/<id>/lock          flock held while an engine child runs for the case
    <state>/cases/<id>/engine-<run>.log   the child's output (0600; never shown in full)
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import yaml

STATES = ("idle", "running", "waiting_approval", "paused", "killed", "budget_stop", "failed", "done")
EVENT_KINDS = ("started", "resumed", "paused_at_checkpoint", "done", "failed", "budget_stop", "killed",
               "start_requested", "paused", "unpaused")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_json_atomic(path: Path, data: dict, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def append_line(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(record, ensure_ascii=False) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


class State:
    def __init__(self, root: Path):
        self.root = Path(root)

    def case_dir(self, case_id: str) -> Path:
        return self.root / "cases" / case_id

    @property
    def killed(self) -> bool:
        return (self.root / "KILL").exists()

    def control(self, case_id: str) -> dict:
        return read_json(self.case_dir(case_id) / "control.json")

    def status(self, case_id: str) -> dict:
        return read_json(self.case_dir(case_id) / "status.json")

    def set_status(self, case_id: str, **fields) -> dict:
        cur = self.status(case_id)
        cur.update(fields, updated_at=now())
        write_json_atomic(self.case_dir(case_id) / "status.json", cur)
        return cur

    def event(self, case_id: str | None, kind: str, detail: str = "", **extra) -> None:
        append_line(self.root / "events.jsonl", {"ts": now(), "case_id": case_id, "kind": kind,
                                                  "detail": detail[:2000], **extra})


def _front_matter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    try:
        data = yaml.safe_load(text.split("---", 2)[1]) or {}
    except (yaml.YAMLError, IndexError):
        return {}
    return data if isinstance(data, dict) else {}


def checkpoint_dirs(case_dir: Path, checkpoint: str) -> list[Path]:
    """Phase dirs whose APPROVAL_PENDING.md is for `checkpoint` (archived drafts under revisions/ excluded)."""
    out = []
    for pending in sorted(Path(case_dir).glob("**/APPROVAL_PENDING.md")):
        if "revisions" in pending.relative_to(case_dir).parts:
            continue
        try:
            fm = _front_matter(pending.read_text(errors="replace"))
        except OSError:
            continue
        if fm.get("checkpoint") == checkpoint:
            out.append(pending.parent)
    return out


def decision_for(case_dir: Path, checkpoint: str) -> str | None:
    """sha256 of the APPROVED marker answering `checkpoint`, or None if the checkpoint is not decided yet."""
    for d in checkpoint_dirs(case_dir, checkpoint):
        marker = d / "APPROVED"
        if marker.is_file():
            return hashlib.sha256(marker.read_bytes()).hexdigest()
    return None
