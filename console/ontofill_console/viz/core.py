"""Shared plumbing for the visualization views: the install context, the view registry, a read-only artifact loader
over one case (case package + lake run feed + gold export + bronze metadata), and honest empty states that name
the gap (coord/GAPS.md row) whose work will produce the missing data."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .. import approvals as ap
from .. import live

SAFE_ERRORS = (LookupError, OSError, ValueError, KeyError, TypeError)

# Gap rows that produce data some views wait for (coord/GAPS.md). Shown verbatim in empty states.
GAPS = {
    "R2": "P5 real path: multi-source execution and list-membership evidence",
    "R3": "P5 browser controller: S1 sessions, verify verdicts and gates in the trace",
    "R4": "Pattern A inside runs: extractor repairs and promoted macros",
    "R6": "signals and relationships evaluated in refine",
    "R8": "containment moments on a real run (quarantine, limit kill)",
    "R10": "taxonomy coverage labelled by a separate critic",
    "R11": "re-refine from bronze with the current ontology",
    "R15": "spiders and site graphs per source",
}


@dataclass
class View:
    key: str  # nav key (render(..., nav=key))
    slug: str  # URL segment under /cases/<id>/ (case views) or absolute href (global views)
    label: str
    order: int = 100

    @property
    def href(self) -> str:
        return self.slug


VIEWS: list[View] = []  # kept for import compatibility; each app's views live on its VizContext
GLOBAL_VIEWS: list[View] = []


@dataclass
class VizContext:
    app: Any
    render: Callable
    get_case: Callable
    case_domain: Callable
    settings: Any
    env: Any
    views: list[View] = field(default_factory=list)
    global_views: list[View] = field(default_factory=list)

    def case_view(self, key: str, slug: str, label: str, order: int) -> None:
        self.views.append(View(key, slug, label, order))

    def global_view(self, key: str, href: str, label: str, order: int) -> None:
        self.global_views.append(View(key, href, label, order))


def gap(row: str | None, what: str, source: str | None = None) -> dict:
    """An honest empty state: what will appear here, which artifact it comes from, which gap produces it."""
    return {"what": what, "source": source, "row": row, "row_desc": GAPS.get(row or "")}


def parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def age(value, now: datetime | None = None) -> str | None:
    ts = parse_ts(value)
    if ts is None:
        return None
    secs = max(0, int(((now or datetime.now(UTC)) - ts).total_seconds()))
    if secs < 90:
        return f"{secs} s"
    if secs < 5400:
        return f"{secs // 60} min"
    if secs < 172800:
        return f"{secs // 3600} h"
    return f"{secs // 86400} d"


class Artifacts:
    """Read-only access to one case's artifacts. Every accessor tolerates missing or half-written files."""

    def __init__(self, case):
        self.case = case
        self.root = Path(case.root)
        self.store = case.store
        self.dir = ap.CaseDir(self.root)

    # case package ---------------------------------------------------------------------------------------------
    def text(self, rel: str, limit: int = 10**7) -> str | None:
        return self.dir.read(rel, limit=limit)

    def json(self, rel: str):
        raw = self.text(rel)
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    def yaml(self, rel: str):
        raw = self.text(rel)
        try:
            return yaml.safe_load(raw) if raw else None
        except yaml.YAMLError:
            return None

    def glob(self, pattern: str) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(str(p.relative_to(self.root)) for p in self.root.glob(pattern) if p.is_file())

    def approvals(self) -> list:
        return ap.approvals(self.dir)

    def decisions(self) -> list[dict]:
        return ap.decisions_log(self.dir)

    # lake: live run feed --------------------------------------------------------------------------------------
    def run_ids(self) -> list[str]:
        try:
            return list(self.store.live_run_ids())
        except SAFE_ERRORS:
            return []

    def latest_run_id(self) -> str | None:
        try:
            return self.store.live_run_id()
        except SAFE_ERRORS:
            return None

    def status(self, run_id: str | None) -> dict:
        if not run_id:
            return {}
        try:
            return self.store.live_status(run_id) or {}
        except SAFE_ERRORS:
            return {}

    def steps(self, run_id: str | None) -> list[dict]:
        if not run_id:
            return []
        try:
            return live.annotate(self.store.live_steps(run_id))
        except SAFE_ERRORS:
            return []

    def jobs(self, run_id: str | None) -> list[dict]:
        if not run_id:
            return []
        try:
            return self.store.live_jobs(run_id)
        except SAFE_ERRORS:
            return []

    # lake: gold export ----------------------------------------------------------------------------------------
    def gold_run_ids(self) -> list[str]:
        try:
            return list(self.store.run_ids())
        except SAFE_ERRORS:
            return []

    def gold(self, run_id: str | None = None):
        try:
            return self.store.run(run_id)
        except SAFE_ERRORS:
            return None

    def bronze_meta(self, key: str | None) -> dict:
        if not key:
            return {}
        try:
            return self.store.bronze_meta(key) or {}
        except SAFE_ERRORS:
            return {}
