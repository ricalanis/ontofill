"""Q1 and Q6–Q8 across cases, for unattended runs · Watch: one supervision page (and a JSON twin polled every minute)
over every registered case: is the runner on, is each case's current run still moving, how fast, what is failing and
where, what it spends against the runner's caps, how close it is to done, and a ranked "look here first" list.

Reads the runner state directory read-only (`KILL`, `events.jsonl`, `cases/<id>/status.json` and `control.json`, via
`runner_state`), and per case the live feed of its current run (`status.json`, `trace.live.jsonl`, `jobs.jsonl`),
the est_usd of every run's trace, and the gold export's `metrics.json` per run. Caps come from the runner's own
settings when the console can see them (`ONTOFILL_RUNNER_BUDGETS`, `ONTOFILL_RUNNER_DEFAULT_CASE_USD`,
`ONTOFILL_RUNNER_GLOBAL_USD`), from cap fields in the runner status, or from the runner's own budget-stop reason;
otherwise they are reported as unknown. Nothing here writes.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import Query, Request
from fastapi.responses import HTMLResponse

from .. import dod, live, runner_state
from ..domain import Domain
from ..gold import backend_of
from .core import SAFE_ERRORS, Artifacts, VizContext, parse_ts
from .failures import classify
from .operation import is_live, live_marker, step_usd
from .output import dod_rows
from .output_common import domain_for

ORDER = 3
KEY, HREF, LABEL = "watch", "/watch", "Watch"
SOURCES = ("runner state: KILL · events.jsonl · cases/<id>/status.json · control.json | "
           "runs/<case>/<run>/status.json · trace.live.jsonl (ts, phase, usage.est_usd) · jobs.jsonl | "
           "gold/<case>/<run>/metrics.json")
STALE_MIN = 10.0          # default: a running case with no step for longer than this is STALE
WINDOWS = (15, 60)        # throughput windows, minutes
PROJECTION_ALERT_H = 8.0  # a cap projected to be hit within this many hours goes on the attention list
GOLD_CAP = 20             # DoD series: at most this many recent gold runs
POLL_MS = 15000           # the page refreshes itself this often while a case is running (viz-live.js)
RUNNING = "running"
CRITICAL = ("killed", "failed", "budget_stop")

# failure kinds counted here: failures.classify kinds that mean something went wrong or was contained
FAIL_KINDS = [("stop", "captcha / login / hard stops"), ("blocked_domain", "blocked domains"),
              ("failure", "errors"), ("escalation", "retries and escalations"), ("limit_kill", "limit kills"),
              ("quarantine", "quarantines"), ("refusal", "refusals")]
FAIL_KEYS = [k for k, _ in FAIL_KINDS]

SEVERITY = {1: ("critical", "block"), 2: ("person", "need"), 3: ("failures", "block"), 4: ("spend", "pause"),
            5: ("dod", "run")}

# the runner's budget-stop reasons: "case spend $1.52 ≥ $1.50" / "global spend $9.10 ≥ $9.00"
CAP_RE = {"case": re.compile(r"case spend \$([\d.]+)\s*≥\s*\$([\d.]+)"),
          "global": re.compile(r"global spend \$([\d.]+)\s*≥\s*\$([\d.]+)")}

# est_usd of runs that are no longer moving, keyed by (lake root, case, run) and the status updated_at it was read at
_USD_CACHE: dict[tuple, tuple] = {}


# small helpers ------------------------------------------------------------------------------------------------------
def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _age_s(ts, now: datetime) -> int | None:
    t = parse_ts(ts)
    return max(0, int((now - t).total_seconds())) if t else None


def _iso(t: datetime | None) -> str | None:
    return t.astimezone(UTC).isoformat(timespec="seconds") if t else None


def human_age(secs: int | None) -> str | None:
    if secs is None:
        return None
    if secs < 90:
        return f"{secs} s"
    if secs < 5400:
        return f"{secs // 60} min"
    if secs < 172800:
        return f"{secs // 3600} h {(secs % 3600) // 60:02d} min"
    return f"{secs // 86400} d"


def parse_budgets(spec: str | None) -> dict[str, float]:
    """The runner's `ONTOFILL_RUNNER_BUDGETS` format: `id=usd,…` (bad entries are skipped, never raised)."""
    out: dict[str, float] = {}
    for part in (p.strip() for p in (spec or "").split(",")):
        cid, sep, usd = part.partition("=")
        try:
            if sep:
                out[cid.strip()] = float(usd)
        except ValueError:
            continue
    return out


