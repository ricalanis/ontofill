"""Q1 (per case), Q6 and Q8 in part · Operation: every run of a case, and for one run the P1–P5 pipeline (state, steps,
time, cost, outer-loop reopens), the phase loop threads (propose → critique → revise → check, objections and how they
were resolved, stop reason) and who decided each step (code, Jev, Vultr) and in which mode (D0/D1/S1/S2).

Reads `runs/<case>/<run>/status.json`, `trace.live.jsonl` (loop steps, `usage`, `generated_by`), `jobs.jsonl`
(live-view links) and `metrics.loops`. Works on the latest run and on any recorded run via `?run=<id>`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import live
from ..gold import backend_of
from .core import Artifacts, VizContext, age, gap, parse_ts

ORDER = 10
KEY, SLUG, LABEL = "operation", "operation", "Operation"
SOURCES = "runs/<case>/<run>/status.json · trace.live.jsonl · jobs.jsonl · metrics.loops"

CP_PHASE = {**live.CHECKPOINT_PHASE, "action": 5}
STATE_OF_RUN = {"running": "run", "paused": "pause", "failed": "block", "done": "done"}
PHASE_CSS = {"done": "done", "current": "run", "paused": "pause", "failed": "block", "pending": "none"}
DECIDERS = [  # key, label, colour token
    ("code", "Code", "--st-done"), ("jev", "Jev", "--st-run"), ("vultr", "Vultr", "--st-need"),
    ("recorded", "Recorded (simulated)", "--st-pause"), ("human", "Person", "--fg-2"), ("other", "Other", "--st-quar")]
MODE_CSS = {"D0": "--st-done", "D1": "--st-run", "S1": "--st-need", "S2": "--st-quar", "none": "--line-strong"}
ROLE_COLS = ("propose", "critique", "revise", "check")
LIVE_WINDOW_S = 120  # a run whose status moved in the last 2 minutes (and is not done/failed) counts as in motion
LIVE_POLL_MS = 2500  # static/viz-live.js re-fetches the page this often while it carries data-live="1"


# helpers ------------------------------------------------------------------------------------------------------------
def safe_url(url) -> str | None:
    return str(url) if url and urlsplit(str(url)).scheme in ("http", "https") else None


def step_usd(s: dict) -> float | None:
    """Estimated cost of one step, if the trace carries it (`usage.est_usd`, CONTRACT §15; older `usd`/`cost_usd`)."""
    usage = s.get("usage") if isinstance(s.get("usage"), dict) else {}
    if str(usage.get("model")) == "none" and not usage.get("input_tokens") and not usage.get("output_tokens"):
        return None  # a loop stage without a model call; its evaluated.usd is the loop's running total, not its own
    for v in (usage.get("est_usd"), usage.get("usd"), s.get("cost_usd"), (s.get("detail") or {}).get("usd")):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


def usage_era(steps: list[dict]) -> bool:
    """CONTRACT v1.0.5: when any step carries `usage`, every model call is its own step with usage (loop stages,
    `decision.complete_json`, controller plan/verify), and `generated_by` elsewhere is only the run's provenance."""
    return any(isinstance(s.get("usage"), dict) and s["usage"] for s in steps)


def decided_by(s: dict, priced_trace: bool = False) -> str:
    """Which backend made this step's decision. A step with no model behind it was decided by code."""
    d = s.get("detail") or {}
    usage = s.get("usage") if isinstance(s.get("usage"), dict) else {}
    gen = s.get("generated_by") if isinstance(s.get("generated_by"), dict) else {}
    if s.get("kind") == "loop":
        if d.get("human"):
            return "human"
        model = usage.get("model") or d.get("model")  # a stage without a model call (model "none") is code
        backend = (usage.get("backend") or gen.get("backend")) if model and model != "none" else None
    elif s.get("kind") == "gate" and d.get("decided_by"):  # the gate says who rated the action
        backend = d.get("decided_by")
    elif d.get("backend"):
        backend = d.get("backend")
    elif usage.get("model") or usage.get("backend"):
        backend = usage.get("backend") or gen.get("backend")
    elif gen.get("model") and s.get("mode") != "D0" and not priced_trace:
        backend = gen.get("backend")
    else:
        backend = None
    backend = str(backend or "code").lower()
    if backend in ("code", "none", "deterministic"):
        return "code"
    return backend if backend in ("jev", "vultr", "recorded", "human") else "other"


def duration(secs: float | None) -> str | None:
    if secs is None:
        return None
    secs = int(round(secs))
    if secs < 90:
        return f"{secs} s"
    if secs < 5400:
        return f"{secs // 60} min {secs % 60:02d} s"
    return f"{secs // 3600} h {(secs % 3600) // 60:02d} min"


