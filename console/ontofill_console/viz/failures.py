"""Q7 · What went wrong, and what was contained? Failures, stops, blocked domains, retries and escalations, limit kills,
quarantines, action gates and dropped sources as strips; one row per sandbox job with its six proof checkpoints.

Reads the run's `trace.live.jsonl` (or the gold `trace.jsonl`), `status.json` (sources health, metrics.jobs) and
`jobs.jsonl` (checkpoints, limits, usage, killed_by).
"""

from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import HTMLResponse

from .. import live
from ..gold import backend_of
from . import health_common as hc
from .core import Artifacts, VizContext, gap

ORDER = 60
SOURCE = "runs/<case>/<run>/trace.live.jsonl · status.json (sources[].health) · jobs.jsonl"

KINDS = [  # (kind, label, state) in display order
    ("stop", "Captcha, login or injection stops", "block"),
    ("failure", "Failures and errors", "block"),
    ("blocked_domain", "Blocked domains", "block"),
    ("limit_kill", "Limit kills", "block"),
    ("checkpoint_fail", "Failed proof checkpoints", "block"),
    ("source_dropped", "Sources dropped", "block"),
    ("quarantine", "Quarantined pages", "quar"),
    ("gate", "Action gates held or denied", "pause"),
    ("escalation", "Retries and escalations", "pause"),
    ("source_degraded", "Sources with failed steps", "pause"),
]
STOP_WORDS = ("captcha", "login", "log in", "sign in", "injection", "401", "402", "paywall")
BLOCK_WORDS = ("domain_not_allowed", "not allowlisted", "not_allowlisted", "egress_blocked", "blocked_domain",
               "domain blocked", "outside allowed_domains", "not in allowed_domains")
CK_GLYPH = {"pass": "✓", "fail": "✗", "pending": "–"}
CK_WORD = {"pass": "pass", "fail": "fail", "pending": "missing"}


def _text(s: dict) -> str:
    return " ".join(json.dumps(s.get(k)) if isinstance(s.get(k), (dict, list)) else str(s.get(k) or "")
                    for k in ("requested", "executed", "evaluated")).lower()


def _attempt(s: dict) -> int | None:
    for k in ("requested", "observed", "executed"):
        v = s.get(k)
        if isinstance(v, dict) and isinstance(v.get("attempt"), int):
            return v["attempt"]
    return None


def classify(s: dict) -> tuple[str, str, str] | None:
    """(kind, title, detail) for a step that went wrong or was contained, else None."""
    kind, ev, d = s.get("kind"), s.get("event"), s.get("detail") or {}
    text = _text(s)
    src = s.get("source_id") or "no source"
    if kind == "quarantine":
        detail = " · ".join(str(x) for x in (
            d.get("jev_choice"), f"confidence {d['jev_confidence']}" if d.get("jev_confidence") is not None else None,
            d.get("safety_verdict") and f"safety {d['safety_verdict']}", d.get("reason"), d.get("by") and f"by {d['by']}",
            "withheld from planning, kept as evidence") if x)
        return "quarantine", f"Page quarantined · {src}", detail
    if kind == "kill" or ev == "limit_kill":
        reason = d.get("reason")
        return "limit_kill", f"Stopped by a resource limit · {src}", " · ".join(
            str(x) for x in (live.KILL_LABELS.get(reason, reason), hc.text_of(s.get("requested"), 80), "host untouched") if x)
    if kind == "gate":
        outcome = d.get("outcome")
        if outcome in ("pending_approval", "denied"):
            word = "awaits approval" if outcome == "pending_approval" else "denied"
            return "gate", f"Action {word} · {src}", " · ".join(str(x) for x in (
                d.get("action"), d.get("risk_tier") and f"risk {d['risk_tier']}", d.get("decided_by") and f"by {d['decided_by']}",
                d.get("approval_path")) if x)
        return None
    if ev == "hard_stop" or (any(w in text for w in STOP_WORDS) and ev in ("failure", "hard_stop")):
        which = next((w for w in STOP_WORDS if w in text), "hard stop")
        return "stop", f"Stopped: {which} · {src}", hc.text_of(s.get("evaluated")) or hc.text_of(s.get("requested"))
    if any(w in text for w in BLOCK_WORDS):
        return "blocked_domain", f"Domain blocked · {src}", hc.text_of(s.get("evaluated")) or hc.text_of(s.get("requested"))
    if ev == "escalation":
        return "escalation", f"Escalated to {s.get('mode') or '?'} · {src}", \
            f"{live.MODE_NAMES.get(s.get('mode'), s.get('mode'))} after the cheaper mode failed its check · {hc.text_of(s.get('requested'), 100)}"
    if (_attempt(s) or 0) > 1 and kind != "repair":
        return "escalation", f"Retry, attempt {_attempt(s)} · {src}", hc.text_of(s.get("evaluated")) or hc.text_of(s.get("requested"))
    if ev == "failure" or (kind is None and ev is None and live._failed(s)):
        word = "Error" if ("error" in text or "exception" in text) else ("Timeout" if "timeout" in text else "Failed")
        return "failure", f"{word} · {src}", " · ".join(x for x in (hc.text_of(s.get("requested"), 100),
                                                                     hc.text_of(s.get("evaluated"), 120)) if x)
    if "dropped" in text and s.get("source_id"):
        return "source_dropped", f"Source dropped · {src}", hc.text_of(s.get("evaluated")) or hc.text_of(s.get("executed"))
    return None