def _env_float(env: dict, name: str) -> float | None:
    try:
        return float(env[name]) if env.get(name) else None
    except ValueError:
        return None


# runner -------------------------------------------------------------------------------------------------------------
def runner_overview(root: Path, now: datetime) -> dict:
    ok = root.is_dir()
    kill = runner_state.killed(root) if ok else False
    evs = runner_state.events(root, limit=1000) if ok else []
    stamps = []
    if ok:
        try:
            for p in (root / "cases").glob("*/status.json"):
                stamps.append(runner_state.status(root, p.parent.name).get("updated_at"))
        except OSError:
            pass
    newest = max((t for t in map(parse_ts, stamps) if t), default=None)
    return {"on": ok and not kill, "killed": kill, "state_dir_ok": ok, "state_dir": str(root),
            "last_write_at": _iso(newest), "last_write_age_s": _age_s(_iso(newest), now) if newest else None,
            "n_events": len(evs), "events": evs}


def case_runner(root: Path, case_id: str, evs: list[dict], ok: bool) -> dict:
    st = runner_state.status(root, case_id) if ok else {}
    ctl = runner_state.control(root, case_id) if ok else {}
    mine = [e for e in evs if e.get("case_id") == case_id]
    resume = next((e for e in reversed(mine) if e.get("kind") == "resumed"), None)
    last = mine[-1] if mine else None

    def ev(e):
        return {"ts": e.get("ts"), "kind": e.get("kind"), "run_id": e.get("run_id"),
                "detail": (str(e.get("detail") or "").splitlines() or [None])[0]} if e else None

    return {"seen": bool(st), "state": st.get("state"), "run_id": st.get("run_id"), "checkpoint": st.get("checkpoint"),
            "reason": st.get("reason"), "paused": bool(ctl.get("paused") or st.get("paused")), "pid": st.get("pid"),
            "running_since": st.get("running_since"), "last_started_at": st.get("last_started_at"),
            "last_resumed_at": st.get("last_resumed_at"), "updated_at": st.get("updated_at"),
            "spent_usd_case": _num(st.get("spent_usd_case")), "spent_usd_global": _num(st.get("spent_usd_global")),
            "last_trigger": st.get("last_trigger"), "last_resume": ev(resume), "last_event": ev(last),
            "_status": st}


# liveness -----------------------------------------------------------------------------------------------------------
def liveness(runner: dict, status: dict, steps: list[dict], now: datetime, stale_min: float) -> dict:
    """STALE: the runner or the run status says running, yet no step has landed for more than `stale_min` minutes
    (measured from the newest of the last step and the runner's own (re)start of the run)."""
    last_step = max((t for t in (parse_ts(s.get("ts")) for s in steps[-20:]) if t), default=None)
    started = parse_ts(runner.get("running_since")) if runner.get("state") == RUNNING else None
    ref = max((t for t in (last_step, started) if t), default=None)
    # a runner that stopped the engine (killed, budget_stop, failed, …) outranks a lake status left at "running";
    # a lake run the runner does not know about (an operator's CLI run) still counts
    says_running = runner.get("state") == RUNNING or (status.get("state") == RUNNING and runner.get("state") in
                                                      (None, "idle"))
    quiet_s = int((now - ref).total_seconds()) if ref else None
    stale = bool(says_running and quiet_s is not None and quiet_s > stale_min * 60)
    return {"last_step_at": _iso(last_step), "last_step_age_s": _age_s(_iso(last_step), now) if last_step else None,
            "status_updated_at": status.get("updated_at"), "status_age_s": _age_s(status.get("updated_at"), now),
            "runner_age_s": _age_s(runner.get("updated_at"), now), "says_running": says_running,
            "quiet_s": quiet_s, "stale": stale, "stale_min": stale_min}


# throughput ---------------------------------------------------------------------------------------------------------
def _job_times(jobs: list[dict], step_ts: dict) -> list[datetime]:
    out = []
    for j in jobs:
        t = next((x for x in (parse_ts(j.get(k)) for k in ("ended_at", "started_at", "ts", "created_at")) if x), None)
        out.append(t or step_ts.get(j.get("step_id")))
    return [t for t in out if t]


