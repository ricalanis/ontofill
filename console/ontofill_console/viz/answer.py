"""The answer: a public, read-only page that answers a case's question from its gold export, one card per entity of
the ontology's primary class, each value one click from its evidence (source URL, selector, screenshot, raw capture)
and its lineage. Before gold lands it says so and shows the live run instead of inventing values.

Reads `gold/<case>/<run>/entities.jsonl` + ontology (via the shared gold loader) and, for the live state, the run's
`status.json` and trace. Generic: nothing here knows about libraries or suppliers.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from .. import dod, live
from . import health_common as hc
from .core import Artifacts, VizContext
from .output_common import (
    brief_text,
    domain_for,
    entity_identifier,
    entity_title,
    evidence_view,
    props_for,
    q,
    resolve_run,
    value_state,
)

ORDER = 3  # right after the summary (the first case view after runs)
KEY, SLUG, LABEL = "answer", "answer", "Answer"
CAPTURE_KEYS = ("html_key", "screenshot_key", "bronze_key", "document_key")
MAX_FIELDS = 8


def show(value) -> str:
    """A gold value as a reader sees it: booleans in words, lists and records on lines, never a Python repr."""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, dict):
        return "\n".join(f"{k}: {show(v)}" for k, v in value.items() if v not in (None, "", [], {}))
    if isinstance(value, (list, tuple)):
        return "\n".join(show(v) for v in value if v not in (None, ""))
    return str(value)


def _live(a: Artifacts) -> dict | None:
    """The newest run's progress, from its own status and trace: what a reader can honestly see before gold."""
    rid = hc.resolve_run(a, None)
    if not rid:
        return None
    status = a.status(rid) or {}
    steps = a.steps(rid) or []
    phase = status.get("phase")
    captures = sum(
        1
        for s in steps
        if any(isinstance(s.get(f), dict) and any(s[f].get(k) for k in CAPTURE_KEYS) for f in ("executed", "observed"))
    )
    return {
        "run_id": rid,
        "state": status.get("state"),
        "phase": phase,
        "phase_name": dict(live.PHASES).get(phase, ""),
        "checkpoint": status.get("checkpoint_pending"),
        "reason": status.get("reason"),
        "updated_at": status.get("updated_at"),
        "n_steps": len(steps),
        "n_captures": captures,
        "n_sources": len({s.get("source_id") for s in steps if s.get("source_id")}),
        "phases": [
            {"n": n, "name": name, "done": bool(phase and n < phase), "now": n == phase} for n, name in live.PHASES
        ],
    }


def model(case, run: str | None = None) -> dict:
    a = Artifacts(case)
    g, run_id, note = resolve_run(a, run)
    brief = brief_text(a) or ""
    question = next((ln.strip("# ").strip() for ln in brief.splitlines() if ln.strip()), case.id)
    base = {
        "case_id": case.id,
        "case_title": getattr(case, "title", None) or case.id,
        "question": question,
        "brief": brief,
        "live": _live(a),
        "run_id": run_id,
    }
    if g is None:
        return {**base, "has_gold": False, "cards": [], "fields": [], "n_cards": 0}
    domain = domain_for(g)
    cls = domain.primary_class
    props, d = props_for(domain, g, cls)
    shown = {domain.title_property(cls), domain.identifier_property(cls)}  # already in the card heading
    fields = [p for p in props if p.id not in shown and p.dod][:MAX_FIELDS] or [p for p in props if p.id not in shown][
        :MAX_FIELDS
    ]
    rq = f"?run={q(run_id)}" if run_id else ""
    cards, n_filled = [], 0
    for e in sorted(g.primary, key=lambda x: entity_title(d, x).lower()):
        values = e.get("properties") or {}
        rows = []
        for p in fields:
            f = values.get(p.id) if isinstance(values.get(p.id), dict) else None
            filled = dod.is_filled(f)
            n_filled += bool(filled)
            vid = (f or {}).get("value_id")
            rows.append(
                {
                    "id": p.id,
                    "label": p.label,
                    "value": show((f or {}).get("value")) if filled else "",
                    "state": value_state(f),
                    "filled": bool(filled),
                    "evidence": [
                        evidence_view(case.id, a, ev, with_meta=False) for ev in (f or {}).get("evidence") or []
                    ],
                    "lineage_href": f"/cases/{case.id}/lineage/{q(vid)}{rq}" if vid else None,
                }
            )
        cards.append(
            {
                "id": e["id"],
                "title": entity_title(d, e),
                "identifier": entity_identifier(d, e),
                "rows": rows,
                "complete": all(r["filled"] for r in rows),
                "href": f"/cases/{case.id}/entities/{q(e['id'])}{rq}",
            }
        )
    total = len(cards) * len(fields)
    return {
        **base,
        "has_gold": True,
        "note": note,
        "class_label": d.class_label(cls),
        "class_plural": d.class_label(cls, plural=True),
        "fields": [{"id": p.id, "label": p.label} for p in fields],
        "cards": cards,
        "n_cards": len(cards),
        "n_complete": sum(c["complete"] for c in cards),
        "n_filled": n_filled,
        "n_values": total,
        "backend": g.inference_backend,
    }


def install(ctx: VizContext) -> None:
    app, render = ctx.app, ctx.render
    ctx.case_view(KEY, SLUG, LABEL, ORDER)

    @app.get(f"/cases/{{case_id}}/{SLUG}", response_class=HTMLResponse)
    def answer_page(request: Request, case_id: str, run: str | None = None):
        case = ctx.get_case(case_id)
        m = model(case, run or None)
        return render(request, "viz/answer.html", nav=KEY, case=case, m=m, backend=m.get("backend"))

    @app.get(f"/cases/{{case_id}}/api/viz/{SLUG}")
    def answer_api(case_id: str, run: str | None = None) -> dict:
        return model(ctx.get_case(case_id), run or None)