def job_row(job: dict, base: str, rid: str) -> dict:
    cps = []
    for key, label, desc in live.CHECKPOINTS:
        state = live.checkpoint_state(job, key)
        cps.append({"key": key, "label": label, "desc": desc, "state": state, "glyph": CK_GLYPH[state],
                    "word": CK_WORD[state]})
    runtime = ((job.get("checkpoints") or {}).get("host") or {}).get("runtime")
    tier = live.isolation_tier(runtime)
    limits = job.get("limits") if isinstance(job.get("limits"), dict) else {}
    usage = job.get("usage") if isinstance(job.get("usage"), dict) else {}
    return {"job_id": job.get("job_id"), "source_id": job.get("source_id"), "step_id": job.get("step_id"),
            "href": f"{base}/runs/{rid}#{job.get('step_id')}" if job.get("step_id") else f"{base}/runs/{rid}#proof-h",
            "checkpoints": cps, "passed": sum(1 for c in cps if c["state"] == "pass"),
            "failed": sum(1 for c in cps if c["state"] == "fail"),
            "missing": sum(1 for c in cps if c["state"] == "pending"),
            "tier": tier, "runtime": runtime,
            "limits": ", ".join(f"{k} {v}" for k, v in limits.items()) or None,
            "usage": ", ".join(f"{k} {v}" for k, v in usage.items()) or None,
            "killed_by": job.get("killed_by"),
            "teardown": live.checkpoint_state(job, "teardown") == "pass"}