def throughput(steps: list[dict], jobs: list[dict], anchor: datetime) -> dict:
    """Steps per minute per phase over the last 15 and 60 minutes before `anchor` and over the run; sandbox cells
    (jobs.jsonl records) per hour."""
    stamped = [(parse_ts(s.get("ts")), s) for s in steps]
    times = [t for t, _ in stamped if t]
    first = min(times, default=None)
    run_min = max((anchor - first).total_seconds() / 60, 1 / 60) if first else None
    phases: dict = {}
    for t, s in stamped:
        p = s.get("phase") if isinstance(s.get("phase"), int) else None
        row = phases.setdefault(p, {"phase": p, "name": live.phase_name(p) if p else "no phase", "n": 0,
                                    **{f"n_{w}": 0 for w in WINDOWS}})
        row["n"] += 1
        for w in WINDOWS:
            if t and anchor - timedelta(minutes=w) < t <= anchor:
                row[f"n_{w}"] += 1
    rows = sorted(phases.values(), key=lambda r: (r["phase"] is None, r["phase"] or 0))
    total = {"phase": None, "name": "all phases", "n": len(steps),
             **{f"n_{w}": sum(r[f"n_{w}"] for r in rows) for w in WINDOWS}}
    for r in [*rows, total]:
        for w in WINDOWS:
            r[f"per_min_{w}"] = round(r[f"n_{w}"] / w, 2)
        r["per_min_run"] = round(r["n"] / run_min, 2) if run_min else None
    step_ts = {s.get("step_id"): t for t, s in stamped if t}
    jt = _job_times(jobs, step_ts)
    first_all = min([*times, *jt], default=None)
    run_h = max((anchor - first_all).total_seconds() / 3600, 1 / 60) if first_all else None
    n_60 = sum(1 for t in jt if anchor - timedelta(minutes=60) < t <= anchor)
    return {"anchor": _iso(anchor), "first_step_at": _iso(first), "run_minutes": round(run_min, 1) if run_min else None,
            "phases": rows, "total": total, "n_cells": len(jobs), "n_cells_timed": len(jt), "cells_60": n_60,
            "cells_per_h_60": n_60 if jt else None,
            "cells_per_h_run": round(len(jt) / run_h, 2) if jt and run_h else None}


# failures -----------------------------------------------------------------------------------------------------------
def failure_stats(steps: list[dict], jobs: list[dict], anchor: datetime) -> dict:
    """Failure counts by source and kind (classification reused from the Failures view), the rate per 100 steps,
    the last 15 minutes against the whole run, and sources where every step failed."""
    by_src: dict[str, dict] = {}
    totals = dict.fromkeys(FAIL_KEYS, 0)
    hard = 0
    seen_steps = set()
    win = anchor - timedelta(minutes=15)
    n_win = f_win = 0

    def src_row(src):
        return by_src.setdefault(src, {"source_id": src, "steps": 0, "failures": 0, "hard_stops": 0,
                                       "by_kind": dict.fromkeys(FAIL_KEYS, 0)})

    for s in steps:
        if s.get("kind") == "loop":
            continue  # planning loops are not source work
        src = s.get("source_id") or "no source"
        row = src_row(src)
        row["steps"] += 1
        t = parse_ts(s.get("ts"))
        in_win = bool(t and win < t <= anchor)
        n_win += in_win
        c = classify(s)
        if c is None or c[0] not in totals:
            continue
        seen_steps.add(s.get("step_id"))
        row["failures"] += 1
        row["by_kind"][c[0]] += 1
        totals[c[0]] += 1
        f_win += in_win
        if s.get("event") == "hard_stop":
            row["hard_stops"] += 1
            hard += 1
    for j in jobs:  # a sandbox job stopped early that the trace does not already show
        why = live.job_stop_reason(j)
        if not why or j.get("step_id") in seen_steps:
            continue
        kind = "limit_kill" if why in live.LIMIT_REASONS else "stop"
        row = src_row(j.get("source_id") or "no source")
        row["failures"] += 1
        row["by_kind"][kind] += 1
        totals[kind] += 1
    n_steps = sum(r["steps"] for r in by_src.values())
    n_fail = sum(totals.values())
    rows = []
    for r in by_src.values():
        r["rate_per_100"] = round(100 * r["failures"] / r["steps"], 1) if r["steps"] else None
        r["only_failures"] = r["failures"] >= 3 and r["failures"] >= r["steps"]
        r["top"] = ", ".join(f"{dict(FAIL_KINDS)[k]} {n}" for k, n in sorted(r["by_kind"].items(), key=lambda kv: -kv[1])
                             if n)[:160] or None
        if r["failures"] or r["source_id"] != "no source":
            rows.append(r)
    rows.sort(key=lambda r: (-(r["rate_per_100"] or 0), -r["failures"], r["source_id"]))
    rate = round(100 * n_fail / n_steps, 1) if n_steps else None
    rate_15 = round(100 * f_win / n_win, 1) if n_win else None
    rising = bool(rate_15 is not None and f_win >= 3 and rate_15 > max(2 * (rate or 0), (rate or 0) + 10))
    return {"n_steps": n_steps, "n_failures": n_fail, "rate_per_100": rate, "steps_15": n_win, "failures_15": f_win,
            "rate_per_100_15": rate_15, "rising": rising, "hard_stops": hard,
            "by_kind": [{"kind": k, "label": label, "n": totals[k]} for k, label in FAIL_KINDS],
            "sources": rows, "only_failures": [r["source_id"] for r in rows if r["only_failures"]]}


