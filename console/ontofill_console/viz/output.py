"""Q6 · How close is it to done, and what is it doing about the gaps?

Reads the gold export of one run (`entities.jsonl`, `metrics.json`, `ontology.json`, `dod-queries.json`), every gold
run's metrics for the growth chart, and the run's live trace for the outer gap loop's reopen decisions. Shows DoD
progress, a completeness heatmap (entities × DoD properties, each cell a link to that value's evidence), gold growth
over runs, sources × properties coverage, conflicts kept, and the raw gold files for download.
"""

from __future__ import annotations

import os
import re

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from .. import dod, live
from .core import Artifacts, VizContext, gap
from .output_common import STATE_LABELS, brief_text, domain_for, q, resolve_run, value_state

ORDER = 50
ROW_CAP = 60
GROWTH_CAP = 40
EXPORTS = {"entities.jsonl": "application/x-ndjson", "metrics.json": "application/json",
           "ontology.json": "application/json", "dod-queries.json": "application/json",
           "trace.jsonl": "application/x-ndjson"}
SOURCES = ("gold/<case>/<run>/entities.jsonl · metrics.json · ontology.json · dod-queries.json · "
           "runs/<case>/<run>/trace.live.jsonl")


# DoD progress ------------------------------------------------------------------------------------------------------
def dod_rows(run, domain, backend) -> list[dict]:
    recomputed = dod.compute(run.entities, domain)
    rows = []
    for c in dod.criteria(recomputed, run.metrics, domain, backend, run.dod_queries, run.entities):
        actual = c["actual"] if c["actual"] is not None else c["engine_actual"]
        target = c["target"]
        nums = [x for x in (actual, target) if isinstance(x, (int, float))]
        scale = max([*nums, 1])
        met = c["met"]
        rows.append({**c, "shown": actual,
                     "v": round(100 * (actual or 0) / scale, 1) if isinstance(actual, (int, float)) else 0,
                     "t": round(100 * target / scale, 1) if isinstance(target, (int, float)) else None,
                     "state": "pause" if c["mock"] else ("done" if met else "run")})
    return rows


# completeness heatmap ------------------------------------------------------------------------------------------
def heatmap(case_id: str, run, domain, show_all: bool) -> dict:
    primary = [e for e in run.entities if e.get("class") == domain.primary_class]
    cols = domain.dod_props()
    dod_cols = bool(cols)
    if not cols:  # an inferred ontology marks nothing as DoD: show every property of the primary class
        cols = domain.props()
    rows = []
    for e in primary:
        values = e.get("properties") or {}
        cells = []
        for p in cols:
            f = values.get(p.id)
            state = value_state(f)
            cells.append({"prop": p.id, "state": state, "value_id": (f or {}).get("value_id") if isinstance(f, dict) else None,
                          "href": f"/cases/{case_id}/entities/{q(e['id'])}?run={q(run.run_id)}#p-{p.id}"})
        filled = sum(c["state"] == "gold" for c in cells)
        rows.append({"entity_id": e["id"], "title": run.title(e), "identifier": run.identifier(e),
                     "ratio": round(filled / len(cols), 4) if cols else 0.0, "filled": filled, "cells": cells,
                     "href": f"/cases/{case_id}/entities/{q(e['id'])}?run={q(run.run_id)}"})
    rows.sort(key=lambda r: (-r["ratio"], str(r["title"]).lower()))
    n = len(rows)
    columns = []
    for i, p in enumerate(cols):
        states = [r["cells"][i]["state"] for r in rows]
        columns.append({"id": p.id, "label": p.label, "gold": states.count("gold"), "weak": states.count("weak"),
                        "conflict": states.count("conflict"), "missing": states.count("missing"),
                        "ratio": round(states.count("gold") / n, 4) if n else 0.0})
    return {"class_label": domain.class_label(plural=True), "columns": columns, "dod_columns": dod_cols,
            "rows": rows if show_all else rows[:ROW_CAP], "n_rows": n, "capped": not show_all and n > ROW_CAP,
            "threshold": domain.dod_threshold, "threshold_stated": domain.threshold_stated,
            "meeting": sum(r["ratio"] >= domain.dod_threshold - 1e-9 for r in rows)}


