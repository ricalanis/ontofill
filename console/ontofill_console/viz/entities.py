"""Q5 · Why is this value here? A generic entity browser over a run's gold entities, an entity page with every value's
evidence one click away, and a lineage journal per value:
value → evidence → step(s) → plan (TDD) → objective → ontology → PRD (DoD criterion) → brief.

Reads `gold/<case>/<run>/entities.jsonl`, `trace.jsonl`, `ontology.json`, `dod-queries.json`, `metrics.json`,
bronze sidecars, the run's live trace as a fallback for lineage, and the case package (`04-local/*/tdd.md`,
`03-fanout/objectives.yaml`, `01-scope/prd.json`, `brief.md`).
"""

from __future__ import annotations

import re

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import dod, live
from ..domain import humanize
from .core import Artifacts, VizContext, gap
from .output_common import (
    STATE_LABELS,
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

ORDER = 55
LIST_CAP = 200
COMPLETENESS = {"": "any", "complete": "meets the DoD share", "partial": "partly filled", "none": "nothing filled"}


def _gold(a: Artifacts, run: str | None):
    g, run_id, note = resolve_run(a, run)
    return g, run_id, note


def _ratio(entity: dict, props) -> tuple[float | None, int, int]:
    dod_props = [p for p in props if p.dod]
    if not dod_props:
        return None, 0, 0
    values = entity.get("properties") or {}
    filled = sum(dod.is_filled(values.get(p.id)) for p in dod_props)
    return round(filled / len(dod_props), 4), filled, len(dod_props)


def _run_q(run_id: str | None) -> str:
    return f"?run={q(run_id)}" if run_id else ""


# browser ---------------------------------------------------------------------------------------------------------
def list_model(case, run: str | None = None, text: str = "", cls: str = "", completeness: str = "",
               show_all: bool = False) -> dict:
    a = Artifacts(case)
    g, run_id, note = _gold(a, run)
    text, cls = (text or "").strip()[:200], (cls or "").strip()[:100]
    completeness = completeness if completeness in COMPLETENESS else ""
    base = {"case_id": case.id, "run_id": run_id, "gold_run_ids": a.gold_run_ids(), "brief": brief_text(a),
            "filters": {"q": text, "cls": cls, "completeness": completeness}, "completeness_options": COMPLETENESS}
    if g is None:
        return {**base, "backend": None, "classes": [], "rows": [], "n_rows": 0, "n_total": 0, "capped": False,
                "threshold": None,
                "empty": gap(None, "Every entity of the run with its class, title, identifier and share of "
                                   "definition-of-done properties filled; each opens its values and evidence.",
                             "gold/<case>/<run>/entities.jsonl (written when a run publishes gold)")}
    domain = domain_for(g)
    counts: dict[str, int] = {}
    for e in g.entities:
        counts[e.get("class") or "?"] = counts.get(e.get("class") or "?", 0) + 1
    known = list(domain.classes) + [c for c in counts if c not in domain.classes]
    classes = [{"id": c, "label": domain.class_label(c) if c in domain.classes else humanize(c),
                "n": counts.get(c, 0), "primary": c == domain.primary_class} for c in known]
    needle = text.lower()
    rows = []
    for e in g.entities:
        ecls = e.get("class") or "?"
        if cls and ecls != cls:
            continue
        props, d = props_for(domain, g, ecls)
        title, ident = entity_title(d, e), entity_identifier(d, e)
        if needle and not any(needle in str(x).lower() for x in (title, ident or "", e.get("id"))):
            continue
        ratio, filled, n_dod = _ratio(e, props)
        share = ratio or 0.0
        if completeness == "complete" and not (ratio is not None and share >= domain.dod_threshold - 1e-9):
            continue
        if completeness == "partial" and not (0 < share < domain.dod_threshold - 1e-9):
            continue
        if completeness == "none" and share > 0:
            continue
        rows.append({"entity_id": e["id"], "class": ecls,
                     "class_label": domain.class_label(ecls) if ecls in domain.classes else humanize(ecls),
                     "title": title, "identifier": ident, "ratio": ratio, "filled": filled, "n_dod": n_dod,
                     "flags": len(e.get("flags") or []), "links": len(e.get("links") or []),
                     "href": f"/cases/{case.id}/entities/{q(e['id'])}{_run_q(run_id)}"})
    rows.sort(key=lambda r: (r["class"] != domain.primary_class, r["class"], -(r["ratio"] or 0), str(r["title"]).lower()))
    n = len(rows)
    return {**base, "backend": g.inference_backend, "classes": classes, "rows": rows if show_all else rows[:LIST_CAP],
            "n_rows": n, "n_total": len(g.entities), "capped": not show_all and n > LIST_CAP,
            "threshold": domain.dod_threshold, "empty": None}


# one entity ------------------------------------------------------------------------------------------------------
def entity_model(case, entity_id: str, run: str | None = None) -> dict:
    a = Artifacts(case)
    g, run_id, _ = _gold(a, run)
    e = g.entities_by_id.get(entity_id) if g is not None else None
    if e is None:
        raise HTTPException(404, f"no entity {entity_id} in this run")
    domain = domain_for(g)
    cls = e.get("class") or "?"
    props, d = props_for(domain, g, cls)
    values = e.get("properties") or {}
    ordered = [(p.id, p) for p in props] + [(k, None) for k in values if k not in {p.id for p in props}]
    rq = _run_q(run_id)
    rows = []
    for pid, p in ordered:
        f = values.get(pid) if isinstance(values.get(pid), dict) else None
        vid = (f or {}).get("value_id")
        rows.append({"id": pid, "label": p.label if p else d.prop_label(pid), "dod": bool(p and p.dod),
                     "datatype": p.datatype if p else "", "description": p.description if p else "",
                     "in_ontology": p is not None, "state": value_state(f), "status": (f or {}).get("status") or "missing",
                     "value": (f or {}).get("value"), "confidence": (f or {}).get("confidence"), "value_id": vid,
                     "backend": ((f or {}).get("generated_by") or {}).get("backend"),
                     "evidence": [evidence_view(case.id, a, ev) for ev in (f or {}).get("evidence") or []],
                     "step_ids": [s.get("step_id") for s in g.steps_by_value.get(vid or "", [])],
                     "lineage_href": f"/cases/{case.id}/lineage/{q(vid)}{rq}" if vid else None})
    by_value = {vid: ref.prop for vid, ref in g.values.items() if ref.entity_id == e["id"]}
    links = []
    for ln in e.get("links") or []:
        if not isinstance(ln, dict):
            continue
        t = g.entities_by_id.get(ln.get("target") or "")
        rel = ln.get("property") or ln.get("type") or ""
        via = ln.get("via_value_id")
        links.append({"property": rel, "label": d.relation_label(rel), "target": ln.get("target"),
                      "target_title": entity_title(domain, t) if t else ln.get("target"),
                      "href": f"/cases/{case.id}/entities/{q(ln['target'])}{rq}" if t else None,
                      "via_value_id": via, "via_href": f"/cases/{case.id}/lineage/{q(via)}{rq}" if via else None})
    flags = []
    for fl in e.get("flags") or []:
        if not isinstance(fl, dict):
            continue
        rule = d.rule(str(fl.get("rule_id") or ""), fl.get("label") or "")
        flags.append({"rule_id": fl.get("rule_id"), "label": fl.get("label") or rule["label"],
                      "explanation": fl.get("explanation"), "verify": rule["verify"],
                      "evidence": [{"value_id": v, "prop": by_value.get(v), "href": f"#p-{by_value[v]}" if v in by_value
                                    else f"/cases/{case.id}/lineage/{q(v)}{rq}"} for v in fl.get("evidence_value_ids") or []]})
    backlinks = [{"entity_id": x["id"], "title": entity_title(domain, x), "property": d.relation_label(ln.get("property") or ""),
                  "href": f"/cases/{case.id}/entities/{q(x['id'])}{rq}"}
                 for x in g.entities for ln in x.get("links") or [] if isinstance(ln, dict) and ln.get("target") == e["id"]][:30]
    ratio, filled, n_dod = _ratio(e, props)
    return {"case_id": case.id, "run_id": run_id, "backend": g.inference_backend, "brief": brief_text(a),
            "entity_id": e["id"], "class": cls,
            "class_label": d.class_label(cls) if cls in d.classes else humanize(cls),
            "title": entity_title(d, e), "identifier": entity_identifier(d, e), "classified_as": e.get("classified_as") or [],
            "ratio": ratio, "filled": filled, "n_dod": n_dod, "props": rows, "links": links, "flags": flags,
            "backlinks": backlinks, "inferred_ontology": d is not g.domain,
            "empty_flags": None if flags else gap("R6", "Rule outputs the ontology defines, each with the values that "
                                                        "triggered it.", "entities.jsonl flags[]"),
            "list_href": f"/cases/{case.id}/entities{rq}"}


# lineage ---------------------------------------------------------------------------------------------------------
def _step_view(case_id: str, s: dict, live_ids: list[str]) -> dict:
    rid = s.get("run_id")
    return {"step_id": s.get("step_id"), "phase": s.get("phase"), "phase_name": live.phase_name(s.get("phase")),
            "mode": s.get("mode"), "mode_name": live.MODE_NAMES.get(s.get("mode") or "", s.get("mode")),
            "source_id": s.get("source_id"), "objective_id": s.get("objective_id"), "tdd_path": s.get("tdd_path"),
            "observed": s.get("observed"), "requested": s.get("requested"), "executed": s.get("executed"),
            "evaluated": s.get("evaluated"), "ts": s.get("ts"), "parent_step_id": s.get("parent_step_id"),
            "backend": (s.get("generated_by") or {}).get("backend") if isinstance(s.get("generated_by"), dict) else None,
            "href": f"/cases/{case_id}/runs/{rid}#{s.get('step_id')}" if rid and rid in live_ids else None}


def _live_chains(steps: list[dict], value_id: str) -> list[list[dict]]:
    by_id = {s.get("step_id"): s for s in steps}
    chains = []
    for s in steps:
        if value_id not in (s.get("value_ids") or []):
            continue
        chain, seen, cur = [], set(), s
        while cur and cur.get("step_id") not in seen:
            seen.add(cur.get("step_id"))
            chain.append(cur)
            cur = by_id.get(cur.get("parent_step_id") or "")
        chains.append(chain)
    return chains


def _mentions(text, *needles) -> bool:
    t = str(text or "").lower()
    return any(n and re.search(rf"(?<![a-z0-9_]){re.escape(n.lower())}(?![a-z0-9_])", t) for n in needles)


def lineage_model(case, value_id: str, run: str | None = None) -> dict:
    a = Artifacts(case)
    g, run_id, _ = _gold(a, run)
    ref = g.values.get(value_id) if g is not None else None
    if ref is None:
        raise HTTPException(404, f"no value {value_id} in this run")
    e = g.entities_by_id.get(ref.entity_id) or {}
    domain = domain_for(g)
    cls = e.get("class") or "?"
    props, d = props_for(domain, g, cls)
    p = next((x for x in props if x.id == ref.prop), None)
    f = ref.data
    rq = _run_q(run_id)
    live_ids = a.run_ids()

    # value → evidence
    evidence = [evidence_view(case.id, a, ev) for ev in f.get("evidence") or []]
    # → steps (gold trace, else the run's live trace)
    chains, steps_from = g.lineage(value_id), "gold trace.jsonl"
    if not chains and run_id in live_ids:
        chains, steps_from = _live_chains(a.steps(run_id), value_id), "live trace.live.jsonl"
    chain_views = [[_step_view(case.id, s, live_ids) for s in c] for c in chains]
    producing = [c[0] for c in chains if c]
    all_steps = [s for c in chains for s in c]
    # → plan (TDD)
    tdd_paths = list(dict.fromkeys(s.get("tdd_path") for s in all_steps if s.get("tdd_path")))
    matched_by = "named in the trace"
    source_ids = list(dict.fromkeys([s.get("source_id") for s in producing if s.get("source_id")]
                                    + [ev["source_id"] for ev in evidence if ev.get("source_id")]))
    if not tdd_paths:
        for sid in source_ids:
            tdd_paths += a.glob(f"04-local/{sid}__*/tdd.md")
        matched_by = "matched by source id (the trace names no plan)"
    plans = []
    for path in tdd_paths:
        text = a.text(path, limit=40_000)
        lines = [ln for ln in (text or "").splitlines() if ln.strip()]
        plans.append({"path": path, "exists": text is not None, "href": f"/cases/{case.id}/files/{path}",
                      "title": next((ln.lstrip("#").strip() for ln in lines if ln.startswith("#")), path),
                      "excerpt": "\n".join(lines[1:9]) if lines else None, "matched_by": matched_by})
    # → objective
    objectives_doc = a.yaml("03-fanout/objectives.yaml")
    objectives = objectives_doc.get("objectives") if isinstance(objectives_doc, dict) else None
    obj_ids = {(s.get("objective_id"), s.get("source_id")) for s in all_steps if s.get("objective_id")}
    obj_rows = []
    for o in objectives or []:
        if not isinstance(o, dict):
            continue
        by_step = any(o.get("id") == oid and (not sid or o.get("source_id") == sid) for oid, sid in obj_ids)
        by_field = o.get("source_id") in source_ids and ref.prop in (o.get("target_fields") or [])
        if by_step or by_field:
            obj_rows.append({"id": o.get("id"), "source_id": o.get("source_id"), "source_url": o.get("source_url"),
                             "target_fields": o.get("target_fields") or [], "priority": o.get("priority"),
                             "expected_contribution": o.get("expected_contribution"),
                             "authority_tier": o.get("authority_tier"),
                             "matched_by": "objective id in the trace" if by_step else "source and target field"})
    for oid, sid in sorted(obj_ids, key=str):
        if not any(o["id"] == oid for o in obj_rows):
            obj_rows.append({"id": oid, "source_id": sid, "source_url": None, "target_fields": [], "priority": None,
                             "expected_contribution": None, "authority_tier": None,
                             "matched_by": "named in the trace; not in objectives.yaml"})
    # → ontology
    ontology = {"class": cls, "class_label": d.class_label(cls) if cls in d.classes else humanize(cls),
                "class_description": (d.classes.get(cls) or {}).get("description"),
                "property": ref.prop, "label": p.label if p else d.prop_label(ref.prop),
                "datatype": p.datatype if p else None, "description": p.description if p else None,
                "dod": bool(p and p.dod), "aligned_to": p.aligned_to if p else None, "in_ontology": p is not None,
                "inferred": d is not g.domain,
                "href": f"/cases/{case.id}/files/02-ontology/ontology.json" if a.dir.exists("02-ontology/ontology.json")
                else None}
    # → PRD (DoD criteria that use the property)
    label = ontology["label"]
    queries = []
    for qd in g.dod_queries:
        if not isinstance(qd, dict):
            continue
        qp = qd.get("properties")
        uses = (isinstance(qp, list) and ref.prop in qp) or (qp == "dod" and ontology["dod"]) \
            or any(c.get("property") == ref.prop for c in qd.get("conditions") or [] if isinstance(c, dict)) \
            or (qd.get("aggregate") == "entities_meeting_completeness" and qp is None and ontology["dod"])
        if uses:
            queries.append({"criterion_id": qd.get("criterion_id"), "query": dod.query_text(qd),
                            "target": qd.get("target"), "matched_by": "dod-queries.json"})
    if not queries:
        for row in (g.metrics.get("dod") or []) if isinstance(g.metrics.get("dod"), list) else []:
            if isinstance(row, dict) and _mentions(row.get("query"), ref.prop):
                queries.append({"criterion_id": row.get("criterion_id"), "query": row.get("query"),
                                "target": row.get("target"), "matched_by": "metrics.json dod[] query text"})
    if not queries and ontology["dod"]:
        queries.append({"criterion_id": "dod_properties", "query": f"{ref.prop} is a definition-of-done property",
                        "target": None, "matched_by": "ontology dod flag"})
    prd = a.json("01-scope/prd.json")
    prd_dod = [c for c in (prd.get("definition_of_done") or []) if isinstance(c, dict)] if isinstance(prd, dict) else []
    crit_ids = {qq["criterion_id"] for qq in queries}
    prd_rows = []
    for c in prd_dod:
        direct = c.get("id") in crit_ids or c.get("metric") in crit_ids
        text_hit = _mentions(" ".join(str(c.get(k) or "") for k in ("metric", "basis_quote", "description")),
                             ref.prop, label)
        prd_rows.append({"id": c.get("id"), "metric": c.get("metric"), "operator": c.get("operator"),
                         "target": c.get("target"), "basis": c.get("basis"), "basis_quote": c.get("basis_quote"),
                         "linked": direct or text_hit,
                         "matched_by": "criterion id" if direct else ("mentions the property" if text_hit else None)})
    linked = [r for r in prd_rows if r["linked"]]
    brief = brief_text(a)
    return {"case_id": case.id, "run_id": run_id, "backend": g.inference_backend,
            "value": {"value_id": value_id, "entity_id": ref.entity_id, "entity_title": entity_title(d, e),
                      "entity_href": f"/cases/{case.id}/entities/{q(ref.entity_id)}{rq}#p-{ref.prop}",
                      "prop": ref.prop, "label": label, "value": f.get("value"), "status": f.get("status"),
                      "state": value_state(f), "confidence": f.get("confidence"),
                      "backend": (f.get("generated_by") or {}).get("backend")},
            "evidence": evidence, "chains": chain_views, "steps_from": steps_from if chains else None,
            "step_ids": [s.get("step_id") for s in producing],
            "plans": plans, "objectives": obj_rows, "objectives_file": objectives is not None,
            "ontology": ontology, "dod_queries": queries,
            "prd": {"exists": isinstance(prd, dict), "linked": linked, "others": [r for r in prd_rows if not r["linked"]],
                    "href": f"/cases/{case.id}/files/01-scope/prd.json" if isinstance(prd, dict) else None},
            "brief": brief, "brief_href": f"/cases/{case.id}/files/brief.md" if brief else None,
            "empty_steps": None if chains else gap("R2", "The step that produced this value, its parents, and what each "
                                                          "observed, requested, executed and evaluated.",
                                                   "trace.jsonl value_ids[] · parent_step_id"),
            "empty_plan": None if plans else gap("R2", "The local plan (TDD) for this source and objective.",
                                                 "04-local/<source>__<objective>/tdd.md · trace tdd_path"),
            "empty_objective": None if obj_rows else gap(
                None, "The Phase 3 objective that sent the engine to this source.",
                "03-fanout/objectives.yaml")}


def install(ctx: VizContext) -> None:
    app, render = ctx.app, ctx.render
    ctx.case_view("entities", "entities", "Entities", 55)

    @app.get("/cases/{case_id}/entities", response_class=HTMLResponse)
    def entities_page(request: Request, case_id: str, run: str | None = None, q: str = "", cls: str = "",
                      completeness: str = "", all: int = 0):
        case = ctx.get_case(case_id)
        m = list_model(case, run, q, cls, completeness, bool(all))
        return render(request, "viz/entities.html", nav="entities", case=case, m=m, backend=m["backend"])

    @app.get("/cases/{case_id}/api/viz/entities")
    def entities_api(case_id: str, run: str | None = None, q: str = "", cls: str = "", completeness: str = "",
                     all: int = 0) -> dict:
        return list_model(ctx.get_case(case_id), run, q, cls, completeness, bool(all))

    @app.get("/cases/{case_id}/entities/{entity_id:path}", response_class=HTMLResponse)
    def entity_page(request: Request, case_id: str, entity_id: str, run: str | None = None):
        case = ctx.get_case(case_id)
        m = entity_model(case, entity_id, run)
        return render(request, "viz/entity.html", nav="entities", case=case, m=m, backend=m["backend"],
                      STATE_LABELS=STATE_LABELS)

    @app.get("/cases/{case_id}/api/viz/entities/{entity_id:path}")
    def entity_api(case_id: str, entity_id: str, run: str | None = None) -> dict:
        return entity_model(ctx.get_case(case_id), entity_id, run)

    @app.get("/cases/{case_id}/lineage/{value_id:path}", response_class=HTMLResponse)
    def lineage_page(request: Request, case_id: str, value_id: str, run: str | None = None):
        case = ctx.get_case(case_id)
        m = lineage_model(case, value_id, run)
        return render(request, "viz/lineage.html", nav="entities", case=case, m=m, backend=m["backend"],
                      STATE_LABELS=STATE_LABELS)

    @app.get("/cases/{case_id}/api/viz/lineage/{value_id:path}")
    def lineage_api(case_id: str, value_id: str, run: str | None = None) -> dict:
        return lineage_model(ctx.get_case(case_id), value_id, run)