# spend --------------------------------------------------------------------------------------------------------------
def _run_usd(a: Artifacts, case, rid: str, current: str | None, current_steps: list[dict]) -> tuple[float, int]:
    if rid == current:
        steps = current_steps
    else:
        stamp = a.status(rid).get("updated_at")
        key = (str(getattr(case.store, "source", "")), case.id, rid)
        hit = _USD_CACHE.get(key)
        if hit and hit[0] == stamp and stamp:
            return hit[1], hit[2]
        try:
            steps = case.store.live_steps(rid)
        except SAFE_ERRORS:
            steps = []
        priced = [u for u in map(step_usd, steps) if u is not None]
        _USD_CACHE[key] = (stamp, sum(priced), len(priced))
        return sum(priced), len(priced)
    priced = [u for u in map(step_usd, steps) if u is not None]
    return sum(priced), len(priced)


def caps(case_id: str, runner: dict, env: dict, meta: dict | None = None) -> dict:
    """Per-case and global caps: the runner's env settings (when this process can see them), cap fields in the
    runner's status, or the runner's own budget-stop reason. None when nothing states them."""
    st = runner.get("_status") or {}
    case_cap = global_cap = None
    case_basis = global_basis = None
    budgets = parse_budgets(env.get("ONTOFILL_RUNNER_BUDGETS"))
    if meta and _num(meta.get("budget_usd")) is not None:  # the case registry (v1.0.6) is what the runner reads
        case_cap, case_basis = _num(meta["budget_usd"]), "cases.json budget_usd"
    elif case_id in budgets:
        case_cap, case_basis = budgets[case_id], "ONTOFILL_RUNNER_BUDGETS"
    elif _env_float(env, "ONTOFILL_RUNNER_DEFAULT_CASE_USD") is not None:
        case_cap, case_basis = _env_float(env, "ONTOFILL_RUNNER_DEFAULT_CASE_USD"), "ONTOFILL_RUNNER_DEFAULT_CASE_USD"
    else:
        for k in ("case_cap_usd", "budget_usd_case", "case_budget_usd", "budget_usd"):
            if _num(st.get(k)) is not None:
                case_cap, case_basis = _num(st[k]), f"runner status.{k}"
                break
    if _env_float(env, "ONTOFILL_RUNNER_GLOBAL_USD") is not None:
        global_cap, global_basis = _env_float(env, "ONTOFILL_RUNNER_GLOBAL_USD"), "ONTOFILL_RUNNER_GLOBAL_USD"
    else:
        for k in ("global_cap_usd", "budget_usd_global", "global_budget_usd"):
            if _num(st.get(k)) is not None:
                global_cap, global_basis = _num(st[k]), f"runner status.{k}"
                break
    reason = str(st.get("reason") or "")
    for scope, rx in CAP_RE.items():
        m = rx.search(reason)
        if m and scope == "case" and case_cap is None:
            case_cap, case_basis = float(m.group(2)), "runner budget-stop reason"
        if m and scope == "global" and global_cap is None:
            global_cap, global_basis = float(m.group(2)), "runner budget-stop reason"
    return {"case_cap_usd": case_cap, "case_cap_basis": case_basis,
            "global_cap_usd": global_cap, "global_cap_basis": global_basis}


