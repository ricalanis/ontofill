"""Q1 · What is the engine doing right now, and what needs me? A cross-case inbox of strips.

Reads, per registered case: pending checkpoints (`*/APPROVAL_PENDING.md` without `APPROVED`), the latest run's
`status.json`, its trace (quarantines, limit kills, failures) and `jobs.jsonl`. Ordered by what needs a person first.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request
from fastapi.responses import HTMLResponse

from .. import live
from .core import Artifacts, VizContext, age, gap

ORDER = 0
CP_LABELS = {"prd": "PRD", "factors": "Factors", "ontology": "Ontology", "action": "Action before submit"}
RANK = {"need": 0, "block": 1, "quar": 2, "run": 3, "pause": 4, "done": 5}
STATE_OF_RUN = {"running": "run", "paused": "pause", "failed": "block", "done": "done"}


def case_items(case, now: datetime) -> list[dict]:
    a = Artifacts(case)
    base = f"/cases/{case.id}"
    items: list[dict] = []
    for item in a.approvals():
        if item.approved is not None:
            continue
        cp = item.checkpoint or "checkpoint"
        what = item.meta.get("intended_action") if cp == "action" else None
        items.append({"state": "need", "case_id": case.id, "kind": "checkpoint",
                      "title": f"{CP_LABELS.get(cp, cp)} awaits your review" + (f": {what}" if what else ""),
                      "detail": " · ".join(x for x in (item.phase_dir, item.meta.get("reason")) if x),
                      "when": age(item.meta.get("requested_at"), now), "since": item.meta.get("requested_at"),
                      "href": f"{base}/approvals/{item.phase_dir}"})
    if case.lake_error:
        items.append({"state": "block", "case_id": case.id, "kind": "lake", "title": "Lake unreachable",
                      "detail": case.lake_error, "when": None, "since": None, "href": base})
    rid = a.latest_run_id()
    if rid:
        status = a.status(rid)
        steps = a.steps(rid)
        state = STATE_OF_RUN.get(status.get("state") or "", "run" if steps else "pause")
        modes: dict[str, int] = {}
        for s in steps:
            if s.get("mode"):
                modes[s["mode"]] = modes.get(s["mode"], 0) + 1
        phase = status.get("phase")
        detail = [f"phase {phase} · {live.phase_name(phase)}" if phase else None, f"{len(steps)} steps",
                  " · ".join(f"{m} {n}" for m, n in sorted(modes.items(), key=lambda kv: live.MODE_RANK.get(kv[0], 9)))]
        if status.get("checkpoint_pending"):
            detail.insert(0, f"paused at {status['checkpoint_pending']}")
        items.append({"state": state, "case_id": case.id, "kind": "run", "title": f"Run {rid}: {status.get('state') or 'no status yet'}",
                      "detail": " · ".join(x for x in detail if x), "when": age(status.get("updated_at"), now),
                      "since": status.get("updated_at"), "href": f"{base}/runs/{rid}"})
        for s in steps:
            if s.get("kind") == "quarantine":
                d = s.get("detail") or {}
                items.append({"state": "quar", "case_id": case.id, "kind": "quarantine",
                              "title": f"Page quarantined · {s.get('source_id') or 'unknown source'}",
                              "detail": " · ".join(str(x) for x in (d.get("jev_choice"), d.get("reason"), "withheld from planning, kept as evidence") if x),
                              "when": age(s.get("ts"), now), "since": s.get("ts"), "href": f"{base}/runs/{rid}#{s.get('step_id')}"})
            elif s.get("kind") == "kill":
                reason = (s.get("detail") or {}).get("reason")
                items.append({"state": "block", "case_id": case.id, "kind": "limit_kill",
                              "title": "Job stopped by a resource limit",
                              "detail": " · ".join(str(x) for x in (live.KILL_LABELS.get(reason, reason), s.get("source_id"), "host untouched") if x),
                              "when": age(s.get("ts"), now), "since": s.get("ts"), "href": f"{base}/runs/{rid}#{s.get('step_id')}"})
    return items


def model(settings) -> dict:
    now = datetime.now(UTC)
    items, cases = [], []
    for case in settings.cases.values():
        mine = case_items(case, now)
        items += mine
        cases.append({"id": case.id, "title": case.title, "question": case.brief,
                      "needs_you": sum(1 for i in mine if i["state"] == "need")})
    items.sort(key=lambda i: (RANK.get(i["state"], 9), i["since"] or ""))
    return {"strips": items, "cases": cases, "needs_you": sum(1 for i in items if i["state"] == "need"),
            "empty": None if items else gap(None, "Checkpoints waiting for a person, runs in motion, quarantined pages and "
                                                  "limit kills across every registered case.",
                                            "*/APPROVAL_PENDING.md, runs/<case>/<run>/status.json, trace.live.jsonl")}


def install(ctx: VizContext) -> None:
    app, render, settings = ctx.app, ctx.render, ctx.settings
    ctx.env.globals["inbox_count"] = lambda: sum(
        1 for c in settings.cases.values() for a in Artifacts(c).approvals() if a.approved is None)

    @app.get("/inbox", response_class=HTMLResponse)
    def inbox(request: Request):
        return render(request, "viz/inbox.html", nav="inbox", m=model(settings))

    @app.get("/api/viz/inbox")
    def inbox_api() -> dict:
        return model(settings)
