"""Case packages and their approval checkpoints (CONTRACT §5, v0.9.5 deny with reason, v0.9.7 hardening).

A decision is only accepted when it names the exact artifact bytes the approver saw (`artifact_sha256`), comes from
an authenticated identity (the SSO proxy's header in deployed mode), and is recorded twice, together or not at all:
the `APPROVED` marker the engine reads, and one line in the case's append-only `decisions.jsonl`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

PHASE_NAMES = {1: "Scope", 2: "Ontology", 3: "Fan out", 4: "Local scoping", 5: "Execute"}


class CaseDir:
    """Read-only, path-safe access to a case package directory."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def path(self, rel: str) -> Path | None:
        p = (self.root / rel).resolve()
        return p if p.is_relative_to(self.root) else None

    def read(self, rel: str | None, limit: int = 6000) -> str | None:
        p = self.path(rel) if rel else None
        if not p or not p.is_file():
            return None
        text = p.read_text(errors="replace")
        return text if len(text) <= limit else text[:limit] + "\n…"

    def exists(self, rel: str) -> bool:
        p = self.path(rel)
        return bool(p and p.exists())

    def latest(self, pattern: str) -> str | None:
        found = sorted(self.root.glob(pattern))
        return str(found[-1].relative_to(self.root)) if found else None


CHECKPOINTS = ("prd", "factors", "ontology", "action")
DENIABLE = ("prd", "factors", "ontology", "action")  # CONTRACT v0.9.5 (phase checkpoints) and §12 (actions)
DENY_REASON_MAX = 2000


RISK_TIERS = ("SAFE", "LOW", "HIGH")
_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class Approval:
    phase_dir: str  # relative to the case dir, e.g. "01-scope"
    pending_text: str
    meta: dict
    approved: dict | None

    @property
    def checkpoint(self) -> str | None:
        cp = self.meta.get("checkpoint")
        if cp in CHECKPOINTS:
            return cp
        return {"01-scope": "prd", "02-ontology": "ontology"}.get(self.phase_dir)

    @property
    def decision(self) -> str | None:
        """'approve' or 'deny' once answered (a missing decision on an answered checkpoint means approve)."""
        if not self.approved:
            return None
        return "deny" if self.approved.get("decision") == "deny" else "approve"

    @property
    def risk_tier(self) -> str | None:
        tier = str(self.meta.get("risk_tier") or "").upper()
        return tier if tier in RISK_TIERS else None