def burn_rate(steps: list[dict], anchor: datetime) -> tuple[float | None, float, float]:
    """(USD per hour over the last hour of the run, usd in that window, window hours). The window is shorter when
    the run is younger than an hour. None when no priced step landed in the window."""
    times = [t for t in (parse_ts(s.get("ts")) for s in steps) if t]
    if not times:
        return None, 0.0, 0.0
    start = max(anchor - timedelta(minutes=60), min(times))
    span_h = (anchor - start).total_seconds() / 3600
    usd = 0.0
    priced = 0
    for s in steps:
        t, u = parse_ts(s.get("ts")), step_usd(s)
        if t and u is not None and start <= t <= anchor:
            usd += u
            priced += 1
    if not priced or span_h <= 0:
        return None, round(usd, 6), round(span_h, 4)
    return round(usd / span_h, 4), round(usd, 6), round(span_h, 4)


def projection(spent: float | None, cap: float | None, burn: float | None, now: datetime) -> dict:
    if spent is None or cap is None:
        return {"remaining_usd": None, "hours": None, "at": None}
    remaining = round(cap - spent, 6)
    if remaining <= 0:
        return {"remaining_usd": remaining, "hours": 0.0, "at": _iso(now)}
    if not burn:
        return {"remaining_usd": remaining, "hours": None, "at": None}
    hours = remaining / burn
    return {"remaining_usd": remaining, "hours": round(hours, 3), "at": _iso(now + timedelta(hours=hours))}


# DoD ----------------------------------------------------------------------------------------------------------------
def _metric_point(metrics: dict, domain: Domain, run_id: str, at, source: str) -> dict | None:
    if not isinstance(metrics, dict):
        return None
    tot, meet = metrics.get("entities_total"), metrics.get("entities_meeting_dod")
    if not isinstance(tot, dict) or not tot:
        return None
    cls = domain.primary_class if domain.primary_class in tot else next(iter(
        meet if isinstance(meet, dict) and meet else tot))
    total = int(_num(tot.get(cls)) or 0)
    meeting = int(_num((meet or {}).get(cls)) or 0) if isinstance(meet, dict) else None
    return {"run_id": run_id, "at": at, "source": source, "class": cls, "entities_total": total,
            "meeting_dod": meeting, "pct_meeting": round(100 * meeting / total, 1) if total and meeting is not None else None,
            "distinct_source_classes": _num(metrics.get("distinct_source_classes"))}


def _engine_rows(metrics: dict, domain: Domain) -> list[dict]:
    """Criterion meters from a status.json snapshot's metrics.dod[] (the engine's own actual/target/met)."""
    rows = []
    for r in metrics.get("dod") or []:
        if not isinstance(r, dict):
            continue
        actual, target = _num(r.get("actual")), _num(r.get("target"))
        shown = r.get("actual") if actual is not None else None
        scale = max([x for x in (actual, target) if x is not None] + [1])
        rows.append({"criterion_id": r.get("criterion_id"), "label": dod.criterion_label(
            str(r.get("query") or ""), str(r.get("criterion_id") or ""), domain), "query": r.get("query"),
            "shown": shown, "target": r.get("target") if target is not None else None, "met": r.get("met"),
            "v": round(100 * (actual or 0) / scale, 1), "t": round(100 * target / scale, 1) if target is not None else None,
            "state": "done" if r.get("met") else "run"})
    return rows


def dod_progress(a: Artifacts, rid: str | None, status: dict, backend: str | None) -> dict:
    gold_ids = a.gold_run_ids()[-GOLD_CAP:]
    latest_gold = a.gold(None) if gold_ids else None
    onto = a.json("02-ontology/ontology.json")
    domain = domain_for(latest_gold) if latest_gold is not None else Domain.from_ontology(onto if isinstance(onto, dict) else {})
    points: dict[str, dict] = {}
    for grid in gold_ids:
        g = a.gold(grid)
        if g is None:
            continue
        m = dict(g.metrics or {})
        if not isinstance(m.get("entities_total"), dict):
            rc = dod.compute(g.entities, domain_for(g))
            m.update(entities_total=rc["entities_total"], entities_meeting_dod=rc["entities_meeting_dod"],
                     distinct_source_classes=rc["distinct_source_classes"])
        at = a.status(grid).get("updated_at") or m.get("generated_at") or m.get("finished_at")
        p = _metric_point(m, domain, grid, at, "gold")
        if p:
            points[grid] = p
    for lrid in a.run_ids()[-GOLD_CAP:]:
        if lrid in points:
            continue
        st = status if lrid == rid else a.status(lrid)
        p = _metric_point(st.get("metrics") or {}, domain, lrid, st.get("updated_at"), "status")
        if p:
            points[lrid] = p
    series = sorted(points.values(), key=lambda p: (parse_ts(p["at"]) or datetime.min.replace(tzinfo=UTC), p["run_id"]))
    cur_metrics = status.get("metrics") if isinstance(status.get("metrics"), dict) else {}
    if rid and rid not in a.gold_run_ids() and cur_metrics.get("dod"):
        rows, basis = _engine_rows(cur_metrics, domain), f"status.json of {rid} (engine metrics.dod, partial)"
    elif latest_gold is not None:
        rows, basis = dod_rows(latest_gold, domain_for(latest_gold), backend or latest_gold.inference_backend), \
            f"gold export of {latest_gold.run_id}"
    else:
        rows, basis = [], None
    crit = [{"criterion_id": r.get("criterion_id"), "label": r.get("label"), "actual": r.get("shown"),
             "target": r.get("target"), "met": bool(r.get("met")), "v": r.get("v"), "t": r.get("t"),
             "state": r.get("state"), "mock": bool(r.get("mock"))} for r in rows]
    last = series[-1] if series else None
    return {"basis": basis, "latest": last, "entities_total": last["entities_total"] if last else None,
            "pct_meeting": last["pct_meeting"] if last else None,
            "distinct_sources": last["distinct_source_classes"] if last else None,
            "criteria": crit, "n_criteria": len(crit), "n_met": sum(1 for c in crit if c["met"]),
            "series": series, "chart": dod_chart(series) if series else None}