def share(counts: dict[str, int], usd: dict[str, float], order, total: int) -> list[dict]:
    rows = []
    for key, label, css in order:
        n = counts.get(key, 0)
        if n:
            rows.append({"key": key, "label": label, "n": n, "pct": round(100 * n / total, 1) if total else 0,
                         "usd": round(usd[key], 4) if key in usd else None, "css": css})
    return rows


def last_activity(status: dict, steps: list[dict]) -> str | None:
    """The newest of status.updated_at and the last step's ts: what the live indicator calls "updated"."""
    stamps = [(t, raw) for raw in (status.get("updated_at"), *(s.get("ts") for s in steps[-5:]))
              if (t := parse_ts(raw))]
    return max(stamps)[1] if stamps else None


def is_live(status: dict, now: datetime | None = None) -> bool:
    """In motion: state running, or updated within LIVE_WINDOW_S and not done/failed. Finished runs stay static."""
    state = status.get("state")
    if state == "running":
        return True
    if state in ("done", "failed"):
        return False
    ts = parse_ts(status.get("updated_at"))
    return bool(ts and ((now or datetime.now(UTC)) - ts).total_seconds() <= LIVE_WINDOW_S)


def live_marker(on: bool, updated_at: str | None) -> dict:
    """What templates/viz/_macros.html live_indicator renders; `on` makes the page poll itself (static/viz-live.js)."""
    t = parse_ts(updated_at)
    return {"on": on, "updated_at": updated_at, "hhmmss": t.astimezone(UTC).strftime("%H:%M:%S") if t else None,
            "poll_ms": LIVE_POLL_MS if on else None}


# view-model pieces ----------------------------------------------------------------------------------------------------
def run_strips(a: Artifacts, case_id: str, selected: str | None, now: datetime) -> list[dict]:
    latest = a.latest_run_id()
    rows = []
    for rid in a.run_ids():
        st = a.status(rid)
        metrics = st.get("metrics") if isinstance(st.get("metrics"), dict) else {}
        backend = backend_of(metrics, [st])
        replay = bool(st.get("replay_of") or st.get("replayed_from") or metrics.get("replay_of") or "-replay-" in rid)
        state = st.get("state") or "no status yet"
        phase = st.get("phase")
        kind = "live" if state == "running" else ("replay" if replay else ("recorded" if backend == "recorded" else "run"))
        detail = [f"phase {phase} · {live.phase_name(phase)}" if phase else None,
                  f"paused at {st['checkpoint_pending']}" if st.get("checkpoint_pending") else None,
                  str(st["reason"]) if st.get("reason") else None,  # run-status.schema.json: why it paused or failed
                  "live" if kind == "live" else None, "replayed feed" if replay else None,
                  "recorded inference (simulated)" if backend == "recorded" else (f"inference: {backend}" if backend else None),
                  "latest" if rid == latest else None]
        rows.append({"run_id": rid, "state": STATE_OF_RUN.get(state, "none"), "status": state, "kind": kind,
                     "live": kind == "live", "replay": replay, "recorded": backend == "recorded", "backend": backend,
                     "latest": rid == latest, "selected": rid == selected, "since": st.get("updated_at"),
                     "when": age(st.get("updated_at"), now), "detail": " · ".join(x for x in detail if x),
                     "href": f"/cases/{case_id}/{SLUG}?run={quote(rid)}"})
    latest_rows = [r for r in rows if r["latest"]]
    rest = sorted((r for r in rows if not r["latest"]), key=lambda r: (r["since"] or "", r["run_id"]), reverse=True)
    return latest_rows + rest


def pipeline(steps: list[dict], status: dict, reopened: dict) -> list[dict]:
    state = status.get("state") or ("running" if steps else "waiting")
    current = status.get("phase") or max((s.get("phase") or 1 for s in steps if isinstance(s.get("phase"), int)),
                                         default=1)
    cp = status.get("checkpoint_pending")
    out = []
    for n, name in live.PHASES:
        mine = [s for s in steps if s.get("phase") == n]
        stamps = [t for t in (parse_ts(s.get("ts")) for s in mine) if t]
        costs = [c for c in (step_usd(s) for s in mine) if c is not None]
        if state == "done" or (isinstance(current, int) and n < current):
            ph = "done"
        elif n == current:
            ph = {"paused": "paused", "failed": "failed"}.get(state, "current")
        else:
            ph = "pending"
        secs = (max(stamps) - min(stamps)).total_seconds() if len(stamps) > 1 else (0.0 if stamps else None)
        out.append({"n": n, "name": name, "state": ph, "css": PHASE_CSS[ph], "current": n == current and state != "done",
                    "steps": len(mine), "elapsed_s": secs, "elapsed": duration(secs),
                    "usd": round(sum(costs), 4) if costs else None, "priced_steps": len(costs),
                    "checkpoint": cp if cp and CP_PHASE.get(cp) == n else None,
                    "reopened": reopened.get(n, 0)})
    return out


