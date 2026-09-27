"""Q10 · Same engine, different question. Case vs case side by side (brief → PRD → ontology → discovery → output → runs)
at `/compare?a=&b=`, and run vs run inside one case at `/cases/<id>/compare?run_a=&run_b=` (metrics deltas,
per-property completeness, entities added/removed, values changed, mode share change).
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from ..gold import backend_of
from . import health_common as hc
from .core import SAFE_ERRORS, Artifacts, VizContext, gap

ORDER = 90
SAMPLE = 25


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _sources(a: Artifacts, latest: str | None) -> tuple[list[str], list[str]]:
    """Discovered sources: objectives.yaml, surface maps, 04-local plans and the latest run's status. Returns (ids, from)."""
    ids: dict[str, None] = {}
    found = []
    doc = a.yaml("03-fanout/objectives.yaml")
    objs = doc.get("objectives") if isinstance(doc, dict) else None
    if isinstance(objs, list):
        ids.update(dict.fromkeys(str(o["source_id"]) for o in objs if isinstance(o, dict) and o.get("source_id")))
        found.append("03-fanout/objectives.yaml")
    maps = {p.split("/")[2] for p in a.glob("03-fanout/surface-map/*/*")}
    if maps:
        ids.update(dict.fromkeys(sorted(maps)))
        found.append("03-fanout/surface-map/")
    local = {p.split("/")[1].split("__")[0] for p in a.glob("04-local/*/*") if "__" in p.split("/")[1]}
    if local:
        ids.update(dict.fromkeys(sorted(local)))
        found.append("04-local/")
    st = a.status(latest).get("sources") or []
    if st:
        ids.update(dict.fromkeys(str(s.get("source_id")) for s in st if isinstance(s, dict) and s.get("source_id")))
        found.append("status.json sources")
    return list(ids), found


def case_summary(case, domain_of) -> dict:
    a = Artifacts(case)
    prd = a.json("01-scope/prd.json")
    prd = prd if isinstance(prd, dict) else {}
    approvals = {x.phase_dir: x for x in a.approvals()}
    dod = [
        {
            "id": c.get("id"),
            "metric": c.get("metric"),
            "operator": c.get("operator") or ">=",
            "target": c.get("target"),
            "basis": c.get("basis"),
        }
        for c in prd.get("definition_of_done") or []
        if isinstance(c, dict)
    ]
    try:
        domain = domain_of(case)
    except SAFE_ERRORS:
        domain = None
    has_onto = bool(domain and domain.classes)
    onto = None
    if has_onto:
        onto = {
            "primary": domain.class_label(),
            "primary_plural": domain.class_label(plural=True),
            "n_classes": len(domain.classes),
            "n_properties": sum(len(v) for v in domain.properties.values()),
            "n_relations": len(domain.relations),
            "dod_props": [p.label for p in domain.dod_props()],
            "approved": (approvals.get("02-ontology").approved is not None) if approvals.get("02-ontology") else None,
        }
    runs = hc.runs_by_start(a)
    latest = a.latest_run_id() or (runs[-1] if runs else None)
    sources, sources_from = _sources(a, latest)
    gold = a.gold()
    output = None
    if gold is not None:
        m = gold.metrics or {}
        cls = gold.domain.primary_class
        per = (
            ((m.get("per_property_completeness") or {}).get(cls) or {})
            if isinstance(m.get("per_property_completeness"), dict)
            else {}
        )
        output = {
            "run_id": gold.run_id,
            "entities": len(gold.primary),
            "entities_all": len(gold.entities),
            "meeting_dod": _num((m.get("entities_meeting_dod") or {}).get(cls))
            if isinstance(m.get("entities_meeting_dod"), dict)
            else None,
            "n_values": sum(1 for v in gold.values.values() if v.data.get("status") == "gold"),
            "completeness": [{"label": p.label, "ratio": _num(per.get(p.id))} for p in gold.domain.dod_props()],
            "dod_met": sum(1 for r in m.get("dod") or [] if isinstance(r, dict) and r.get("met")),
            "dod_total": len([r for r in m.get("dod") or [] if isinstance(r, dict)]),
        }
    steps = hc.steps_of(a, latest)
    counts = hc.mode_counts(steps, hc.metrics_of(a, latest))
    total = sum(counts.values())
    return {
        "id": case.id,
        "title": case.title,
        "question": case.brief,
        "href": f"/cases/{case.id}",
        "prd": {
            "present": bool(prd),
            "personas": len(prd.get("personas") or []),
            "jobs": len(prd.get("jobs_to_be_done") or []),
            "requirements": len(prd.get("requirements") or []),
            "revisions": len(prd.get("revisions") or []),
            "dod": dod,
            "approved": (approvals.get("01-scope").approved is not None) if approvals.get("01-scope") else None,
        },
        "ontology": onto,
        "discovery": {"n_sources": len(sources), "sources": sources[:12], "from": sources_from},
        "output": output,
        "runs": {
            "n": len(runs),
            "latest": latest,
            "modes": counts,
            "n_moded": total,
            "mode_rows": hc.shares(
                [{"name": k, "steps": v} for k, v in counts.items()], "steps", colours=hc.MODE_COLOURS, keep_order=True
            ),
            "code_only": hc.code_only_share(counts),
            "usd": hc.run_step_usd(steps),
        },
    }