def dod_chart(series: list[dict]) -> dict:
    """Geometry for a small step chart of entities found and meeting the DoD per run (viewBox 0 0 320 120)."""
    x0, x1, y0, y1 = 36, 312, 96, 10
    top = max(max(p["entities_total"] for p in series), 1)
    n = len(series)

    def x(i):
        return round(x0 + (x1 - x0) * (i / (n - 1) if n > 1 else 0.5), 1)

    def y(v):
        return round(y0 - (y0 - y1) * (v or 0) / top, 1)

    def path(key):
        return " ".join((f"M{x(i)},{y(p[key])}" if i == 0 else f"H{x(i)}V{y(p[key])}") for i, p in enumerate(series))

    return {"top": top, "x0": x0, "x1": x1, "y0": y0, "y1": y1, "n": n,
            "total": path("entities_total"), "meeting": path("meeting_dod"),
            "pts": [{"x": x(i), "yt": y(p["entities_total"]), "ym": y(p["meeting_dod"]), "run": p["run_id"],
                     "total": p["entities_total"], "meeting": p["meeting_dod"], "source": p["source"]}
                    for i, p in enumerate(series)],
            "first": series[0]["run_id"], "last": series[-1]["run_id"]}


# one case -----------------------------------------------------------------------------------------------------------
def case_model(case, root: Path, rover: dict, now: datetime, stale_min: float, env: dict) -> dict:
    a = Artifacts(case)
    runner = case_runner(root, case.id, rover["events"], rover["state_dir_ok"])
    base = f"/cases/{case.id}"
    run_ids = a.run_ids()
    rid = runner["run_id"] if runner["run_id"] and runner["state"] in (RUNNING, "waiting_approval", "killed",
                                                                       "budget_stop", "failed") else None
    rid = rid or a.latest_run_id() or runner["run_id"]
    status = a.status(rid)
    steps = a.steps(rid)
    jobs = a.jobs(rid)
    lv = liveness(runner, status, steps, now, stale_min)
    moving = lv["says_running"] or (is_live(status, now) and runner["state"] in (None, "idle", RUNNING))
    last_step = parse_ts(lv["last_step_at"])
    anchor = now if moving else (last_step or parse_ts(status.get("updated_at")) or now)
    thr = throughput(steps, jobs, anchor)
    fails = failure_stats(steps, jobs, anchor)

    # spend: est_usd over every run's trace (the runner's own measure), and the runner's recorded figures
    trace_usd, priced = 0.0, 0
    for r in run_ids:
        u, n = _run_usd(a, case, r, rid, steps)
        trace_usd += u
        priced += n
    case_usd = round(trace_usd, 6) if priced else runner["spent_usd_case"]
    burn, burn_usd, burn_h = burn_rate(steps, anchor) if moving else (None, 0.0, 0.0)
    cap = caps(case.id, runner, env, getattr(case, "meta", None))
    spend = {"case_usd": case_usd, "case_basis": "trace est_usd, all runs" if priced else (
        "runner status" if runner["spent_usd_case"] is not None else None), "priced_steps": priced,
        "runner_case_usd": runner["spent_usd_case"], "global_usd": runner["spent_usd_global"], **cap,
        "burn_usd_per_h": burn, "burn_window_usd": burn_usd, "burn_window_h": burn_h,
        "case_projection": projection(case_usd, cap["case_cap_usd"], burn, now)}
    spend["projected_cap_hit_at"] = spend["case_projection"]["at"] if burn else None
    spend["case_pct"] = round(100 * case_usd / cap["case_cap_usd"], 1) if case_usd is not None and cap["case_cap_usd"] else None
    backend = backend_of(status.get("metrics") if isinstance(status.get("metrics"), dict) else None, [status]) \
        if rid else None
    dp = dod_progress(a, rid, status, backend)
    q = f"?run={quote(rid)}" if rid else ""
    links = {"case": base, "run": f"{base}/runs/{quote(rid)}" if rid else f"{base}/runs",
             "operation": f"{base}/operation{q}", "failures": f"{base}/failures{q}", "inference": f"{base}/inference{q}",
             "cost": f"{base}/cost{q}", "output": f"{base}/output", "approvals": f"{base}/approvals", "inbox": "/inbox"}
    public_runner = {k: v for k, v in runner.items() if not k.startswith("_")}
    return {"case_id": case.id, "title": case.title, "runner": public_runner, "run_id": rid, "n_runs": len(run_ids),
            "run_state": status.get("state"), "phase": status.get("phase"),
            "checkpoint_pending": status.get("checkpoint_pending"), "run_reason": status.get("reason"),
            "moving": moving, "pending_approvals": case.pending, "lake_error": case.lake_error,
            "liveness": lv, "throughput": thr, "failures": fails, "spend": spend, "dod": dp, "links": links}