def _resolution(thread: dict, idx: int, it: dict) -> str:
    """How the objections raised in iteration `it` were answered: a revision in the same iteration, the critic
    accepting the next draft, or the loop's stop reason."""
    for x in it["steps"]:
        d = x["detail"]
        if d["role"] == "revise":
            if d["human"]:
                return "revised after a person's reason" + (f": {d['reason']}" if d.get("reason") else "")
            return "revised" + (f" by {d['model']}" if d.get("model") else "")
    for later in thread["iterations"][idx + 1:]:
        for x in later["steps"]:
            d = x["detail"]
            if d["role"] == "critique":
                v = str(d.get("verdict") or "").replace("_", " ")
                return f"iteration {later['n']} critique: {v or 'no verdict'}" + (
                    f", {len(d['objections'])} new objection(s)" if d["objections"] else "")
    return f"open; loop stopped: {thread['stop_label']}" if thread.get("stop_label") else "open"


def _cell(role: str, members: list[dict]) -> dict:
    if not members:
        return {"role": role, "text": "—", "cls": "none"}
    d = members[-1]["detail"]
    verdict = str(d.get("verdict") or "").lower()
    model = d.get("model")
    if role == "critique":
        objs = sum(len(x["detail"]["objections"]) for x in members)
        if objs:
            return {"role": role, "text": f"critic: {objs} objection{'s' if objs != 1 else ''}", "cls": "obj"}
        return {"role": role, "text": f"critic: {verdict.replace('_', ' ') or 'no objections'}",
                "cls": "obj" if verdict in ("rejected", "reject") else "ok"}
    if role == "revise":
        return {"role": role, "text": "revised by a person" if d["human"] else ("revise · " + model if model else "revise"),
                "cls": "ok"}
    if role == "check":
        ok = verdict in ("pass", "passed", "met", "ok", "completed", "accepted")
        return {"role": role, "text": f"check {verdict.replace('_', ' ') or 'run'}", "cls": "ok" if ok else "fail"}
    failed = verdict in ("failed", "fail", "error")
    return {"role": role, "text": role + (f" · {model}" if model else ""), "cls": "fail" if failed else "ok"}


def threads_model(steps: list[dict]) -> list[dict]:
    out = []
    for t in live.loop_threads(steps):
        its = []
        for idx, it in enumerate(t["iterations"]):
            by_role: dict[str, list[dict]] = {}
            for x in it["steps"]:
                by_role.setdefault(x["detail"]["role"], []).append(x)
            objections = [o for x in by_role.get("critique", []) for o in x["detail"]["objections"]]
            res = _resolution(t, idx, it) if objections else None
            stop = next((x["detail"]["stop_reason"] for x in it["steps"] if x["detail"].get("stop_reason")), None)
            its.append({"n": it["n"], "cells": [_cell(r, by_role.get(r, [])) for r in ROLE_COLS],
                        "gathered": len(by_role.get("gather", [])),
                        "objections": [{"text": o, "resolution": res} for o in objections],
                        "stop": live.STOP_LABELS.get(stop, stop) if stop else None,
                        "step_id": it["steps"][0].get("step_id")})
        out.append({"id": t["id"], "label": t["label"], "phase": t["phase"], "n_iterations": len(its),
                    "n_objections": t["objections"], "stop_reason": t["stop_reason"],
                    "stop_label": t["stop_label"] or "still running", "usd": t["usd"], "iterations": its})
    return out


def reopen_markers(steps: list[dict]) -> list[dict]:
    rows = []
    for s in steps:
        if live.is_reopen_marker(s):
            d = s["detail"]
            rows.append({"iteration": d["iteration"], "reopen": d.get("reopen"),
                         "phase_name": live.phase_name(d["reopen"]) if d.get("reopen") is not None else None,
                         "reason": d.get("reason"),
                         "stop": live.STOP_LABELS.get(d["stop_reason"], d["stop_reason"]) if d.get("stop_reason") else None,
                         "step_id": s.get("step_id"), "ts": s.get("ts")})
    return rows


def live_view(status: dict, jobs: list[dict]) -> list[dict]:
    """The agent's browser, published read-only: status.live_view_url (the engine copies it from session.open) and any
    per-job link a cell reported."""
    links, seen = [], set()
    cands = [("run", status.get("live_view_url"))]
    for j in jobs:
        sess = j.get("session") if isinstance(j.get("session"), dict) else {}
        cands.append((j.get("source_id") or j.get("job_id") or "cell", j.get("live_view_url") or sess.get("live_view_url")))
    for label, url in cands:
        url = safe_url(url)
        if url and url not in seen:
            seen.add(url)
            links.append({"label": label, "url": url})
    return links


