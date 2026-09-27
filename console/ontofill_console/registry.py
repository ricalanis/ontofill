"""The case registry (CONTRACT v1.0.6): one `cases.json` in the cases root, shared by the console, the runner and deploy.

Layout per case: `<root>/<id>/case/` (the case package; the engine adds its phase folders on its first run) and
`<root>/<id>/lake.yaml` (the engine's lake pointer). The registry is written atomically (tmp + rename) under an
exclusive lock on `cases.json.lock`; readers never need the lock and keep their last good copy on a torn read.
Status is not stored here: the console derives it from the runner and the run feed. Nothing is ever hard-deleted:
archive hides a case and restore brings it back; "revise the question" makes a new case version.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import unicodedata
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import yaml

REGISTRY = "cases.json"
LOCK = "cases.json.lock"
TEMPLATE = "lake.template.yaml"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
TITLE_MAX, QUESTION_MIN, QUESTION_MAX, NOTES_MAX = 120, 10, 4000, 4000
DEFAULT_BUDGET, DEFAULT_MAX_BUDGET = 2.0, 10.0


class RegistryError(ValueError):
    """A request the registry refuses; `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def root_from_env(env: dict[str, str] | None = None) -> Path | None:
    raw = (os.environ if env is None else env).get("ONTOFILL_CASES_ROOT", "").strip()
    return Path(raw) if raw else None


def max_budget(env: dict[str, str] | None = None) -> float:
    try:
        return float((os.environ if env is None else env).get("ONTOFILL_CASES_MAX_BUDGET_USD", DEFAULT_MAX_BUDGET))
    except ValueError:
        return DEFAULT_MAX_BUDGET


# reading ---------------------------------------------------------------------------------------------------------
def load(root: Path) -> dict:
    """The registry, or an empty one. Raises RegistryError only when the file exists and cannot be parsed."""
    path = root / REGISTRY
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return {"version": 1, "cases": []}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RegistryError(f"{REGISTRY} is not valid JSON ({exc})", 500) from exc
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise RegistryError(f"{REGISTRY} has no cases list", 500)
    return data


def entry(data: dict, case_id: str) -> dict | None:
    return next((c for c in data["cases"] if isinstance(c, dict) and c.get("id") == case_id), None)


def safe_path(root: Path, rel: str) -> Path:
    """A registry path resolved inside the cases root: relative, no '..', no symlink anywhere on the way."""
    if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise RegistryError(f"unsafe case path {rel!r}", 400)
    base = root.resolve()
    here = base
    for part in Path(rel).parts:
        here = here / part
        if here.is_symlink():
            raise RegistryError(f"case path {rel!r} goes through a symlink", 400)
    resolved = (base / rel).resolve()
    if resolved != base and base not in resolved.parents:
        raise RegistryError(f"case path {rel!r} leaves the cases root", 400)
    return resolved


def resolve(root: Path, value: str) -> Path:
    """Absolute paths are allowed only for migrated entries (written by deploy, never by the console)."""
    return Path(value) if Path(value).is_absolute() else safe_path(root, value)


# writing ---------------------------------------------------------------------------------------------------------
@contextmanager
def locked(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def save(root: Path, data: dict) -> None:
    tmp = root / f".{REGISTRY}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    with open(tmp, "rb+") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, root / REGISTRY)


def audit(case_dir: Path, record: dict) -> None:
    """One line in the case's append-only decisions.jsonl (same file and guarantees as approvals)."""
    case_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(case_dir / "decisions.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(record, ensure_ascii=False) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def slugify(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:32].strip("-")
    return slug or "case"


def _unique(data: dict, root: Path, base: str) -> str:
    taken = {c.get("id") for c in data["cases"] if isinstance(c, dict)}
    cid, n = base, 2
    while cid in taken or (root / cid).exists():
        cid = f"{base[:36]}-{n}"
        n += 1
    return cid


def brief_text(title: str, question: str, notes: str | None) -> str:
    """brief.md exactly as typed: the title as heading, the question as its body, notes in their own section."""
    text = f"# {title.strip()}\n\n{question.strip()}\n"
    if notes and notes.strip():
        text += f"\n## Constraints and notes\n\n{notes.strip()}\n"
    return text


def _validate(title: str, question: str, notes: str | None, budget) -> tuple[str, str, str, float]:
    title, question, notes = (title or "").strip(), (question or "").strip(), (notes or "").strip()
    if not 1 <= len(title) <= TITLE_MAX or "\n" in title:
        raise RegistryError(f"the title needs 1–{TITLE_MAX} characters on one line")
    if not QUESTION_MIN <= len(question) <= QUESTION_MAX:
        raise RegistryError(f"the question needs {QUESTION_MIN}–{QUESTION_MAX} characters")
    if len(notes) > NOTES_MAX:
        raise RegistryError(f"constraints and notes take at most {NOTES_MAX} characters")
    try:
        budget_usd = float(budget) if budget not in (None, "") else DEFAULT_BUDGET
    except (TypeError, ValueError) as exc:
        raise RegistryError("the budget must be a number in USD") from exc
    if not 0 < budget_usd <= max_budget():
        raise RegistryError(f"the budget must be more than 0 and at most {max_budget():g} USD")
    return title, question, notes, round(budget_usd, 2)


def _lake_yaml(root: Path, cid: str, lake: str) -> str:
    if lake == "scratch":
        doc = {"case_id": cid, "bronze": {"kind": "file", "root": str((root / cid / ".lake").resolve())}}
        return yaml.safe_dump(doc, sort_keys=False)
    template = root / TEMPLATE
    if not template.is_file():
        raise RegistryError(
            f"no default lake configured: {TEMPLATE} is missing from the cases root; choose the scratch lake", 400
        )
    doc = yaml.safe_load(template.read_text()) or {}
    if not isinstance(doc, dict):
        raise RegistryError(f"{TEMPLATE} is not a mapping", 500)
    doc["case_id"] = cid
    return yaml.safe_dump(doc, sort_keys=False)


def create(
    root: Path,
    *,
    title: str,
    question: str,
    notes: str | None = None,
    budget_usd=None,
    lake: str = "default",
    created_by: dict,
    supersedes: str | None = None,
    to_phase=None,
) -> dict:
    """A new case: minimal package (brief.md + lake.yaml), registered and logged. Returns the registry entry."""
    if lake not in ("default", "scratch"):
        raise RegistryError("lake must be default or scratch")
    title, question, notes, budget = _validate(title, question, notes, budget_usd)
    try:
        to_phase = int(to_phase) if to_phase not in (None, "") else 5
    except (TypeError, ValueError) as exc:
        raise RegistryError("run up to phase must be 1–5") from exc
    if not 1 <= to_phase <= 5:
        raise RegistryError("run up to phase must be 1–5")
    with locked(root):
        data = load(root)
        cid = _unique(data, root, slugify(title))
        case_dir = root / cid / "case"
        lake_text = _lake_yaml(root, cid, lake)  # validates the template before anything is written
        case_dir.mkdir(parents=True, exist_ok=False)
        brief = brief_text(title, question, notes)
        (case_dir / "brief.md").write_text(brief)
        (root / cid / "lake.yaml").write_text(lake_text)
        item = {
            "id": cid,
            "title": title,
            "path": f"{cid}/case",
            "lake": f"{cid}/lake.yaml",
            "lake_kind": lake,
            "budget_usd": budget,
            "to_phase": to_phase,
            "created_by": created_by,
            "created_at": now_iso(),
            "archived": False,
            "archived_at": None,
            "archived_by": None,
            "version": 1,
            "supersedes": supersedes,
            "superseded_by": None,
            "brief_sha256": _sha(brief),
        }
        if supersedes:
            old = entry(data, supersedes)
            item["version"] = int((old or {}).get("version") or 1) + 1
        data["cases"].append(item)
        save(root, data)
    audit(
        case_dir,
        {
            "ts": item["created_at"],
            "case_id": cid,
            "action": "case.create",
            "checkpoint": "case",
            "decision": "create",
            **_who(created_by),
            "title": title,
            "budget_usd": budget,
            "lake": lake,
            "brief_sha256": item["brief_sha256"],
            **({"supersedes": supersedes} if supersedes else {}),
        },
    )
    return item


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def _who(by: dict) -> dict:
    return {
        "approver": by.get("approver"),
        "identity_source": by.get("identity_source"),
        **({"unverified_name": by["unverified_name"]} if by.get("unverified_name") else {}),
    }


def has_run(case_dir: Path, run_ids: list[str] | None = None) -> bool:
    """Once the engine has started, the PRD is bound to the brief's fingerprint: the phase folders or any run exist."""
    if run_ids:
        return True
    return any((case_dir / d).exists() for d in ("01-scope", "runs", "02-ontology"))


def update_brief(root: Path, cid: str, *, question: str, notes: str | None, by: dict, run_ids=None) -> dict:
    with locked(root):
        data = load(root)
        item = entry(data, cid)
        if not item:
            raise RegistryError("unknown case", 404)
        case_dir = resolve(root, item["path"])
        if has_run(case_dir, run_ids):
            raise RegistryError(
                "a run exists: the brief is bound to it; use “Revise the question” for a new version", 409
            )
        _, question, notes, _ = _validate(item["title"], question, notes, item.get("budget_usd"))
        brief = brief_text(item["title"], question, notes)
        before = item.get("brief_sha256")
        (case_dir / "brief.md").write_text(brief)
        item["brief_sha256"] = _sha(brief)
        save(root, data)
    audit(
        case_dir,
        {
            "ts": now_iso(),
            "case_id": cid,
            "action": "case.update_brief",
            "checkpoint": "case",
            "decision": "update",
            **_who(by),
            "brief_sha256_before": before,
            "brief_sha256": item["brief_sha256"],
        },
    )
    return item


def update_meta(root: Path, cid: str, *, title: str | None, budget_usd, by: dict, run_ids=None) -> dict:
    """Title edits are always allowed (logged). The budget is part of the engine's fingerprint, so it is editable only
    before the first run; afterwards it would regenerate the current draft on the next resume."""
    with locked(root):
        data = load(root)
        item = entry(data, cid)
        if not item:
            raise RegistryError("unknown case", 404)
        try:
            wants_budget = budget_usd not in (None, "") and float(budget_usd) != float(item.get("budget_usd") or 0)
        except ValueError as exc:
            raise RegistryError("the budget must be a number in USD") from exc
        if wants_budget and has_run(resolve(root, item["path"]), run_ids):
            raise RegistryError("the budget is fixed once a run has started (the engine fingerprints it)", 409)
        new_title, _, _, budget = _validate(
            title or item["title"],
            "x" * QUESTION_MIN,
            "",
            budget_usd if budget_usd not in (None, "") else item.get("budget_usd"),
        )
        changes = {k: [item.get(k), v] for k, v in (("title", new_title), ("budget_usd", budget)) if item.get(k) != v}
        item.update(title=new_title, budget_usd=budget)
        save(root, data)
    if changes:
        audit(
            resolve(root, item["path"]),
            {
                "ts": now_iso(),
                "case_id": cid,
                "action": "case.update",
                "checkpoint": "case",
                "decision": "update",
                **_who(by),
                "changes": changes,
            },
        )
    return item


def set_archived(root: Path, cid: str, archived: bool, *, by: dict, reason: str | None = None) -> dict:
    with locked(root):
        data = load(root)
        item = entry(data, cid)
        if not item:
            raise RegistryError("unknown case", 404)
        if bool(item.get("archived")) == archived:
            raise RegistryError("already archived" if archived else "not archived", 409)
        item.update(
            archived=archived, archived_at=now_iso() if archived else None, archived_by=_who(by) if archived else None
        )
        save(root, data)
    audit(
        resolve(root, item["path"]),
        {
            "ts": now_iso(),
            "case_id": cid,
            "action": "case.archive" if archived else "case.restore",
            "checkpoint": "case",
            "decision": "archive" if archived else "restore",
            **_who(by),
            **({"reason": reason[:500]} if reason else {}),
        },
    )
    return item


def revise(root: Path, cid: str, *, question: str, notes: str | None, by: dict, lake: str | None = None) -> dict:
    """A new case version with a revised question; the old one is archived and linked both ways."""
    data = load(root)
    old = entry(data, cid)
    if not old:
        raise RegistryError("unknown case", 404)
    if old.get("superseded_by"):
        raise RegistryError(f"already revised as {old['superseded_by']}", 409)
    new = create(
        root,
        title=old["title"],
        question=question,
        notes=notes,
        budget_usd=old.get("budget_usd"),
        lake=lake or old.get("lake_kind") or "default",
        created_by=by,
        supersedes=cid,
    )
    with locked(root):
        data = load(root)
        item = entry(data, cid)
        item.update(superseded_by=new["id"], archived=True, archived_at=now_iso(), archived_by=_who(by))
        save(root, data)
    audit(
        resolve(root, item["path"]),
        {
            "ts": now_iso(),
            "case_id": cid,
            "action": "case.revise",
            "checkpoint": "case",
            "decision": "revise",
            **_who(by),
            "superseded_by": new["id"],
        },
    )
    return new