# attention ----------------------------------------------------------------------------------------------------------
def attention(rover: dict, cases: list[dict], now: datetime) -> list[dict]:
    out: list[dict] = []

    def add(sev: int, case_id, text: str, href: str):
        level, state = SEVERITY[sev]
        out.append({"severity": sev, "level": level, "state": state, "case_id": case_id, "text": text, "href": href})

    if rover["killed"]:
        add(1, None, "Kill switch is on: nothing launches and running engines are stopped", "/")
    if not rover["state_dir_ok"]:
        add(1, None, f"Runner state not found at {rover['state_dir']}: runner status unknown", "/")
    for c in cases:
        cid, L, lv, r = c["case_id"], c["links"], c["liveness"], c["runner"]
        if lv["stale"]:
            add(1, cid, f"STALE: runner says running, no step for {human_age(lv['quiet_s'])} "
                        f"(threshold {lv['stale_min']:g} min)", L["run"])
        if r["state"] in CRITICAL:
            why = r["reason"] or (r["last_event"] or {}).get("detail") or ""
            add(1, cid, f"Runner {r['state'].replace('_', ' ')}" + (f": {why}" if why else ""),
                L["cost"] if r["state"] == "budget_stop" else L["run"] if r["state"] == "failed" else "/inbox")
        elif c["run_state"] == "failed":
            add(1, cid, "Run failed" + (f": {c['run_reason']}" if c["run_reason"] else ""), L["run"])
        if c["lake_error"]:
            add(1, cid, f"Lake unreachable: {c['lake_error']}", L["case"])
        if r["state"] == "waiting_approval" or (c["run_state"] == "paused" and c["checkpoint_pending"]):
            cp = r["checkpoint"] or c["checkpoint_pending"] or "checkpoint"
            add(2, cid, f"Waiting for a person: the {cp} decision (the run resumes by itself after it)", "/inbox")
        elif c["pending_approvals"]:
            add(2, cid, f"{c['pending_approvals']} approval{'s' if c['pending_approvals'] != 1 else ''} pending", L["approvals"])
        f = c["failures"]
        if f["rising"]:
            add(3, cid, f"Failure rate rising: {f['rate_per_100_15']} per 100 steps in the last 15 min vs "
                        f"{f['rate_per_100']} over the run", L["failures"])
        for src in f["only_failures"]:
            add(3, cid, f"Source {src}: every step failed", L["failures"])
        s = c["spend"]
        pr = s["case_projection"]
        if s["burn_usd_per_h"] and pr["hours"] is not None and pr["hours"] <= PROJECTION_ALERT_H:
            add(4, cid, f"Case cap {s['case_cap_usd']:.2f} USD projected at {pr['at'][11:16]} UTC "
                        f"(burn {s['burn_usd_per_h']:.2f} USD/h)", L["inference"])
        d = c["dod"]
        if d["n_criteria"] and d["n_met"] < d["n_criteria"] and (c["moving"] or c["run_state"] == "done"):
            low = min((x for x in d["criteria"] if not x["met"]), key=lambda x: x["v"] or 0)
            add(5, cid, f"DoD {d['n_met']}/{d['n_criteria']} criteria met; furthest: {low['label']} "
                        f"{low['actual'] if low['actual'] is not None else '—'} / {low['target']}", L["output"])
    g = next((c["spend"] for c in cases if c["spend"]["global_cap_usd"] is not None
              and c["spend"]["global_usd"] is not None), None)
    gb = sum(c["spend"]["burn_usd_per_h"] or 0 for c in cases)
    if g and gb:
        pr = projection(g["global_usd"], g["global_cap_usd"], gb, now)
        if pr["hours"] is not None and pr["hours"] <= PROJECTION_ALERT_H:
            add(4, None, f"Global cap {g['global_cap_usd']:.2f} USD projected at {pr['at'][11:16]} UTC "
                         f"(burn {gb:.2f} USD/h across cases)", "/inference")
    out.sort(key=lambda i: (i["severity"], i["case_id"] or ""))
    return out