# gold growth over runs -----------------------------------------------------------------------------------------
def growth(a: Artifacts) -> dict:
    ids = a.gold_run_ids()[-GROWTH_CAP:]
    points = []
    for rid in ids:
        r = a.gold(rid)
        if r is None:
            continue
        d = domain_for(r)
        cls = d.primary_class
        m = r.metrics or {}
        total = (m.get("entities_total") or {}).get(cls)
        meeting = (m.get("entities_meeting_dod") or {}).get(cls)
        if total is None or meeting is None:
            rc = dod.compute(r.entities, d)
            total = rc["entities_total"].get(cls, 0) if total is None else total
            meeting = rc["entities_meeting_dod"].get(cls, 0) if meeting is None else meeting
        points.append({"run_id": rid, "entities_total": int(total or 0), "meeting_dod": int(meeting or 0),
                       "backend": r.inference_backend})
    return {"runs": points, "n_runs": len(points), "capped": len(a.gold_run_ids()) > GROWTH_CAP,
            "chart": chart(points) if points else None}


def chart(points: list[dict]) -> dict:
    """Plot geometry for a two-series step chart (viewBox 0 0 640 240; plot area x 56..616, y 16..184)."""
    x0, x1, y0, y1 = 56, 616, 184, 16
    top = max(max(p["entities_total"] for p in points), 1)
    n = len(points)

    def x(i):
        return round(x0 + (x1 - x0) * (i / (n - 1) if n > 1 else 0.5), 1)

    def y(v):
        return round(y0 - (y0 - y1) * v / top, 1)

    def path(key):
        d = []
        for i, p in enumerate(points):
            if i == 0:
                d.append(f"M{x(i)},{y(p[key])}")
            else:
                d.append(f"H{x(i)}V{y(p[key])}")
        return " ".join(d)

    ticks = sorted({0, top, round(top / 2)})
    return {"top": top, "x0": x0, "x1": x1, "y0": y0, "y1": y1,
            "yticks": [{"v": t, "y": y(t)} for t in ticks],
            "series": [{"key": "entities_total", "label": "found", "path": path("entities_total"), "cls": "s-total",
                        "pts": [{"x": x(i), "y": y(p["entities_total"]), "v": p["entities_total"], "run": p["run_id"]}
                                for i, p in enumerate(points)]},
                       {"key": "meeting_dod", "label": "meeting the DoD", "path": path("meeting_dod"), "cls": "s-dod",
                        "pts": [{"x": x(i), "y": y(p["meeting_dod"]), "v": p["meeting_dod"], "run": p["run_id"]}
                                for i, p in enumerate(points)]}],
            "xlabels": [{"x": x(i), "run": p["run_id"], "short": short} for i, (p, short) in enumerate(
                zip(points, short_labels([p["run_id"] for p in points]), strict=True))]}


def short_labels(ids: list[str]) -> list[str]:
    """Run ids without their shared prefix (whole ids when there is one run or nothing is shared)."""
    if len(ids) < 2:
        return ids
    prefix = os.path.commonprefix(ids)
    cut = max(prefix.rfind("-"), prefix.rfind("_"), prefix.rfind(":")) + 1
    return [(i[cut:] or i)[-14:] for i in ids]