def _front_matter(text: str) -> dict:
    """Structured metadata inside APPROVAL_PENDING.md: YAML front matter or a ```json block."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            try:
                data = yaml.safe_load(text[3:end])
                if isinstance(data, dict):
                    return data
            except yaml.YAMLError:
                pass
    m = _JSON_BLOCK.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except ValueError:
            pass
    return {}


def approvals(case: CaseDir) -> list[Approval]:
    out = []
    if not case.root.is_dir():
        return out
    for pending in sorted(case.root.glob("*/APPROVAL_PENDING.md")) + sorted(case.root.glob("*/*/APPROVAL_PENDING.md")):
        rel_dir = str(pending.parent.relative_to(case.root))
        if "revisions" in pending.parent.relative_to(case.root).parts:
            continue  # an archived draft's request (v0.9.5), not a live checkpoint
        text = pending.read_text(errors="replace")
        approved_path = pending.parent / "APPROVED"
        approved = None
        if approved_path.is_file():
            try:
                approved = json.loads(approved_path.read_text())
            except ValueError:
                approved = {"approver": approved_path.read_text().strip()[:200], "date": None}
        out.append(Approval(rel_dir, text, _front_matter(text), approved))
    return out


DEFAULT_ARTIFACTS = {"prd": ["01-scope/prd.json"], "factors": ["02-ontology/factors/factors.json"],
                     "ontology": ["02-ontology/ontology.json"]}


def artifact_paths(item: Approval) -> list[str]:
    return list(item.meta.get("artifact_paths") or DEFAULT_ARTIFACTS.get(item.checkpoint or "", []))


def load_artifacts(case: CaseDir, item: Approval) -> dict:
    """The JSON documents a checkpoint asks the approver to review, keyed by kind (prd | factors | ontology)."""
    docs: dict = {}
    for rel in artifact_paths(item):
        if not rel.endswith(".json"):
            continue
        text = case.read(rel, limit=10**7)
        try:
            doc = json.loads(text) if text else None
        except ValueError:
            doc = None
        if not isinstance(doc, dict):
            continue
        if "definition_of_done" in doc:
            docs["prd"] = doc
        elif "taxonomies" in doc:
            docs["ontology"] = doc
        elif "factors" in doc:
            docs["factors"] = doc
    return docs


def revision_history(docs: dict) -> list[dict]:
    """`revisions: [{n, decision, reason, approver, date}]` recorded in the reviewed artifact (v0.9.5), newest first."""
    rows = []
    for doc in docs.values():
        for r in (doc or {}).get("revisions") or []:
            if isinstance(r, dict):
                rows.append({k: r.get(k) for k in ("n", "decision", "reason", "approver", "date")})
    return sorted(rows, key=lambda r: r["n"] if isinstance(r["n"], int) else -1, reverse=True)


def archived_drafts(case: CaseDir, item: Approval) -> list[dict]:
    """Rejected drafts the engine archived under `<phase_dir>/revisions/<n>/` (v0.9.5), newest first.
    Paths are relative to the case dir and only ever read through CaseDir (path-safe)."""
    base = case.path(f"{item.phase_dir}/revisions")
    if not base or not base.is_dir():
        return []
    out = []
    for d in base.iterdir():
        if not (d.is_dir() and d.name.isdigit()):
            continue
        files = sorted(str(f.relative_to(case.root)) for f in d.iterdir()
                       if f.is_file() and not f.is_symlink() and case.path(str(f.relative_to(case.root))))
        out.append({"n": int(d.name), "files": files})
    return sorted(out, key=lambda r: r["n"], reverse=True)


def taxonomy_stats(tax: dict) -> dict:
    """Nodes per level and critic-label counts for one taxonomy (the numbers the approver signs off on)."""
    levels: dict[int, int] = {}
    critic: dict[str, int] = {}

    def walk(nodes):
        for n in nodes or []:
            levels[n.get("level", 0)] = levels.get(n.get("level", 0), 0) + 1
            critic[n.get("critic_label", "?")] = critic.get(n.get("critic_label", "?"), 0) + 1
            walk(n.get("children"))

    walk(tax.get("children"))
    return {"levels": dict(sorted(levels.items())), "critic": critic, "nodes": sum(levels.values())}


def build_record(case: CaseDir, item: Approval, approver: str, today: date | None = None,
                 decisions: dict[str, str] | None = None, decision: str | None = None,
                 reason: str | None = None) -> dict:
    """The APPROVED record for a pending checkpoint (without digests). Raises DecisionError(400) when invalid.

    An action checkpoint (approve-before-submit, CONTRACT §12) needs `decision` approve|deny. The prd, factors and
    ontology checkpoints may be denied too (CONTRACT v0.9.5); approving them keeps the original shape (no `decision`).
    A deny always needs a reason (ReasonRequired otherwise). The answer is always one APPROVED file; a deny is
    recorded there as `decision: deny` + `reason`, and the engine regenerates the artifact with it."""
    approver = " ".join((approver or "").split())[:IDENTITY_MAX]
    if not approver:
        raise DecisionError("an approver identity is required", 403)
    record: dict = {"approver": approver, "date": (today or datetime.now(UTC).date()).isoformat()}
    if item.checkpoint:
        record["checkpoint"] = item.checkpoint
    if decision is not None and decision not in ("approve", "deny"):
        raise DecisionError("the decision must be approve or deny")
    if item.checkpoint == "action" and decision is None:
        raise DecisionError("choose approve or deny for this action")
    if decision is not None and item.checkpoint not in DENIABLE:
        raise DecisionError("this checkpoint can only be approved")
    if decision == "deny":
        reason = " ".join((reason or "").split())
        if not reason:
            raise DecisionError("Say why you deny it: the engine uses your reason to regenerate the artifact.")
        if len(reason) > DENY_REASON_MAX:
            raise DecisionError(f"Keep the reason under {DENY_REASON_MAX} characters.")
        record["decision"] = "deny"
        record["reason"] = reason
    elif item.checkpoint == "action":
        record["decision"] = "approve"
    if decision == "deny" and decisions and item.checkpoint == "factors":
        # a deny may carry the per-factor view the approver had formed, but only when it is complete and valid
        factors = (load_artifacts(case, item).get("factors") or {}).get("factors") or []
        known = {f["id"] for f in factors if isinstance(f, dict) and f.get("id")}
        if set(decisions) == known and all(v in ("accept", "reject") for v in decisions.values()):
            record["decisions"] = dict(sorted(decisions.items()))
    elif decisions:
        if item.checkpoint != "factors":
            raise DecisionError("per-factor decisions only apply to the factors checkpoint")
        factors = (load_artifacts(case, item).get("factors") or {}).get("factors") or []
        known = {f["id"] for f in factors if isinstance(f, dict) and f.get("id")}
        if set(decisions) != known:
            raise DecisionError("decide every proposed factor, and only those")
        if any(v not in ("accept", "reject") for v in decisions.values()):
            raise DecisionError("each factor decision must be accept or reject")
        if "accept" not in decisions.values():
            raise DecisionError("accept at least one factor, or ask the engine to propose new ones")
        record["decisions"] = dict(sorted(decisions.items()))
    elif (decision != "deny" and item.checkpoint == "factors"
          and (load_artifacts(case, item).get("factors") or {}).get("factors")):
        raise DecisionError("decide each factor (accept or reject) before approving")
    return record

class DecisionError(ValueError):
    """A decision the console refuses; `status` is the HTTP status the web layer answers with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