# the page -----------------------------------------------------------------------------------------------------------
def model(settings, now: datetime | None = None, stale_min: float = STALE_MIN, env: dict | None = None) -> dict:
    now = now or datetime.now(UTC)
    env = dict(os.environ if env is None else env)
    root = Path(getattr(settings, "runner_state", runner_state.DEFAULT_STATE))
    rover = runner_overview(root, now)
    cases = [case_model(c, root, rover, now, stale_min, env) for c in settings.cases.values()]
    g = [c["spend"] for c in cases]
    gl_usd = max((s["global_usd"] for s in g if s["global_usd"] is not None), default=None)
    gl_basis = "runner spent_usd_global"
    if gl_usd is None and any(s["case_usd"] is not None for s in g):
        # the runner writes its global figure only when it launches a run; until then, the sum of every case's
        # priced trace steps (the same measure the runner uses per case), labelled as such
        gl_usd, gl_basis = round(sum(s["case_usd"] or 0 for s in g), 6), "sum of the cases' traces (runner not reporting)"
    gl_cap = next((s["global_cap_usd"] for s in g if s["global_cap_usd"] is not None), None)
    for s in g:  # the gateway-wide figure is one number: the newest the runner reported for any case
        s["global_usd"] = gl_usd
        s["global_cap_usd"] = s["global_cap_usd"] if s["global_cap_usd"] is not None else gl_cap
    items = attention(rover, cases, now)
    gl_burn = round(sum(s["burn_usd_per_h"] or 0 for s in g), 4) or None
    newest = max((t for c in cases for t in (parse_ts(c["liveness"]["last_step_at"]),
                                            parse_ts(c["liveness"]["status_updated_at"])) if t and c["moving"]),
                 default=None)
    lm = live_marker(any(c["moving"] for c in cases), _iso(newest))
    if lm["on"]:
        lm["poll_ms"] = POLL_MS
    runner = {k: v for k, v in rover.items() if k != "events"}
    return {"generated_at": _iso(now), "stale_min": stale_min, "live": lm, "runner": runner,
            "global_spend": {"global_usd": gl_usd, "global_basis": gl_basis if gl_usd is not None else None,
                             "global_cap_usd": gl_cap, "burn_usd_per_h": gl_burn,
                             "projection": projection(gl_usd, gl_cap, gl_burn, now)},
            "cases": cases, "attention": items, "n_attention": len(items),
            "n_stale": sum(1 for c in cases if c["liveness"]["stale"]),
            "n_running": sum(1 for c in cases if c["moving"])}


def install(ctx: VizContext) -> None:
    app, render, settings = ctx.app, ctx.render, ctx.settings
    ctx.global_view(KEY, HREF, LABEL, ORDER)

    @app.get(HREF, response_class=HTMLResponse)
    def watch_page(request: Request, stale_min: float = Query(STALE_MIN, ge=0.5, le=1440)):
        return render(request, "viz/watch.html", nav=KEY, m=model(settings, stale_min=stale_min), sources=SOURCES)

    @app.get("/api/watch")
    def watch_api(stale_min: float = Query(STALE_MIN, ge=0.5, le=1440)) -> dict:
        return model(settings, stale_min=stale_min)
