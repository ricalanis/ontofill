"""Morning summary · one page per case, after an unattended run: the question and the run (when, how long, why it
stopped, how often it waited for a person), what it produced, how close it is to done, what failed and was contained,
what it cost, a few representative values with their receipts, and what the engine would do next. Usable directly in a
demo: a plain-text rendering for a script or message, a JSON twin with stable keys, and print styles.

Built only from the other views' view-models (output, failures, cost, inference, pages, definition, operation) and the
runner's own events (read-only). Works live (partial numbers labelled "so far") and on any recorded run (`?run=<id>`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse

from .. import live, runner_state
from ..domain import Domain
from . import cost as cost_view
from . import failures as failures_view
from . import health_common as hc
from . import inference as inference_view
from . import output as output_view
from . import pages as pages_view
from .core import SAFE_ERRORS, Artifacts, VizContext, gap, parse_ts
from .definition import CP_LABELS, pending_strips
from .fmt import value_text
from .operation import duration, is_live, last_activity, live_marker, reopen_markers
from .output_common import brief_text, domain_for, evidence_view, q, value_state

ORDER = 2
KEY, SLUG, LABEL = "summary", "summary", "Summary"
SOURCES = ("brief.md · runs/<case>/<run>/status.json · trace.live.jsonl · jobs.jsonl · gold/<case>/<run>/entities.jsonl · "
           "metrics.json · bronze/sha256/<hex>.meta.json · */APPROVAL_PENDING.md · decisions.jsonl · runner events.jsonl · "
           "gateway call log")
MAX_RECEIPTS = 6
MAX_GOLD_RECEIPTS = 4
MAX_REASONS = 6
MAX_NEXT = 10
RUNNER_STOPS = ("budget_stop", "failed", "killed", "done")
FAIL_LABELS = {k: label for k, label, _ in failures_view.KINDS}
FAIL_STATES = {k: st for k, _, st in failures_view.KINDS}
CONTAINED = ("quarantine", "limit_kill", "blocked_domain", "gate")


def usd(x: float | None) -> str:
    """Money as the page and the plain text show it (same string in both)."""
    if x is None:
        return "—"
    return f"${x:.4f}" if x < 1 else f"${x:,.2f}"


def pct(x: float | None) -> str:
    return "—" if x is None else f"{round(100 * x)}%"


def _hhmm(ts: str | None) -> str | None:
    t = parse_ts(ts)
    return t.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if t else None


# 1 · the run --------------------------------------------------------------------------------------------------------
def runner_events(root: Path | None, case_id: str, rid: str | None) -> list[dict]:
    if root is None or not rid:
        return []
    try:
        if not Path(root).is_dir():
            return []
        evs = runner_state.events(Path(root), limit=5000)
    except SAFE_ERRORS:
        return []
    return [{"ts": e.get("ts"), "kind": e.get("kind"), "detail": (str(e.get("detail") or "").splitlines() or [None])[0],
             "checkpoint": e.get("checkpoint")}
            for e in evs if isinstance(e, dict) and e.get("case_id") == case_id and e.get("run_id") in (rid, None)
            and e.get("kind")]


def run_section(case, a: Artifacts, rid: str | None, steps: list[dict], status: dict, root: Path | None,
                now: datetime) -> dict | None:
    if not rid:
        return None
    stamps = sorted(t for t in (parse_ts(s.get("ts")) for s in steps) if t)
    started = stamps[0] if stamps else None
    ended = stamps[-1] if stamps else None
    secs = (ended - started).total_seconds() if started and ended else None
    live_on = is_live(status, now)
    has_feed = rid in a.run_ids()
    state = status.get("state") or ("running" if steps and has_feed and live_on else ("recorded" if not has_feed else None))
    cp = status.get("checkpoint_pending")
    evs = runner_events(root, case.id, rid)
    decisions = [d for d in a.decisions() if isinstance(d, dict) and d.get("run_id") == rid]
    runner_pauses = [e for e in evs if e["kind"] == "paused_at_checkpoint"]
    runner_resumes = [e for e in evs if e["kind"] == "resumed"]
    if runner_pauses or runner_resumes:
        pauses, resumes, basis = len(runner_pauses), len(runner_resumes), "runner events.jsonl"
    else:
        pauses = len(decisions) + (1 if cp else 0)
        resumes = sum(1 for d in decisions if (d.get("decision") or "approve") == "approve")
        basis = "decisions.jsonl · status.json"
    stop: list[str] = []
    if status.get("reason"):
        stop.append(str(status["reason"]))
    if cp:
        stop.append(f"waiting for the {CP_LABELS.get(cp, cp)} checkpoint: a person decides, then the runner resumes it")
    last_stop = next((e for e in reversed(evs) if e["kind"] in RUNNER_STOPS), None)
    if last_stop:
        stop.append(f"runner: {last_stop['kind'].replace('_', ' ')}" + (f" · {last_stop['detail']}" if last_stop["detail"] else ""))
    outer = [r for r in live.loop_summary(steps, hc.metrics_of(a, rid))["rows"] if r.get("phase") == "outer" and r.get("stop_label")]
    if outer:
        stop.append(f"gap loop stopped: {outer[-1]['stop_label']}")
    if state == "running" and not stop:
        stop.append("still running")
    phase = status.get("phase") or max((s.get("phase") for s in steps if isinstance(s.get("phase"), int)), default=None)
    return {"run_id": rid, "state": state or "no status", "phase": phase,
            "phase_name": live.phase_name(phase) if phase else None, "checkpoint": cp,
            "reason": str(status["reason"]) if status.get("reason") else None,
            "started_at": started.isoformat() if started else None, "ended_at": ended.isoformat() if ended else None,
            "started": _hhmm(started.isoformat()) if started else None, "ended": _hhmm(ended.isoformat()) if ended else None,
            "duration_s": secs, "duration": duration(secs), "updated_at": status.get("updated_at"),
            "n_steps": len(steps), "has_feed": has_feed, "stop": stop,
            "pauses": pauses, "resumes": resumes, "pause_basis": basis, "n_decisions": len(decisions),
            "runner_events": evs[-12:], "n_runner_events": len(evs),
            "href": f"/cases/{case.id}/runs/{quote(rid)}" if has_feed else None,
            "operation_href": f"/cases/{case.id}/operation?run={quote(rid)}" if has_feed else None}


# 2 · what was produced ----------------------------------------------------------------------------------------------
def produced_section(case, a: Artifacts, rid: str | None, g, domain: Domain | None, steps: list[dict], metrics: dict,
                     jobs: list[dict]) -> dict:
    by_class: list[dict] = []
    sources: set[str] = set()
    classes: set[str] = set()
    if g is not None:
        counts: dict[str, int] = {}
        for e in g.entities:
            counts[e.get("class") or "?"] = counts.get(e.get("class") or "?", 0) + 1
        n_values = 0
        for ref in g.values.values():
            if ref.data.get("status") == "gold":
                n_values += 1
            for ev in ref.data.get("evidence") or []:
                if isinstance(ev, dict):
                    if ev.get("source_id"):
                        sources.add(ev["source_id"])
                    if ev.get("source_type"):
                        classes.add(ev["source_type"])
        basis = "gold export"
    else:
        counts = {k: int(v) for k, v in (metrics.get("entities_total") or {}).items() if isinstance(v, (int, float))} \
            if isinstance(metrics.get("entities_total"), dict) else {}
        n_values = len({vid for s in steps for vid in s.get("value_ids") or []})
        basis = "status.json metrics · value_ids in the trace" if rid else None
    primary = domain.primary_class if domain else None
    for cls, n in sorted(counts.items(), key=lambda kv: (kv[0] != primary, -kv[1], kv[0])):
        by_class.append({"class": cls, "label": domain.class_label(cls, plural=True) if domain else cls, "n": n,
                         "primary": cls == primary})
    touched = {s.get("source_id") for s in steps if s.get("source_id")}
    pages = pages_view.collect(a, case.id, rid, steps, g) if rid else []
    return {"basis": basis, "entities_by_class": by_class, "n_entities": sum(r["n"] for r in by_class),
            "n_values": n_values, "sources": sorted(sources), "n_sources": len(sources),
            "source_classes": [{"id": c, "label": domain.source_label(c) if domain else c} for c in sorted(classes)],
            "n_source_classes": len(classes), "n_sources_touched": len(touched),
            "n_pages": len(pages), "n_screenshots": sum(1 for p in pages if p["img"]), "n_cells": len(jobs),
            "n_steps": len(steps),
            "pages_href": f"/cases/{case.id}/pages" + (f"?run={quote(rid)}" if rid else ""),
            "entities_href": f"/cases/{case.id}/entities" + (f"?run={quote(rid)}" if g is not None else "")}


# 3 · completeness ---------------------------------------------------------------------------------------------------
def completeness_section(case, g, domain: Domain | None, backend: str | None, metrics: dict, rid: str | None) -> dict:
    out = {"basis": None, "mock": backend == "recorded", "criteria": [], "n_met": 0, "props": [], "primary_label": None,
           "n_primary": 0, "meeting": 0, "threshold": None, "href": f"/cases/{case.id}/output" + (f"?run={quote(rid)}" if rid else ""),
           "empty": None}
    if g is not None and domain is not None:
        rows = output_view.dod_rows(g, domain, backend)
        heat = output_view.heatmap(case.id, g, domain, show_all=True)
        out.update(basis="gold export (recomputed from entities.jsonl)",
                   criteria=[{"criterion_id": r["criterion_id"], "label": r["label"], "query": r["query"],
                              "actual": r["shown"], "target": r["target"], "met": r["met"], "mock": r["mock"],
                              "v": r["v"], "t": r["t"], "state": r["state"]} for r in rows],
                   props=[{"id": c["id"], "label": c["label"], "ratio": c["ratio"], "pct": pct(c["ratio"]),
                           "gold": c["gold"], "missing": c["missing"] + c["weak"] + c["conflict"],
                           "v": round(100 * c["ratio"], 1)} for c in heat["columns"]],
                   primary_label=heat["class_label"], n_primary=heat["n_rows"], meeting=heat["meeting"],
                   threshold=heat["threshold"])
    elif metrics:
        rows = []
        for r in metrics.get("dod") or []:
            if not isinstance(r, dict):
                continue
            actual, target = r.get("actual"), r.get("target")
            nums = [x for x in (actual, target) if isinstance(x, (int, float))]
            scale = max([*nums, 1])
            mock = backend == "recorded"
            met = False if mock else r.get("met")
            rows.append({"criterion_id": r.get("criterion_id"), "label": str(r.get("criterion_id") or r.get("query")).replace("_", " "),
                         "query": r.get("query"), "actual": actual, "target": target, "met": met, "mock": mock,
                         "v": round(100 * (actual or 0) / scale, 1) if isinstance(actual, (int, float)) else 0,
                         "t": round(100 * target / scale, 1) if isinstance(target, (int, float)) else None,
                         "state": "pause" if mock else ("done" if met else "run")})
        props = [{"id": p["name"], "label": p["label"], "ratio": p["ratio"], "pct": pct(p["ratio"]), "gold": None,
                  "missing": None, "v": round(100 * (p["ratio"] or 0), 1)}
                 for p in live.property_ratios(metrics, domain) if p["ratio"] is not None]
        out.update(basis="status.json metrics (engine-reported, so far)", criteria=rows, props=props,
                   primary_label=domain.class_label(plural=True) if domain else None)
    out["n_met"] = sum(1 for r in out["criteria"] if r["met"])
    if not out["criteria"] and not out["props"]:
        out["empty"] = gap(None, "Each definition-of-done criterion against its target, and per-property completeness "
                                 "of the primary class, once a run publishes gold or partial metrics.",
                           "gold/<case>/<run>/entities.jsonl · metrics.json · status.json metrics")
    return out


# 4 · what failed and why --------------------------------------------------------------------------------------------
def failures_section(case, fm: dict, rid: str | None) -> dict:
    groups: dict[str, dict] = {}
    for x in fm["strips"]:
        grp = groups.setdefault(x["kind"], {"kind": x["kind"], "label": FAIL_LABELS.get(x["kind"], x["kind"]),
                                            "state": FAIL_STATES.get(x["kind"], "block"), "n": 0, "example": None,
                                            "contained": x["kind"] in CONTAINED})
        grp["n"] += 1
        if grp["example"] is None or (not grp["example"]["step_id"] and x.get("step_id")):
            grp["example"] = {"title": x["title"], "detail": x["detail"], "step_id": x.get("step_id"), "href": x["href"]}
    order = [k for k, _, _ in failures_view.KINDS]
    reasons = sorted(groups.values(), key=lambda r: (-r["n"], order.index(r["kind"]) if r["kind"] in order else 99))
    counts = {c["kind"]: c["n"] for c in fm["counts"]}
    contained = {"quarantines": counts.get("quarantine", 0), "limit_kills": counts.get("limit_kill", 0),
                 "blocked_domains": counts.get("blocked_domain", 0), "gates": counts.get("gate", 0)}
    return {"n": fm["n_strips"], "reasons": reasons[:MAX_REASONS], "n_reasons": len(reasons),
            "dropped": [x["title"].split(" · ", 1)[-1] for x in fm["strips"] if x["kind"] == "source_dropped"],
            "contained": contained, "n_contained": sum(contained.values()),
            "jobs": fm["totals"]["jobs"], "jobs_all_pass": fm["totals"]["all_pass"], "jobs_killed": fm["totals"]["killed"],
            "href": f"/cases/{case.id}/failures" + (f"?run={quote(rid)}" if rid else ""),
            "empty": None if fm["strips"] else fm["empty"]}


# 5 · cost -----------------------------------------------------------------------------------------------------------
def cost_section(case, cm: dict, im: dict, rid: str | None) -> dict:
    spent = cm["usd_total"] if cm["usd_total"] is not None else cm["loops_total"]

    def rows(key: str) -> list[dict]:
        bar = next((b for b in cm["bars"] if b["key"] == key), None)
        out = []
        for r in (bar or {}).get("table") or []:
            if not r.get("steps"):
                continue
            out.append({"name": r["name"], "usd": r.get("usd") if cm["has_cost"] else None,
                        "usd_text": usd(r.get("usd")) if cm["has_cost"] else "—", "steps": r["steps"],
                        "calls": r.get("calls", 0)})
        if key == "model" and cm["has_cost"]:
            out = [r for r in out if r["name"] != "no model call"]
        return out[:8]

    mounted = bool(im["log"]["mounted"])
    return {"usd_total": spent, "usd_text": usd(spent), "basis": cm["spent_basis"], "has_cost": cm["has_cost"],
            "n_priced": cm["n_priced"], "n_unpriced": cm["n_unpriced"],
            "by_phase": rows("phase"), "by_model": rows("model"),
            "n_values": cm["n_values"], "values_from": cm["values_from"],
            "entities_meeting_dod": cm["entities_meeting_dod"],
            "per_value": cm["per_value"], "per_value_text": usd(cm["per_value"]),
            "per_entity": cm["per_entity"], "per_entity_text": usd(cm["per_entity"]),
            "budget_usd": cm["budget_usd"], "burn": cm["burn"], "burn_text": pct(cm["burn"]) if cm["burn"] is not None else None,
            "inference": {"mounted": mounted, "n_calls": im["n_calls"] if mounted else None,
                          "n_reasoning": im["n_reasoning"] if mounted else None, "pct_vultr": im["pct_vultr"],
                          "unattributed": im["unattributed"]["n"], "n_model_steps": im["n_model_steps"],
                          "usd": im["usd_total"],
                          "text": (f"{im['n_calls']} gateway calls · "
                                   + (f"{im['pct_vultr']:g}% of reasoning on Vultr" if im["pct_vultr"] is not None
                                      else "no reasoning calls")
                                   + f" · {im['unattributed']['n']} unattributed") if mounted else
                                  f"gateway log not mounted · {im['n_model_steps']} model steps in the trace, "
                                  "share on Vultr and unattributed calls unknown",
                          "href": f"/cases/{case.id}/inference" + (f"?run={quote(rid)}" if rid else "")},
            "href": f"/cases/{case.id}/cost" + (f"?run={quote(rid)}" if rid else ""),
            "empty": cm["empty"] if not cm["has_cost"] and cm["loops_total"] is None else None}


# 6 · key receipts ---------------------------------------------------------------------------------------------------
def _candidates(g, domain: Domain) -> list[dict]:
    dod_ids = [p.id for p in domain.dod_props()]
    order = {p.id: i for i, p in enumerate(domain.props())}
    out = []
    for e in g.entities:
        for name, f in (e.get("properties") or {}).items():
            if not isinstance(f, dict) or not f.get("value_id"):
                continue
            evs = [ev for ev in f.get("evidence") or [] if isinstance(ev, dict)]
            out.append({"entity": e, "prop": name, "f": f, "state": value_state(f),
                        "dod": e.get("class") == domain.primary_class and name in dod_ids,
                        "primary": e.get("class") == domain.primary_class,
                        "n_src": len({ev.get("source_id") for ev in evs if ev.get("source_id")}),
                        "shot": any(ev.get("screenshot_key") for ev in evs),
                        "order": order.get(name, 10**6)})
    out.sort(key=lambda c: (not c["dod"], not c["primary"], -c["n_src"], not c["shot"], c["order"], str(c["entity"].get("id")),
                            c["prop"]))
    return out


def receipts(case, a: Artifacts, g, domain: Domain, rid: str) -> list[dict]:
    """3–6 representative values, chosen deterministically: DoD properties of the primary class first (one per
    property, most sources and a screenshot first), then one conflict kept and one membership boolean when present."""
    cands = _candidates(g, domain)
    picks: list[tuple[dict, str]] = []
    seen_props: set[str] = set()
    gold = [c for c in cands if c["state"] == "gold"]
    seen_entities: set[str] = set()
    for c in gold:  # properties in the order of their best candidate; a different entity for each when possible
        if len(picks) >= MAX_GOLD_RECEIPTS:
            break
        if c["prop"] in seen_props:
            continue
        seen_props.add(c["prop"])
        same = [x for x in gold if x["prop"] == c["prop"] and x["dod"] == c["dod"] and x["n_src"] == c["n_src"]]
        c = next((x for x in same if str(x["entity"].get("id")) not in seen_entities), c)
        seen_entities.add(str(c["entity"].get("id")))
        picks.append((c, "DoD property" if c["dod"] else "gold value"))
    for c in gold:  # fewer properties than slots: fill to three
        if len(picks) >= 3:
            break
        if all(c is not p for p, _ in picks):
            picks.append((c, "DoD property" if c["dod"] else "gold value"))
    conflict = next((c for c in cands if c["state"] == "conflict"), None)
    if conflict is not None:
        picks.append((conflict, "conflict kept"))
    boolean = next((c for c in cands if isinstance(c["f"].get("value"), bool) and c["state"] == "gold"
                    and all(c is not p for p, _ in picks)), None)
    if boolean is not None:
        picks.append((boolean, "membership"))
    out = []
    rq = f"?run={q(rid)}"
    for c, why in picks[:MAX_RECEIPTS]:
        f, e = c["f"], c["entity"]
        evs = [ev for ev in f.get("evidence") or [] if isinstance(ev, dict)]
        first = next((ev for ev in evs if ev.get("screenshot_key")), evs[0] if evs else None)
        ev = evidence_view(case.id, a, first) if first else None
        reasons = [why] + ([f"{c['n_src']} sources"] if c["n_src"] > 1 else [])
        out.append({"value_id": f["value_id"], "entity_id": e.get("id"), "entity_title": g.title(e),
                    "entity_href": f"/cases/{case.id}/entities/{q(e['id'])}{rq}#p-{c['prop']}",
                    "prop": c["prop"], "prop_label": domain.prop_label(c["prop"], e.get("class")),
                    "value": value_text(f.get("value")), "status": f.get("status"), "state": c["state"],
                    "why": " · ".join(reasons), "n_sources": c["n_src"],
                    "sources": sorted({x.get("source_id") for x in evs if x.get("source_id")}),
                    "evidence": {"host": ev["host"], "url": ev["url"], "captured_at": ev["captured_at"],
                                 "captured": _hhmm(ev["captured_at"]) or ev["captured_at"],
                                 "source_id": ev["source_id"], "screenshot_href": ev["screenshot_href"],
                                 "selector": ev["selector"]} if ev else None,
                    "lineage_href": f"/cases/{case.id}/lineage/{q(f['value_id'])}{rq}"})
    return out


# 7 · next -----------------------------------------------------------------------------------------------------------
def next_section(case, a: Artifacts, rid: str | None, steps: list[dict], comp: dict, run: dict | None,
                 live_on: bool) -> list[dict]:
    base = f"/cases/{case.id}"
    rq = f"?run={quote(rid)}" if rid else ""
    items = [{"state": s["state"], "title": s["title"], "detail": s["detail"] or None, "href": s["href"]}
             for s in pending_strips(a, case.id, (*CP_LABELS, ""))]
    if live_on and run:
        items.append({"state": "run", "title": f"Run in motion: phase {run['phase']} · {run['phase_name']}"
                      if run.get("phase") else "Run in motion", "detail": "numbers on this page are so far",
                      "href": run["operation_href"] or run["href"]})
    marks = reopen_markers(steps)
    if marks and marks[-1]["reopen"] is not None:
        mk = marks[-1]
        items.append({"state": "need", "title": f"Gap loop reopens phase {mk['reopen']} · {mk['phase_name']}",
                      "detail": mk["reason"], "href": f"{base}/runs/{quote(rid)}#{mk['step_id']}" if run and run["has_feed"]
                      else f"{base}/output{rq}"})
    if comp["mock"] and comp["criteria"]:
        items.append({"state": "pause", "title": "Recorded inference: rerun on live inference for the DoD to count",
                      "detail": "criteria below are shown for rehearsal and never count as met", "href": comp["href"]})
    else:
        for r in comp["criteria"]:
            if r["met"] is False or r["met"] is None:
                items.append({"state": "run", "title": f"Criterion not met: {r['label']}",
                              "detail": f"{r['actual'] if r['actual'] is not None else '—'} of target {r['target']}",
                              "href": comp["href"] + "#dod"})
    for p in comp["props"]:
        if p["ratio"] is not None and p["ratio"] < 1:
            items.append({"state": "run", "title": f"Uncovered: {p['label']} at {p['pct']}",
                          "detail": f"{p['missing']} {(comp['primary_label'] or 'entities').lower()} not yet gold with evidence"
                          if p["missing"] is not None else "per-property completeness below 100%",
                          "href": comp["href"] + "#heat"})
    if comp["n_primary"] and comp["meeting"] < comp["n_primary"]:
        items.append({"state": "run", "title": f"{comp['n_primary'] - comp['meeting']} of {comp['n_primary']} "
                                               f"{(comp['primary_label'] or 'entities').lower()} below the per-entity threshold",
                      "detail": f"threshold {pct(comp['threshold'])} of the DoD properties", "href": comp["href"] + "#heat"})
    return items[:MAX_NEXT]


# plain text -----------------------------------------------------------------------------------------------------------
def plain_text(m: dict) -> str:
    L: list[str] = []
    sofar = " (so far)" if m["so_far"] else ""
    L.append(f"{m['case_title']} · summary" + (f" · run {m['run_id']}" if m["run_id"] else ""))
    if m["question"]:
        L.append(f"Question: {' '.join(m['question'].split())}")
    r = m["run"]
    if r:
        L.append(f"Run: {r['run_id']} · {r['state']}" + (f" · phase {r['phase']} {r['phase_name']}" if r["phase"] else ""))
        if r["started"]:
            L.append(f"  {r['started']} → {r['ended']} · {r['duration'] or '—'}{sofar} · {r['n_steps']} steps")
        for s in r["stop"]:
            L.append(f"  Why it stopped: {s}")
        L.append(f"  Waited for a person {r['pauses']}× · resumed {r['resumes']}× ({r['pause_basis']})")
    else:
        L.append("Run: none yet")
    if m["mock"]:
        L.append("Recorded (simulated) inference: nothing here counts toward the definition of done.")
    p = m["produced"]
    if m["run_id"]:
        classes = ", ".join(f"{c['n']} {c['label'].lower()}" for c in p["entities_by_class"]) or "no entities"
        L.append(f"Produced{sofar}: {classes} · {p['n_values']} gold values · {p['n_sources']} sources "
                 f"({p['n_source_classes']} classes) · {p['n_pages']} pages captured · sandbox cells run: {p['n_cells']}")
    c = m["completeness"]
    if c["criteria"]:
        L.append(f"Definition of done: {c['n_met']} of {len(c['criteria'])} met" + (" (mock, does not count)" if c["mock"] else ""))
        for x in c["criteria"]:
            word = "mock" if x["mock"] else ("met" if x["met"] else "not met")
            L.append(f"  - {x['label']}: {x['actual'] if x['actual'] is not None else '—'} / {x['target']} · {word}")
    if c["props"]:
        L.append("Completeness: " + " · ".join(f"{x['label']} {x['pct']}" for x in c["props"]))
    f = m["failures"]
    if m["run_id"]:
        L.append(f"Failures and containment: {f['n']} events · {f['n_contained']} contained "
                 f"(quarantined {f['contained']['quarantines']}, limit kills {f['contained']['limit_kills']}, "
                 f"blocked domains {f['contained']['blocked_domains']}, gates held {f['contained']['gates']})")
        for x in f["reasons"]:
            L.append(f"  - {x['label']}: {x['n']}" + (f" · e.g. {x['example']['title']}" if x["example"] else ""))
        if f["dropped"]:
            L.append(f"  Sources dropped: {', '.join(f['dropped'])}")
    k = m["cost"]
    if m["run_id"]:
        L.append(f"Cost: {k['usd_text']}" + (f" ({k['basis']})" if k["basis"] else "") +
                 f" · {k['per_value_text']} per gold value · {k['per_entity_text']} per entity meeting the DoD")
        if k["by_phase"]:
            L.append("  By phase: " + " · ".join(f"{x['name']} {x['usd_text'] if k['has_cost'] else str(x['steps']) + ' steps'}"
                                               for x in k["by_phase"]))
        L.append(f"  Inference: {k['inference']['text']}")
    if m["receipts"]:
        L.append("Receipts:")
        for x in m["receipts"]:
            ev = x["evidence"] or {}
            L.append(f"  - {x['entity_title']} · {x['prop_label']}: {x['value']} ({x['why']}) · "
                     f"{ev.get('host') or 'no evidence'}" + (f" · {ev['captured']}" if ev.get("captured") else ""))
    if m["next"]:
        L.append("Next:")
        for x in m["next"]:
            L.append(f"  - {x['title']}" + (f" · {x['detail']}" if x["detail"] else ""))
    return "\n".join(L)


# view-model -----------------------------------------------------------------------------------------------------------
def _case_domain(a: Artifacts) -> Domain | None:
    onto = a.json("02-ontology/ontology.json")
    if not isinstance(onto, dict):
        return None
    d = Domain.from_ontology(onto)
    return d if d.primary_class else None


def model(case, run: str | None = None, runner_root: Path | None = None, all_cases=None) -> dict:
    a = Artifacts(case)
    now = datetime.now(UTC)
    rid = hc.resolve_run(a, run)  # 404 for an unknown ?run=
    steps = hc.steps_of(a, rid)
    has_feed = bool(rid) and rid in a.run_ids()
    status = a.status(rid) if has_feed else {}
    metrics = hc.metrics_of(a, rid)
    jobs = a.jobs(rid) if has_feed else []
    g = hc.gold_run(a, rid)
    domain = domain_for(g) if g is not None else _case_domain(a)
    fm = failures_view.model(case, rid)
    cm = cost_view.model(case, rid)
    im = inference_view.model(case, rid, all_cases)
    backend = fm["backend"] or cm["backend"] or (g.inference_backend if g is not None else None)
    if g is not None and g.inference_backend == "recorded":
        backend = "recorded"
    live_on = has_feed and is_live(status, now)
    run_m = run_section(case, a, rid, steps, status, runner_root, now)
    comp = completeness_section(case, g, domain, backend, metrics, rid)
    m = {"case_id": case.id, "case_title": case.title, "question": brief_text(a), "run_id": rid,
         "runs": hc.run_ids(a), "gold_run_ids": a.gold_run_ids(),
         "live": live_marker(live_on, last_activity(status, steps) if has_feed else None),
         "so_far": live_on, "backend": backend, "mock": backend == "recorded",
         "primary_class": domain.primary_class if domain else None,
         "run": run_m,
         "produced": produced_section(case, a, rid, g, domain, steps, metrics, jobs),
         "completeness": comp,
         "failures": failures_section(case, fm, rid),
         "cost": cost_section(case, cm, im, rid),
         "receipts": receipts(case, a, g, domain_for(g), rid) if g is not None else [],
         "sources": SOURCES,
         "json_href": f"/cases/{case.id}/api/viz/{SLUG}" + (f"?run={quote(rid)}" if run and rid else "")}
    m["next"] = next_section(case, a, rid, steps, comp, run_m, live_on)
    m["receipts_empty"] = None if m["receipts"] else gap(
        "R2", "Three to six representative values with their evidence: source page, capture time, screenshot and the "
              "lineage of each value.", "gold/<case>/<run>/entities.jsonl evidence[] · bronze/sha256/<hex>.meta.json")
    m["next_empty"] = None if m["next"] else gap(
        None, "Open gaps: checkpoints waiting for a person, uncovered DoD properties, unmet criteria and phases the gap "
              "loop reopens.", "*/APPROVAL_PENDING.md · metrics.json · trace.live.jsonl (outer loop)")
    m["empty"] = None if rid else gap(
        None, "When, how long and why the run stopped; what it produced; how close it is to done; what failed and was "
              f"contained; what it cost; receipts. The engine has not published a run for this case "
              f"({case.lake_error or 'no run feed or gold export yet'}).",
        "runs/<case>/latest.json · gold/<case>/latest.json")
    m["plain"] = plain_text(m)
    return m


def install(ctx: VizContext) -> None:
    app, render, settings = ctx.app, ctx.render, ctx.settings
    ctx.case_view(KEY, SLUG, LABEL, ORDER)

    def _root() -> Path | None:
        return getattr(settings, "runner_state", None)

    def _cases():
        return list(settings.cases.values())

    @app.get(f"/cases/{{case_id}}/{SLUG}", response_class=HTMLResponse)
    def summary_page(request: Request, case_id: str, run: str | None = None):
        case = ctx.get_case(case_id)
        m = model(case, run or None, _root(), _cases())
        return render(request, "viz/summary.html", nav=KEY, case=case, m=m, backend=m["backend"])

    @app.get(f"/cases/{{case_id}}/api/viz/{SLUG}")
    def summary_api(case_id: str, run: str | None = None) -> dict:
        return model(ctx.get_case(case_id), run or None, _root(), _cases())