STALE_MESSAGE = "The artifact changed since you opened it; reload the page and review it again."
IDENTITY_MAX = 200
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WRITE_LOCK = threading.Lock()


def clean_identity(value: str | None) -> str | None:
    """An approver identity as the SSO proxy sent it: trimmed, at most 200 chars, no control characters."""
    if value is None:
        return None
    value = value.strip()
    if not value or _CONTROL.search(value) or len(value) > IDENTITY_MAX:
        return None
    return value


def pending(case: CaseDir, phase_dir: str) -> Approval | None:
    """The checkpoint at `phase_dir` if it is still waiting for a decision (request present, no APPROVED)."""
    return next((a for a in approvals(case) if a.phase_dir == phase_dir and a.approved is None), None)


def artifact_digests(case: CaseDir, item: Approval) -> dict[str, str | None]:
    """sha256 of each reviewed artifact's raw bytes, keyed by case-relative path (None if missing)."""
    out: dict[str, str | None] = {}
    for rel in artifact_paths(item):
        p = case.path(rel)
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p and p.is_file() else None
    return out


def run_id_for(case: CaseDir, item: Approval, live_hint: str | None = None) -> str | None:
    """The run that produced the reviewed artifact: the request's own run_id, else the artifact's
    generated_by.run_id, else the caller's hint (the live run paused at this checkpoint)."""
    if item.meta.get("run_id"):
        return str(item.meta["run_id"])
    for doc in load_artifacts(case, item).values():
        rid = ((doc or {}).get("generated_by") or {}).get("run_id")
        if rid:
            return str(rid)
    return live_hint


def decisions_log(case: CaseDir) -> list[dict]:
    """The case's append-only decision log, newest first (a torn last line is ignored)."""
    p = case.root / "decisions.jsonl"
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[::-1]


def decide(case: CaseDir, case_id: str, phase_dir: str, approver: str, identity_source: str,
           seen_digests: dict[str, str], today: date | None = None, decisions: dict[str, str] | None = None,
           decision: str | None = None, reason: str | None = None, run_id: str | None = None) -> dict:
    """Validate and record one decision: the APPROVED marker (atomic) and one decisions.jsonl line, both or neither.

    Raises DecisionError(status=409) when the checkpoint is no longer pending or the artifact changed since the
    page was rendered (`seen_digests` must name every reviewed artifact with the digest shown), and
    DecisionError(400) for an invalid request (see `build_record`)."""
    if identity_source not in ("sso", "local"):
        raise DecisionError("unknown identity source", 400)
    with _WRITE_LOCK:
        item = pending(case, phase_dir)
        if item is None:
            raise DecisionError(f"{phase_dir} is not waiting for a decision (already answered or no request)", 409)
        current = artifact_digests(case, item)
        if not current or any(v is None for v in current.values()) or \
                any(seen_digests.get(rel) != digest for rel, digest in current.items()):
            raise DecisionError(STALE_MESSAGE, 409)
        record = build_record(case, item, approver, today, decisions, decision, reason)
        record["identity_source"] = identity_source
        record["artifact_sha256"] = dict(sorted(current.items()))
        run_id = run_id_for(case, item, run_id)
        if run_id:
            record["run_id"] = run_id
        target = case.path(phase_dir)
        marker = target / "APPROVED"
        tmp = target / f".APPROVED.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_text(json.dumps(record) + "\n")
        try:
            if marker.exists():  # never overwrite a concurrent decision
                raise DecisionError(f"{phase_dir} is already answered", 409)
            os.replace(tmp, marker)
        finally:
            tmp.unlink(missing_ok=True)
        line = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "case_id": case_id,
                "checkpoint": record.get("checkpoint"), "phase_dir": phase_dir,
                "decision": record.get("decision", "approve"), **({"reason": record["reason"]} if record.get("reason") else {}),
                "approver": record["approver"], "identity_source": identity_source,
                "artifact_sha256": record["artifact_sha256"], **({"run_id": run_id} if run_id else {})}
        try:
            fd = os.open(case.root / "decisions.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, (json.dumps(line, ensure_ascii=False) + "\n").encode())
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            marker.unlink(missing_ok=True)  # both land or neither
            raise DecisionError(f"could not record the decision ({type(exc).__name__}); nothing was written", 500) from exc
        return record