# sources × properties ------------------------------------------------------------------------------------------
def coverage(run, domain) -> dict:
    counts: dict[str, dict[str, int]] = {}
    types: dict[str, str] = {}
    seen_props: dict[str, None] = {}
    for e in run.entities:
        for name, f in (e.get("properties") or {}).items():
            if not isinstance(f, dict):
                continue
            for sid in {ev.get("source_id") for ev in f.get("evidence") or [] if ev.get("source_id")}:
                counts.setdefault(sid, {})
                counts[sid][name] = counts[sid].get(name, 0) + 1
                seen_props[name] = None
            for ev in f.get("evidence") or []:
                if ev.get("source_id") and ev.get("source_type"):
                    types.setdefault(ev["source_id"], ev["source_type"])
    order = {p.id: i for i, p in enumerate(p for ps in domain.properties.values() for p in ps)}
    props = sorted(seen_props, key=lambda p: (order.get(p, 10**6), p))
    top = max((n for row in counts.values() for n in row.values()), default=0)
    rows = []
    for sid in sorted(counts, key=lambda s: -sum(counts[s].values())):
        cells = []
        for p in props:
            n = counts[sid].get(p, 0)
            cells.append({"prop": p, "n": n, "lvl": 0 if not n else (3 if 2 * n > top else 1)})
        rows.append({"source_id": sid, "source_type": types.get(sid), "source_label": domain.source_label(types.get(sid)),
                     "cells": cells, "total": sum(counts[sid].values())})
    return {"columns": [{"id": p, "label": domain.prop_label(p)} for p in props], "rows": rows, "top": top}


# conflicts and gap-loop decisions ------------------------------------------------------------------------------
def conflicts(run, domain) -> list[dict]:
    out: dict[str, dict] = {}
    for e in run.entities:
        if e.get("class") != domain.primary_class:
            continue
        for name, f in (e.get("properties") or {}).items():
            if not isinstance(f, dict):
                continue
            row = out.setdefault(name, {"prop": name, "label": domain.prop_label(name), "conflict": 0, "gold": 0,
                                        "multi_source": 0, "missing": 0})
            st = f.get("status")
            if st in ("conflict", "gold", "missing"):
                row[st] += 1
            if st == "gold" and len({ev.get("source_id") for ev in f.get("evidence") or []}) > 1:
                row["multi_source"] += 1
    order = {p.id: i for i, p in enumerate(domain.props())}
    return sorted(out.values(), key=lambda r: (order.get(r["prop"], 10**6), r["prop"]))


def gap_decisions(case_id: str, steps: list[dict], run_id: str | None) -> list[dict]:
    rows = []
    for s in steps:
        if not live.is_reopen_marker(s):
            continue
        d = s.get("detail") or {}
        reopen = d.get("reopen")
        rows.append({"step_id": s.get("step_id"), "iteration": d.get("iteration"), "verdict": d.get("verdict"),
                     "reopen": reopen, "reopen_label": f"reopen phase {reopen} · {live.phase_name(reopen)}"
                     if reopen is not None else "no reopen", "stop_reason": d.get("stop_reason"),
                     "stop_label": live.STOP_LABELS.get(d.get("stop_reason"), d.get("stop_reason")),
                     "reason": d.get("reason"), "ts": s.get("ts"),
                     "state": "need" if reopen is not None else "done",
                     "href": f"/cases/{case_id}/runs/{run_id}#{s.get('step_id')}" if run_id else None})
    return rows