def cases_model(settings, domain_of, a_id: str | None = None, b_id: str | None = None) -> dict:
    ids = list(settings.cases)
    for x in (a_id, b_id):
        if x and x not in settings.cases:
            raise HTTPException(404, "unknown case")
    a_id = a_id or (ids[0] if ids else None)
    b_id = b_id or next((i for i in ids if i != a_id), None)
    cols = [case_summary(settings.cases[i], domain_of) for i in (a_id, b_id) if i]
    return {
        "a": a_id,
        "b": b_id,
        "case_ids": ids,
        "cols": cols,
        "empty": None
        if len(cols) == 2
        else gap(
            None,
            "Two registered cases side by side: question, PRD, ontology, discovery, output and runs.",
            "ONTOFILL_CONSOLE_CASES",
        ),
    }


# run vs run -------------------------------------------------------------------------------------------------------


def _run_side(a: Artifacts, rid: str) -> dict:
    steps = hc.steps_of(a, rid)
    gold = hc.gold_run(a, rid)
    metrics = hc.metrics_of(a, rid)
    counts = hc.mode_counts(steps, metrics)
    return {
        "run_id": rid,
        "started": hc.started(a, rid, steps),
        "gold": gold,
        "metrics": metrics,
        "counts": counts,
        "n_steps": len(steps),
        "usd": hc.run_step_usd(steps),
        "state": a.status(rid).get("state"),
        "steps": steps,
    }


def _delta(label: str, va, vb, fmt: str = "{:,}") -> dict:
    va, vb = _num(va), _num(vb)
    d = (vb - va) if va is not None and vb is not None else None
    direction = None if d is None else ("same" if abs(d) < 1e-12 else ("up" if d > 0 else "down"))

    def show(x) -> str:
        return fmt.format(x) if x is not None else "—"

    return {
        "label": label,
        "a": va,
        "b": vb,
        "delta": d,
        "dir": direction,
        "a_text": show(va),
        "b_text": show(vb),
        "delta_text": ("+" if d and d > 0 else "") + show(d),
    }


