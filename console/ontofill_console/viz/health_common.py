"""Helpers shared by the failures, cost, learning and compare views (worker `health`). Read-only; no install()."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import HTTPException

from .. import live
from .core import SAFE_ERRORS, Artifacts

# Categorical colours for share bars, only through tokens (no raw colour values in templates or here).
PALETTE = ("var(--st-run)", "var(--st-done)", "var(--st-need)", "var(--st-quar)", "var(--heat-2)", "var(--st-pause)")
OTHER_COLOUR = "var(--line-strong)"
# Execution modes: the cheap, code-only modes read green, the agentic ones blue and magenta.
MODE_COLOURS = {"D0": "var(--heat-3)", "D1": "var(--heat-2)", "S1": "var(--st-run)", "S2": "var(--st-quar)"}
MODES = ("D0", "D1", "S1", "S2")


def run_ids(a: Artifacts) -> list[str]:
    """Every run of the case: live feed runs and gold export runs, in first-seen order."""
    out = list(a.run_ids())
    out += [r for r in a.gold_run_ids() if r not in out]
    return out


def resolve_run(a: Artifacts, run: str | None) -> str | None:
    """The run a view shows: `?run=<id>` when it exists (404 otherwise), else the latest live run, else latest gold."""
    if run:
        if run in run_ids(a):
            return run
        raise HTTPException(404, f"no run {run} in this case")
    rid = a.latest_run_id()
    if rid:
        return rid
    gold = a.gold()
    return gold.run_id if gold is not None else None


def steps_of(a: Artifacts, run_id: str | None) -> list[dict]:
    """Annotated steps of a run: the live feed, else the gold export's trace."""
    if not run_id:
        return []
    steps = a.steps(run_id)
    if steps:
        return steps
    gold = gold_run(a, run_id)
    try:
        return live.annotate(list(gold.trace)) if gold is not None else []
    except SAFE_ERRORS:
        return []


def gold_run(a: Artifacts, run_id: str | None):
    """The gold export of exactly this run, or None (never silently another run)."""
    if not run_id or run_id not in a.gold_run_ids():
        return None
    return a.gold(run_id)


def metrics_of(a: Artifacts, run_id: str | None) -> dict:
    """Final metrics (gold export) win key by key over the partial metrics in status.json (which may carry keys the
    export lacks, e.g. loops)."""
    gold = gold_run(a, run_id)
    m = a.status(run_id).get("metrics")
    return {**(m if isinstance(m, dict) else {}), **((gold.metrics or {}) if gold is not None else {})}


def started(a: Artifacts, run_id: str, steps: list[dict] | None = None) -> str:
    """When a run started: its first step's ts, else status.updated_at, else ''."""
    ts = [str(s.get("ts")) for s in (steps if steps is not None else steps_of(a, run_id)) if s.get("ts")]
    return min(ts) if ts else str(a.status(run_id).get("updated_at") or "")


def runs_by_start(a: Artifacts) -> list[str]:
    return sorted(run_ids(a), key=lambda r: (started(a, r), r))


def mode_counts(steps: list[dict], metrics: dict | None = None) -> dict:
    counts = {m: 0 for m in MODES}
    for s in steps:
        if s.get("mode") in counts:
            counts[s["mode"]] += 1
    if not any(counts.values()):
        mc = (metrics or {}).get("mode_counts")
        if isinstance(mc, dict):
            counts = {m: int(mc.get(m) or 0) for m in MODES}
    return counts


def code_only_share(counts: dict) -> float | None:
    total = sum(counts.values())
    return (counts.get("D0", 0) + counts.get("D1", 0)) / total if total else None


def usage_of(step: dict) -> dict | None:
    """CONTRACT v1.0.5 `usage{model, backend, input_tokens, output_tokens, est_usd}` on model steps."""
    u = step.get("usage")
    if not isinstance(u, dict) or not u:
        return None
    if str(u.get("model")) == "none" and not u.get("input_tokens") and not u.get("output_tokens"):
        return None  # phase_loop: a stage that made no model call (gather, check, decide) reports model "none"
    return u


def usd_of(step: dict) -> float | None:
    u = usage_of(step) or {}
    v = u.get("est_usd")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def run_step_usd(steps: list[dict]) -> float | None:
    known = [x for x in (usd_of(s) for s in steps) if x is not None]
    return round(sum(known), 6) if known else None