# view-model ------------------------------------------------------------------------------------------------------
def model(case, run: str | None = None, show_all: bool = False) -> dict:
    a = Artifacts(case)
    g, run_id, note = resolve_run(a, run)
    live_id = run_id if run_id in a.run_ids() else a.latest_run_id() if not run else None
    steps = a.steps(live_id)
    base = {"case_id": case.id, "run_id": run_id, "gold_run_ids": a.gold_run_ids(), "brief": brief_text(a),
            "live_run_id": live_id, "gap_decisions": gap_decisions(case.id, steps, live_id),
            "reopened": live.loop_summary(steps, None)["reopened"] if steps else []}
    base["empty_gap"] = None if base["gap_decisions"] else gap(
        None, "Each outer-loop decision (reopen a phase, or stop and why) as the run closes its gaps.",
        "runs/<case>/<run>/trace.live.jsonl (event loop, phase outer, role decide)")
    if g is None:
        what = ("Gold export for this run: DoD progress, the completeness heatmap, sources × properties and downloads."
                if note == "live-only" else
                "DoD progress toward each criterion, a completeness heatmap per entity and property, gold growth over "
                "runs, which sources back which properties, conflicts kept, and the raw gold files.")
        return {**base, "backend": None, "mock": False, "dod": [], "heat": None, "growth": growth(a),
                "coverage": None, "conflicts": [], "conflicts_kept": 0, "dod_met": 0, "exports": [],
                "primary_label": None, "empty_heat": None, "empty_cov": None,
                "empty": gap(None, what, "gold/<case>/<run>/entities.jsonl · metrics.json (written when a run "
                                         "publishes gold)")}
    domain = domain_for(g)
    backend = g.inference_backend
    exports = []
    for name in EXPORTS:
        raw = _read_export(case, run_id, name)
        if raw is not None:
            exports.append({"name": name, "bytes": len(raw),
                            "href": f"/cases/{case.id}/api/viz/output/export/{q(run_id)}/{name}"})
    heat = heatmap(case.id, g, domain, show_all)
    cov = coverage(g, domain)
    rows = dod_rows(g, domain, backend)
    conf = conflicts(g, domain)
    return {**base, "backend": backend, "mock": backend == "recorded", "primary_label": domain.class_label(plural=True),
            "dod": rows, "dod_met": sum(1 for r in rows if r["met"]), "heat": heat, "growth": growth(a),
            "coverage": cov, "conflicts": conf,
            "conflicts_kept": sum(r["conflict"] for r in conf), "exports": exports,
            "empty": None,
            "empty_heat": None if heat["rows"] else gap("R2", f"One row per {domain.class_label().lower()} with a cell "
                                                              "per DoD property.", "gold/<case>/<run>/entities.jsonl"),
            "empty_cov": None if cov["rows"] else gap("R2", "Counts of values per source and property, from each "
                                                            "value's evidence.", "entities.jsonl evidence[].source_id")}


def _read_export(case, run_id: str, name: str) -> bytes | None:
    store = case.store
    if name not in EXPORTS or not hasattr(store, "source"):
        return None
    try:
        return store.source.read(f"{store.prefix}/{run_id}/{name}")
    except (LookupError, OSError, ValueError):
        return None


def install(ctx: VizContext) -> None:
    app, render = ctx.app, ctx.render
    ctx.case_view("output", "output", "Output", 50)

    @app.get("/cases/{case_id}/output", response_class=HTMLResponse)
    def output_page(request: Request, case_id: str, run: str | None = None, all: int = 0):
        case = ctx.get_case(case_id)
        m = model(case, run, bool(all))
        return render(request, "viz/output.html", nav="output", case=case, m=m, backend=m["backend"],
                      STATE_LABELS=STATE_LABELS)

    @app.get("/cases/{case_id}/api/viz/output")
    def output_api(case_id: str, run: str | None = None, all: int = 0) -> dict:
        return model(ctx.get_case(case_id), run, bool(all))

    @app.get("/cases/{case_id}/api/viz/output/export/{run_id}/{name}")
    def output_export(case_id: str, run_id: str, name: str):
        case = ctx.get_case(case_id)
        if name not in EXPORTS:
            raise HTTPException(404, "not an exported gold file")
        if run_id not in Artifacts(case).gold_run_ids():
            raise HTTPException(404, f"no gold run {run_id}")
        raw = _read_export(case, run_id, name)
        if raw is None:
            raise HTTPException(404, f"{name} not in gold run {run_id}")
        return Response(raw, media_type=EXPORTS[name], headers={
            "Content-Disposition": f'attachment; filename="{case.id}-{re.sub(r"[^A-Za-z0-9._-]", "_", run_id)}-{name}"',
            "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})