def runs_model(case, domain_of, run_a: str | None = None, run_b: str | None = None) -> dict:
    a = Artifacts(case)
    ordered = hc.runs_by_start(a)
    for r in (run_a, run_b):
        if r and r not in ordered:
            raise HTTPException(404, f"no run {r} in this case")
    run_b = run_b or (ordered[-1] if ordered else None)
    run_a = run_a or next((r for r in reversed(ordered) if r != run_b), None)
    base = {"case_id": case.id, "runs": ordered, "run_a": run_a, "run_b": run_b}
    if not run_a or not run_b:
        return {
            **base,
            "ready": False,
            "metrics": [],
            "props": [],
            "added": [],
            "removed": [],
            "changed": [],
            "n_added": 0,
            "n_removed": 0,
            "n_changed": 0,
            "modes": [],
            "entities_note": None,
            "backend": None,
            "empty": gap(
                "R11",
                f"Run vs run needs two runs of this case; it has {len(ordered)}. Metrics deltas, "
                "per-property completeness, entities added or removed and values changed appear "
                "here, including after a re-refine from bronze with the current ontology.",
                "runs/<case>/*/status.json · gold/<case>/<run>/metrics.json, entities.jsonl",
            ),
        }
    A, B = _run_side(a, run_a), _run_side(a, run_b)
    try:
        domain = (B["gold"] or A["gold"]).domain if (B["gold"] or A["gold"]) else domain_of(case)
    except SAFE_ERRORS:
        domain = None
    cls = domain.primary_class if domain else None

    def cm(side: dict, key: str):
        v = side["metrics"].get(key)
        return v.get(cls) if isinstance(v, dict) and cls else v

    def n_gold(side: dict):
        g = side["gold"]
        return sum(1 for v in g.values.values() if v.data.get("status") == "gold") if g else None

    metrics = [
        _delta(
            f"{domain.class_label(plural=True) if domain else 'Entities'} (gold)",
            cm(A, "entities_total"),
            cm(B, "entities_total"),
        ),
        _delta("Meeting the definition of done", cm(A, "entities_meeting_dod"), cm(B, "entities_meeting_dod")),
        _delta("Gold values", n_gold(A), n_gold(B)),
        _delta(
            "Distinct source classes",
            A["metrics"].get("distinct_source_classes"),
            B["metrics"].get("distinct_source_classes"),
        ),
        _delta(
            "Values without evidence",
            A["metrics"].get("values_without_evidence"),
            B["metrics"].get("values_without_evidence"),
        ),
        _delta("Steps", A["n_steps"], B["n_steps"]),
        _delta("Priced spend ($)", A["usd"], B["usd"], "{:,.4f}"),
    ]
    props = []
    if domain:
        pa = (
            (A["metrics"].get("per_property_completeness") or {}).get(cls) or {}
            if isinstance(A["metrics"].get("per_property_completeness"), dict)
            else {}
        )
        pb = (
            (B["metrics"].get("per_property_completeness") or {}).get(cls) or {}
            if isinstance(B["metrics"].get("per_property_completeness"), dict)
            else {}
        )
        for p in domain.dod_props():
            row = _delta(p.label, pa.get(p.id), pb.get(p.id), "{:.1%}")
            row["a_pct"] = round((row["a"] or 0) * 100, 1)
            row["b_pct"] = round((row["b"] or 0) * 100, 1)
            props.append(row)
    added, removed, changed, entities_note = [], [], [], None
    ga, gb = A["gold"], B["gold"]
    if ga is not None and gb is not None:
        ia, ib = set(ga.entities_by_id), set(gb.entities_by_id)
        added = [
            {"id": e, "title": gb.title(gb.entities_by_id[e]), "class": gb.entities_by_id[e].get("class")}
            for e in sorted(ib - ia)
        ]
        removed = [
            {"id": e, "title": ga.title(ga.entities_by_id[e]), "class": ga.entities_by_id[e].get("class")}
            for e in sorted(ia - ib)
        ]
        for eid in sorted(ia & ib):
            ea, eb = ga.entities_by_id[eid], gb.entities_by_id[eid]
            fa, fb = ea.get("properties") or {}, eb.get("properties") or {}
            for prop in sorted(set(fa) | set(fb)):
                va = fa.get(prop) if isinstance(fa.get(prop), dict) else {}
                vb = fb.get(prop) if isinstance(fb.get(prop), dict) else {}
                if (va.get("value"), va.get("status")) != (vb.get("value"), vb.get("status")):
                    changed.append(
                        {
                            "entity_id": eid,
                            "title": gb.title(eb),
                            "prop": domain.prop_label(prop) if domain else prop,
                            "a": va.get("value"),
                            "a_status": va.get("status") or "missing",
                            "b": vb.get("value"),
                            "b_status": vb.get("status") or "missing",
                        }
                    )
    else:
        missing = [r for r, g in ((run_a, ga), (run_b, gb)) if g is None]
        entities_note = gap(
            "R11",
            "Entities added or removed and values changed need the gold export of both runs; "
            f"missing for {', '.join(missing)}.",
            "gold/<case>/<run>/entities.jsonl",
        )
    sa, sb = hc.code_only_share(A["counts"]), hc.code_only_share(B["counts"])
    modes = [_delta(k, A["counts"][k], B["counts"][k]) for k in hc.MODES]
    modes.append(_delta("Code-only share (D0+D1)", sa, sb, "{:.1%}"))
    return {
        **base,
        "ready": True,
        "empty": None,
        "a_info": {"run_id": run_a, "started": A["started"], "state": A["state"], "gold": ga is not None},
        "b_info": {"run_id": run_b, "started": B["started"], "state": B["state"], "gold": gb is not None},
        "metrics": metrics,
        "props": props,
        "added": added[:SAMPLE],
        "removed": removed[:SAMPLE],
        "changed": changed[:SAMPLE],
        "n_added": len(added),
        "n_removed": len(removed),
        "n_changed": len(changed),
        "modes": modes,
        "entities_note": entities_note,
        "backend": backend_of(B["metrics"], [*A["steps"], *B["steps"]]),
    }


def install(ctx: VizContext) -> None:
    app, render, settings = ctx.app, ctx.render, ctx.settings
    ctx.global_view("compare", "/compare", "Compare", ORDER)
    ctx.case_view("compare-runs", "compare", "Compare runs", ORDER)

    @app.get("/compare", response_class=HTMLResponse)
    def compare_cases(request: Request, a: str | None = None, b: str | None = None):
        return render(request, "viz/compare.html", nav="compare", m=cases_model(settings, ctx.case_domain, a, b))

    @app.get("/api/viz/compare")
    def compare_cases_api(a: str | None = None, b: str | None = None) -> dict:
        return cases_model(settings, ctx.case_domain, a, b)

    @app.get("/cases/{case_id}/compare", response_class=HTMLResponse)
    def compare_runs(request: Request, case_id: str, run_a: str | None = None, run_b: str | None = None):
        case = ctx.get_case(case_id)
        m = runs_model(case, ctx.case_domain, run_a, run_b)
        return render(request, "viz/compare_runs.html", nav="compare-runs", case=case, m=m, backend=m["backend"])

    @app.get("/cases/{case_id}/api/viz/compare")
    def compare_runs_api(case_id: str, run_a: str | None = None, run_b: str | None = None) -> dict:
        return runs_model(ctx.get_case(case_id), ctx.case_domain, run_a, run_b)
