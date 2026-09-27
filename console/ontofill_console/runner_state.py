"""The console's view of (and control over) the ontofill-runner service, through their shared state directory.

The runner owns `cases/<id>/status.json` and `events.jsonl`; the console owns `cases/<id>/control.json`, the global
`KILL` file and `console-decisions.jsonl`. Every operator action also lands in the case's `decisions.jsonl`.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_STATE = Path("/var/lib/ontofill-runner")
ACTIONS = ("start", "pause", "resume")
_LOCK = threading.Lock()


def state_dir(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("ONTOFILL_RUNNER_STATE") or DEFAULT_STATE)


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def append_line(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(record, ensure_ascii=False) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def killed(root: Path) -> bool:
    return (root / "KILL").exists()


def status(root: Path, case_id: str) -> dict:
    return _read(root / "cases" / case_id / "status.json")


def control(root: Path, case_id: str) -> dict:
    return _read(root / "cases" / case_id / "control.json")


def apply_action(root: Path, case_id: str, action: str, by: str, to_phase: int | None = None) -> dict:
    """start → a new run is requested; pause → the runner stops resuming; resume → clears pause and re-arms one
    relaunch after a failure. Returns the new control.json."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    path = root / "cases" / case_id / "control.json"
    with _LOCK:
        ctl = _read(path)
        at = now_iso()
        if action == "start":
            ctl["start_requested"] = {"by": by, "at": at, "to_phase": to_phase}
        elif action == "pause":
            ctl["paused"] = True
        else:
            ctl["paused"] = False
            ctl["retry_at"] = at
        ctl["updated_at"] = at
        _write_atomic(path, ctl)
    return ctl


def set_kill(root: Path, on: bool, record: dict) -> None:
    with _LOCK:
        marker = root / "KILL"
        if on:
            root.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps(record, ensure_ascii=False))
        else:
            marker.unlink(missing_ok=True)
        append_line(root / "console-decisions.jsonl", record)


def _hhmm(ts: str | None) -> str | None:
    try:
        return datetime.fromisoformat(ts).astimezone(UTC).strftime("%H:%M UTC") if ts else None
    except ValueError:
        return None


def line(root: Path, case_id: str) -> dict:
    """The one-line runner state for a case page: {state, text, run_id, reason, paused, kill}."""
    st = status(root, case_id)
    ctl = control(root, case_id)
    kill = killed(root)
    state = st.get("state")
    if kill:
        text = "Runner off (kill switch)"
    elif ctl.get("paused"):
        text = "Runner paused by an operator"
    elif state == "running":
        since = _hhmm(st.get("last_resumed_at") or st.get("running_since"))
        text = (
            (
                f"Resumed automatically at {since}"
                if st.get("last_resumed_at") and st.get("last_resumed_at") == st.get("running_since")
                else f"Running since {since}"
            )
            if since
            else "Running"
        )
    elif state == "waiting_approval":
        text = f"Waiting for the {st.get('checkpoint') or 'checkpoint'} decision; resumes by itself after it"
        if st.get("last_resumed_at"):
            text += f" (last resumed automatically at {_hhmm(st['last_resumed_at'])})"
    elif state == "budget_stop":
        text = "Stopped: budget"
    elif state == "failed":
        text = "Stopped: the last run failed (see the inbox); Resume retries once"
    elif state == "done":
        text = "Done"
    elif state:
        text = state.replace("_", " ").capitalize()
    else:
        text = "Runner has not seen this case yet"
    return {
        "state": state,
        "text": text,
        "run_id": st.get("run_id"),
        "reason": st.get("reason"),
        "paused": bool(ctl.get("paused")),
        "kill": kill,
        "available": root.is_dir(),
    }


def events(root: Path, limit: int = 200) -> list[dict]:
    try:
        lines = (root / "events.jsonl").read_text().splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for raw in lines:
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


INBOX_KINDS = {
    "failed": ("block", "Runner: the run failed"),
    "budget_stop": ("block", "Runner stopped: budget reached"),
    "killed": ("pause", "Runner stopped by the kill switch"),
}


def inbox_items(root: Path, case_ids: set[str]) -> list[dict]:
    """Runner events that need a person, as inbox strips (the latest per case and kind)."""
    latest: dict[tuple, dict] = {}
    for ev in events(root):
        if ev.get("kind") in INBOX_KINDS and ev.get("case_id") in case_ids:
            latest[(ev["case_id"], ev["kind"])] = ev
    items = []
    for (cid, kind), ev in latest.items():
        st, title = INBOX_KINDS[kind]
        detail = (ev.get("detail") or "").splitlines()
        items.append(
            {
                "state": st,
                "case_id": cid,
                "kind": f"runner_{kind}",
                "title": title,
                "detail": " · ".join(x for x in (ev.get("run_id"), detail[0] if detail else None) if x),
                "when": None,
                "since": ev.get("ts"),
                "href": f"/cases/{cid}",
            }
        )
    return items
