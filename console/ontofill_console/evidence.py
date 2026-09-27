"""/evidence: the track checklist with the proof we already have on disk, per requirement.

Static by design: it reads only files the project already produces and never calls an API or probes anything.
- the track checklist (`track-checklist.md` in ONTOFILL_CONSOLE_EVIDENCE_DIR, else docs/reference/ in the repo);
- a saved remote verification output (`verify-remote.txt` in ONTOFILL_CONSOLE_EVIDENCE_DIR);
- the sandbox proof checkpoints in the latest run's jobs.jsonl (through the gold store);
- the spend tracker's history (ONTOFILL_CONSOLE_SPEND_HISTORY), shown as totals only.
A requirement those files cannot prove links to the screen that shows it, and says so.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import live

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKLIST_DOC = "docs/reference/track-blast-radius-zero.md"
VERIFY_FILE = "verify-remote.txt"
LIVE_VIEW_FILE = "live-view-check.txt"  # saved outside-in check of a per-cell live view (netbird expose)
LINE_RE = re.compile(r"^\s*(PASS|FAIL|WARN)\s+(.*)$")


def evidence_dir() -> Path | None:
    raw = os.environ.get("ONTOFILL_CONSOLE_EVIDENCE_DIR")
    return Path(raw) if raw else None


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="minutes")


def checklist_rows(text: str) -> list[dict]:
    """Rows of the "Requirement → our answer → status" table (the doc's own status column is not shown: it is a
    planning note, and this page computes status from proof instead)."""
    rows, in_table = [], False
    for line in text.splitlines():
        if line.startswith("| Requirement"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2 or set(cells[0]) <= {"-", " ", ":"}:
                continue
            rows.append({"requirement": cells[0].replace("**", "").replace("`", ""),
                         "answer": cells[1].replace("**", "").replace("`", "")})
    return rows


def load_checklist() -> tuple[list[dict], str | None]:
    for path in ([evidence_dir() / "track-checklist.md"] if evidence_dir() else []) + [REPO_ROOT / CHECKLIST_DOC]:
        if path.is_file():
            return checklist_rows(path.read_text()), str(path.name)
    return [], None


@dataclass
class VerifyResult:
    source: str | None = None
    saved_at: str | None = None
    result: str | None = None  # PASS | FAIL
    lines: list[tuple[str, str]] = field(default_factory=list)

    def matching(self, *words: str) -> list[tuple[str, str]]:
        return [(k, t) for k, t in self.lines if any(w in t for w in words)]

    @property
    def ports(self) -> list[tuple[str, str]]:
        return self.matching("VM port")

    @property
    def auth(self) -> list[tuple[str, str]]:
        return self.matching("unauthenticated")


def load_verify(name: str = VERIFY_FILE) -> VerifyResult:
    base = evidence_dir()
    path = base / name if base else None
    if not path or not path.is_file():
        return VerifyResult()
    out = VerifyResult(source=name, saved_at=_mtime(path))
    for raw in path.read_text(errors="replace").splitlines():
        m = LINE_RE.match(raw)
        if m:
            out.lines.append((m.group(1), m.group(2).strip()))
        elif raw.startswith("RESULT:"):
            out.result = raw.split(":", 1)[1].strip().split()[0] if raw.split(":", 1)[1].strip() else None
    return out


def load_spend() -> dict | None:
    raw = os.environ.get("ONTOFILL_CONSOLE_SPEND_HISTORY")
    path = Path(raw) if raw else None
    if path is None:
        return None
    if not path.is_file():
        return None
    latest = None
    for raw in path.read_text().splitlines():
        try:
            latest = json.loads(raw)
        except ValueError:
            continue
    if not isinstance(latest, dict):
        return None
    totals = {k: latest.get(k) for k in ("ts", "credit_total", "credit_used", "credit_remaining", "pending_charges",
                                           "resource_rate_usd_per_hour") if isinstance(latest.get(k), (int, float, str))}
    return totals or None


def job_proof(cases) -> dict | None:
    """Checkpoint pass counts over the sandbox jobs of the first registered case whose latest run has jobs (the
    same data as that run's Sandbox proof panel). `cases` is an iterable of (case_id, store)."""
    for case_id, store in cases:
        try:
            run_id = store.live_run_id()
        except (LookupError, AttributeError, OSError, ValueError):
            run_id = None
        jobs = store.live_jobs(run_id) if run_id else []
        if jobs:
            return _job_proof(case_id, store, run_id, jobs)
    return None


def _job_proof(case_id: str, store, run_id: str, jobs: list[dict]) -> dict:
    p = live.proof(jobs, live.annotate(store.live_steps(run_id)))
    runtimes = sorted({((j.get("checkpoints") or {}).get("host") or {}).get("runtime") or "unknown" for j in jobs})
    return {"case_id": case_id, "run_id": run_id, "jobs": p["jobs"], "counts": p["counts"], "labels": {k: lbl for k, lbl, _ in p["checkpoints"]},
            "with_limits": p["with_limits"], "killed_total": p["killed_total"], "runtimes": runtimes, "tier": p["tier"]}


# Requirement keyword → which proof speaks to it (checked in order; first match wins).
RULES = [
    ("process isolation", "jobs:host,isolation"),
    ("secret hygiene", "jobs:secrets"),
    ("resource limits", "jobs:limits"),
    ("lifecycle", "jobs:teardown"),
    ("sandboxes never in the app", "jobs:where,isolation"),
    ("vm-based backend", "verify:ports"),
    ("public demo url", "verify:auth"),
    ("public web app", "verify:auth"),
    ("agent llm calls", "link:/"),
    ("central control", "link:/"),
    ("vision model", "link:/"),
    ("human approves", "link:/"),
    ("rest/websocket", "link:/"),
    ("dashboards", "link:/"),
    ("containment", "jobs:isolation,limits"),
]


def _jobs_status(jp: dict | None, keys: list[str]) -> tuple[str, list[str]]:
    if not jp:
        return "no proof yet", ["No sandbox jobs in the latest run feed."]
    notes, ok = [], True
    for key in keys:
        if key == "limits":
            notes.append(f"{jp['with_limits']} of {jp['jobs']} jobs recorded their caps; {jp['killed_total']} stopped by a cap")
            ok = ok and jp["with_limits"] == jp["jobs"]
            continue
        c = jp["counts"].get(key) or {}
        notes.append(f"{jp['labels'].get(key, key)}: {c.get('pass', 0)} passed, {c.get('fail', 0)} failed, "
                     f"{c.get('pending', 0)} pending")
        ok = ok and c.get("fail", 0) == 0 and c.get("pass", 0) > 0
    if "host" in keys:
        notes.append("runtimes seen: " + ", ".join(jp["runtimes"]))
        if any(r != "runsc" for r in jp["runtimes"]):  # process isolation means gVisor; runc is only a container
            ok = False
            notes.append("not all jobs ran under gVisor runsc")
    return ("proven" if ok else "partial"), notes


def rows(cases) -> dict:
    checklist, checklist_src = load_checklist()
    verify = load_verify()
    jp = job_proof(cases)
    out = []
    for row in checklist:
        key = row["requirement"].lower()
        rule = next((r for k, r in RULES if k in key), None)
        status, notes, link = "see screen", [], None
        if rule and rule.startswith("jobs:"):
            status, notes = _jobs_status(jp, rule[5:].split(","))
            link = f"/cases/{jp['case_id']}/runs/{jp['run_id']}#proof-h" if jp else None
        elif rule and rule.startswith("verify:"):
            lines = verify.ports if rule.endswith("ports") else verify.auth
            if not verify.source:
                status, notes = "no proof yet", ["No saved verify.sh remote output."]
            else:
                fails = [t for k, t in lines if k == "FAIL"]
                status = "proven" if lines and not fails else ("failed" if fails else "no proof yet")
                passed = sum(1 for k, _ in lines if k == "PASS")
                summary = f"{passed} checks passed, {len(fails)} failed (verify.sh remote, saved {verify.saved_at})"
                notes = [summary, *fails[:3]]
        elif rule and rule.startswith("link:"):
            link = rule[5:]
        out.append({**row, "status": status, "notes": notes, "link": link})
    return {"rows": out, "checklist_src": checklist_src, "verify": verify, "jobs": jp, "spend": load_spend(),
            "live_view": load_verify(LIVE_VIEW_FILE)}