def model(case, run: str | None = None) -> dict:
    a = Artifacts(case)
    rid = hc.resolve_run(a, run)
    base = f"/cases/{case.id}"
    steps = hc.steps_of(a, rid)
    status = a.status(rid)
    jobs = a.jobs(rid)
    strips: list[dict] = []
    for s in steps:
        c = classify(s)
        if c is None:
            continue
        kind, title, detail = c
        strips.append({"kind": kind, "state": dict((k, st) for k, _, st in KINDS)[kind], "title": title,
                       "detail": detail or None, "step_id": s.get("step_id"), "when": s.get("ts"),
                       "phase": s.get("phase"), "mode": s.get("mode"),
                       "href": f"{base}/runs/{rid}#{s.get('step_id')}"})
    for j in jobs:
        if j.get("killed_by"):
            strips.append({"kind": "limit_kill", "state": "block", "title": f"Job stopped by a resource limit · {j.get('job_id')}",
                           "detail": " · ".join(str(x) for x in (live.KILL_LABELS.get(j["killed_by"], j["killed_by"]),
                                                                 j.get("source_id"), "host untouched") if x),
                           "step_id": j.get("step_id"), "when": j.get("ended_at"), "phase": 5, "mode": None,
                           "href": f"{base}/runs/{rid}#{j.get('step_id') or 'proof-h'}"})
        failed = [label for key, label, _ in live.CHECKPOINTS if live.checkpoint_state(j, key) == "fail"]
        if failed:
            strips.append({"kind": "checkpoint_fail", "state": "block", "title": f"Proof checkpoint failed · {j.get('job_id')}",
                           "detail": ", ".join(failed), "step_id": j.get("step_id"), "when": j.get("ended_at"),
                           "phase": 5, "mode": None, "href": f"{base}/runs/{rid}#{j.get('step_id') or 'proof-h'}"})
    for src in status.get("sources") or []:
        h = src.get("health") if isinstance(src.get("health"), dict) else {}
        ok, bad, yld = h.get("ok") or 0, h.get("failed") or 0, h.get("yield") or 0
        if not bad:
            continue
        dropped = ok == 0 or not yld
        strips.append({"kind": "source_dropped" if dropped else "source_degraded", "state": "block" if dropped else "pause",
                       "title": f"{'Source dropped' if dropped else 'Source degraded'} · {src.get('source_id')}",
                       "detail": f"{src.get('source_type') or 'unknown class'} · ok {ok} · failed {bad} · yield {yld}",
                       "step_id": None, "when": status.get("updated_at"), "phase": 5, "mode": None,
                       "href": f"{base}/runs/{rid}"})
    order = [k for k, _, _ in KINDS]
    strips.sort(key=lambda x: (order.index(x["kind"]), x["when"] or ""))
    counts = [{"kind": k, "label": label, "state": st, "n": sum(1 for x in strips if x["kind"] == k)} for k, label, st in KINDS]

    rows = [job_row(j, base, rid) for j in jobs] if rid else []
    totals = {"jobs": len(rows), "all_pass": sum(1 for r in rows if r["passed"] == len(live.CHECKPOINTS)),
              "any_fail": sum(1 for r in rows if r["failed"]), "teardown": sum(1 for r in rows if r["teardown"]),
              "killed": sum(1 for r in rows if r["killed_by"]), "with_limits": sum(1 for r in rows if r["limits"]),
              "by_checkpoint": [{"key": key, "label": label,
                                 "pass": sum(1 for r in rows for c in r["checkpoints"] if c["key"] == key and c["state"] == "pass"),
                                 "fail": sum(1 for r in rows for c in r["checkpoints"] if c["key"] == key and c["state"] == "fail"),
                                 "missing": sum(1 for r in rows for c in r["checkpoints"] if c["key"] == key and c["state"] == "pending")}
                                for key, label, _ in live.CHECKPOINTS]}
    jm = (status.get("metrics") or {}).get("jobs") if isinstance(status.get("metrics"), dict) else None
    if rid is None:
        empty = gap("R8", "Failures, stops, quarantines, limit kills and the six sandbox checkpoints of this case's runs.",
                    "runs/<case>/<run>/trace.live.jsonl, jobs.jsonl")
    elif not strips:
        empty = gap("R8", f"Nothing went wrong or was contained in {rid} as far as its trace shows ({len(steps)} steps). "
                          "Containment moments on a real run (quarantine, limit kill) appear here as strips.", SOURCE)
    else:
        empty = None
    return {"case_id": case.id, "run_id": rid, "runs": hc.run_ids(Artifacts(case)), "state": status.get("state"),
            "n_steps": len(steps), "strips": strips, "counts": counts, "n_strips": len(strips),
            "contained": sum(1 for x in strips if x["kind"] in ("quarantine", "limit_kill", "gate", "blocked_domain")),
            "cells": rows, "totals": totals, "engine_jobs": jm if isinstance(jm, dict) else None,
            "checkpoints": [{"key": k, "label": label, "desc": d} for k, label, d in live.CHECKPOINTS],
            "empty": empty,
            "cells_empty": None if rows else gap("R3", "One row per sandbox job: host, task, where it ran, isolation "
                                                        "probe, teardown and secret hygiene, with limits and usage.",
                                                 "runs/<case>/<run>/jobs.jsonl"),
            "backend": backend_of(status.get("metrics"), [*steps, status]) if rid else None}


def install(ctx: VizContext) -> None:
    app, render = ctx.app, ctx.render
    ctx.case_view("failures", "failures", "Failures", ORDER)

    @app.get("/cases/{case_id}/failures", response_class=HTMLResponse)
    def failures_page(request: Request, case_id: str, run: str | None = None):
        case = ctx.get_case(case_id)
        m = model(case, run)
        return render(request, "viz/failures.html", nav="failures", case=case, m=m, backend=m["backend"])

    @app.get("/cases/{case_id}/api/viz/failures")
    def failures_api(case_id: str, run: str | None = None) -> dict:
        return model(ctx.get_case(case_id), run)