def model(case, run_id: str | None = None) -> dict:
    a = Artifacts(case)
    now = datetime.now(UTC)
    base = f"/cases/{case.id}"
    ids = a.run_ids()
    if run_id is not None and run_id not in ids:
        raise HTTPException(404, f"no live feed for run {run_id}")
    rid = run_id or a.latest_run_id() or (ids[-1] if ids else None)
    strips = run_strips(a, case.id, rid, now)
    m: dict = {"case_id": case.id, "question": case.brief, "run_id": rid, "runs": strips, "n_runs": len(strips),
               "run_href": f"{base}/runs/{quote(rid)}" if rid else None, "sources": SOURCES}
    if not rid:
        why = case.lake_error or "no run feed under runs/<case>/ yet"
        m.update(live=live_marker(False, None), selected=None, pipe=[], threads=[], reopens=[], deciders=[], modes=[], live_view=[], backend=None,
                 n_steps=0, usd_total=None, priced_steps=0, cost_empty=None, threads_empty=None,
                 empty=gap(None, f"Runs of this case, their P1–P5 pipeline, loop threads and who decided each step. "
                                 f"The engine has not published a run feed for this case ({why}).",
                           "runs/<case>/latest.json · runs/<case>/<run>/status.json · trace.live.jsonl"))
        return m
    status = a.status(rid)
    steps = a.steps(rid)
    jobs = a.jobs(rid)
    metrics = status.get("metrics") if isinstance(status.get("metrics"), dict) else {}
    summary = live.loop_summary(steps, metrics)
    reopened = {k: n for k, n in summary["reopened"]}
    deciders_n: dict[str, int] = {}
    deciders_usd: dict[str, float] = {}
    modes_n: dict[str, int] = {}
    modes_usd: dict[str, float] = {}
    priced_trace = usage_era(steps)
    for s in steps:
        who = decided_by(s, priced_trace)
        mode = s.get("mode") if s.get("mode") in live.MODE_RANK else "none"
        deciders_n[who] = deciders_n.get(who, 0) + 1
        modes_n[mode] = modes_n.get(mode, 0) + 1
        usd = step_usd(s)
        if usd is not None:
            deciders_usd[who] = deciders_usd.get(who, 0.0) + usd
            modes_usd[mode] = modes_usd.get(mode, 0.0) + usd
    priced = sum(1 for s in steps if step_usd(s) is not None)
    total_usd = round(sum(deciders_usd.values()), 4) if priced else None
    mode_order = [(k, f"{k} · {live.MODE_NAMES[k]}", MODE_CSS[k]) for k in live.MODE_RANK] + [("none", "no mode", MODE_CSS["none"])]
    threads = threads_model(steps)
    selected = next((r for r in strips if r["run_id"] == rid), None)
    m.update(
        live=live_marker(is_live(status, now), last_activity(status, steps)),
        selected=selected, n_steps=len(steps), backend=backend_of(metrics, [*steps, status]),
        state=status.get("state") or ("running" if steps else "waiting"), phase=status.get("phase"),
        checkpoint=status.get("checkpoint_pending"), updated_at=status.get("updated_at"),
        reason=str(status["reason"]) if status.get("reason") else None,
        pipe=pipeline(steps, status, reopened), reopens=reopen_markers(steps),
        loop_rows=summary["rows"], threads=threads,
        deciders=share(deciders_n, deciders_usd, DECIDERS, len(steps)),
        modes=share(modes_n, modes_usd, mode_order, len(steps)),
        usd_total=total_usd, priced_steps=priced, live_view=live_view(status, jobs),
        cost_empty=None if priced else gap(None, "Cost per phase, per decider and per mode, from each model step's "
                                                 "usage.est_usd. This run's trace carries no cost fields.",
                                           "trace.live.jsonl usage{model, backend, input_tokens, output_tokens, est_usd}"),
        threads_empty=None if threads else gap(None, "Loop threads (propose → critique → revise → check) appear when "
                                                     "a phase emits event: loop steps.", "trace.live.jsonl event loop"),
        empty=None if steps else gap("R2", "This run has a status but no steps in its trace yet.",
                                     f"runs/{case.id}/{rid}/trace.live.jsonl"),
    )
    return m


def install(ctx: VizContext) -> None:
    app, render = ctx.app, ctx.render
    ctx.case_view(KEY, SLUG, LABEL, 10)

    @app.get("/cases/{case_id}/" + SLUG, response_class=HTMLResponse)
    def operation(request: Request, case_id: str, run: str | None = None):
        case = ctx.get_case(case_id)
        m = model(case, run)
        return render(request, "viz/operation.html", nav=KEY, case=case, m=m, backend=m["backend"],
                      live_run_id=m["run_id"])

    @app.get("/cases/{case_id}/api/viz/" + SLUG)
    def operation_api(case_id: str, run: str | None = None) -> dict:
        return model(ctx.get_case(case_id), run)