def spend_history_latest() -> dict | None:
    """Latest snapshot of the spend tracker's history (same env var the /spend page reads). Read-only."""
    raw = os.environ.get("ONTOFILL_CONSOLE_SPEND_HISTORY")
    path = Path(raw) if raw else None
    if not path or not path.is_file():
        return None
    latest = None
    try:
        for line in path.read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("ts"):
                latest = rec
    except OSError:
        return None
    return latest


def shares(
    rows: list[dict], key: str, limit: int = 6, colours: dict | None = None, keep_order: bool = False
) -> list[dict]:
    """Rows sorted by `key` desc (or kept in order), the tail folded into 'other', each with `share` (0..1) and a
    colour token."""
    rows = [r for r in rows if (r.get(key) or 0) > 0]
    if not keep_order:
        rows.sort(key=lambda r: -(r.get(key) or 0))
    if len(rows) > limit:
        head, tail = rows[: limit - 1], rows[limit - 1 :]
        other = {"name": f"other ({len(tail)})", key: sum(r.get(key) or 0 for r in tail), "other": True}
        for k in ("steps", "input_tokens", "output_tokens", "calls", "usd"):
            if k != key and any(k in r for r in tail):
                other[k] = sum(r.get(k) or 0 for r in tail)
        rows = [*head, other]
    total = sum(r.get(key) or 0 for r in rows)
    out = []
    for i, r in enumerate(rows):
        colour = (colours or {}).get(r.get("name")) or (OTHER_COLOUR if r.get("other") else PALETTE[i % len(PALETTE)])
        out.append({**r, "share": (r.get(key) or 0) / total if total else 0.0, "c": colour})
    return out


def jsonish(value):
    """Trace fields are JSON: show booleans as JSON does (true/false), not as Python reprs (True/False)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return {k: jsonish(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonish(v) for v in value]
    return value


def text_of(value, limit: int = 160) -> str:
    """Readable one-liner for trace fields written as strings or objects."""
    if value is None:
        return ""
    value = jsonish(value)
    if isinstance(value, dict):
        text = ", ".join(f"{k}: {v}" for k, v in value.items() if v not in (None, "", [], {}))
    elif isinstance(value, list):
        text = ", ".join(str(x) for x in value[:4])
    else:
        text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


STOPPED_STATES = ("failed", "stopped", "budget_stop", "needs_human")
PHASE_VIEWS = {1: "definition", 2: "definition", 3: "discovery", 4: "discovery", 5: "operation"}


def stop_cause(steps: list[dict], state: str | None, case_id: str, rid: str | None) -> dict | None:
    """Why a failed or stopped run stopped, read from its own trace: the run's last phase loop, when it ended without
    passing its checks (e.g. the iteration cap with every candidate rejected). None while the run is healthy, or when
    the trace does not say."""
    if state not in STOPPED_STATES or not steps:
        return None
    threads = [t for t in live.loop_threads(steps) if t["phase"] != "outer"]
    if not threads:
        return None
    t = max(threads, key=lambda x: x["last"])
    if not t.get("stop_reason") or t["stop_reason"] == "checks_passed":
        return None
    objections = [o for it in t["iterations"] for s in it["steps"] for o in s["detail"]["objections"]]
    phase = t["phase"]
    last_step = t["step_ids"][-1] if t["step_ids"] else None
    text = (
        f"Phase {phase} · {live.phase_name(phase)}: its loop stopped at the {t['stop_label']} after "
        f"{len(t['iterations'])} iteration{'s' if len(t['iterations']) != 1 else ''} without passing its checks"
    )
    view = PHASE_VIEWS.get(phase)
    q = f"?run={rid}" if rid else ""
    return {
        "phase": phase,
        "phase_name": live.phase_name(phase),
        "stop_reason": t["stop_reason"],
        "stop_label": t["stop_label"],
        "iterations": len(t["iterations"]),
        "usd": t["usd"],
        "n_objections": len(objections),
        "objection": text_of(objections[-1], 240) if objections else None,
        "step_id": last_step,
        "text": text,
        "href": f"/cases/{case_id}/{view}{q}" if view else f"/cases/{case_id}/runs/{rid}#{last_step}",
        "step_href": f"/cases/{case_id}/runs/{rid}#{last_step}" if rid and last_step else None,
    }
