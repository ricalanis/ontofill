"""Q3 · Where did it look, and why did it trust or reject each source?

The phase 3 discovery funnel per lead provider (leads → captured → critic-accepted → authority-passed → sources), the
provider usage (calls, credits, cache hits), every captured candidate with its authority decision and reason, the
objectives each accepted source became, and the phase 4 plan per (source, objective). Reads
`03-fanout/surface-map/leads.json`, `03-fanout/surface-map/discovery.json`, `03-fanout/sources/<id>/candidate.json`
(+ its source checkpoint), `03-fanout/objectives.yaml|json`, `03-fanout/surface-map/<source>/site-graph.json`,
`04-local/<source>__<objective>/{tdd.json, local-prd.json, tdd.md}` and the run's trace.
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse

from .. import live
from ..approvals import _front_matter
from ..gold import backend_of
from .core import Artifacts, VizContext, gap
from .definition import file_href, gen_by, resolve_run, run_metrics

ORDER = 30
STAGES = (
    ("leads", "Leads"),
    ("captured", "Captured"),
    ("accepted", "Critic-accepted"),
    ("passed", "Authority-passed"),
    ("sources", "Sources"),
)
DECISION_STATE = {"trusted": "done", "review": "need", "rejected": "block", "not captured": "none"}
MODES = ("D0", "D1", "S1", "S2")


def _dict(x) -> dict:
    return x if isinstance(x, dict) else {}


def _list(x) -> list:
    return x if isinstance(x, list) else []


def host(url) -> str:
    try:
        return (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def objectives_doc(a: Artifacts) -> tuple[list[dict], str | None]:
    for rel, loader in (("03-fanout/objectives.yaml", a.yaml), ("03-fanout/objectives.json", a.json)):
        doc = loader(rel)
        if isinstance(doc, dict) and isinstance(doc.get("objectives"), list):
            return [o for o in doc["objectives"] if isinstance(o, dict)], rel
    return [], None


def source_checkpoint(a: Artifacts, case_id: str, source_id: str | None) -> dict | None:
    """The human answer to a source checkpoint (03-fanout/sources/<id>/APPROVED), or its pending request."""
    if not source_id:
        return None
    base = f"03-fanout/sources/{source_id}"
    marker = a.json(f"{base}/APPROVED")
    # The console's review pages cover the prd/factors/ontology/action checkpoints only; a source checkpoint is
    # shown as its request file until a review page exists for it.
    href = file_href(case_id, f"{base}/APPROVAL_PENDING.md")
    if isinstance(marker, dict):
        deny = marker.get("decision") == "deny"
        return {
            "state": "block" if deny else "done",
            "decision": "deny" if deny else "approve",
            "approver": marker.get("approver"),
            "date": marker.get("date"),
            "reason": marker.get("reason"),
            "href": href,
        }
    if a.text(f"{base}/APPROVAL_PENDING.md") is not None:
        return {"state": "need", "decision": "pending", "approver": None, "date": None, "reason": None, "href": href}
    return None


def source_pending(a: Artifacts, case_id: str) -> list[dict]:
    """Source checkpoints waiting for a person: 03-fanout/sources/<id>/APPROVAL_PENDING.md without APPROVED."""
    out = []
    for rel in a.glob("03-fanout/sources/*/APPROVAL_PENDING.md"):
        sid = rel.split("/")[2]
        if a.text(f"03-fanout/sources/{sid}/APPROVED") is not None:
            continue
        meta = _front_matter(a.text(rel) or "")
        doc = a.json(f"03-fanout/sources/{sid}/candidate.json") or {}
        out.append(
            {
                "state": "need",
                "title": f"Source {host(_dict(doc).get('url')) or sid}: waiting for your review",
                "detail": " · ".join(str(x) for x in (f"03-fanout/sources/{sid}", meta.get("reason")) if x),
                "meta": str(meta.get("requested_at") or "")[:16].replace("T", " "),
                "href": file_href(case_id, rel),
            }
        )
    return out


def decide(c: dict, checkpoint: dict | None) -> tuple[str, str]:
    """(decision, reason) for one candidate: trusted | review | rejected | not captured."""
    status = c.get("status")
    if status == "capture_failed":
        return "rejected", f"capture failed: {c.get('capture_reason') or 'unknown'}"
    if status == "pending" or not status:
        return "not captured", "chosen for capture; no capture recorded"
    if status == "rejected":
        critic = _dict(c.get("critic"))
        return "rejected", "; ".join(f"{k}: {v}" for k, v in critic.items()) or "the critic found no supported property"
    if status == "captured":
        return "review", "captured, not yet reviewed by the critic"
    reason = c.get("authority_reason") or ""
    if c.get("authority") == "auto":
        return "trusted", reason or "approved publisher"
    if checkpoint and checkpoint["decision"] == "approve":
        return "trusted", f"approved by {checkpoint['approver']}" + (f" ({reason})" if reason else "")
    if checkpoint and checkpoint["decision"] == "deny":
        return "rejected", f"denied by {checkpoint['approver']}: {checkpoint.get('reason') or 'no reason'}"
    return "review", reason or "publisher authority needs human review"


def funnels(leads: list[dict], candidates: list[dict], objectives: list[dict], yield_by: dict) -> list[dict]:
    names = list(
        dict.fromkeys(
            [
                *yield_by,
                *(p for lead in leads for p in _list(lead.get("providers")) or [lead.get("discovered_by")] if p),
                *(p for c in candidates for p in _list(c.get("providers")) if p),
            ]
        )
    )
    rows = []
    for name in names:
        y = _dict(yield_by.get(name))
        if leads or candidates:
            mine = [c for c in candidates if name in (_list(c.get("providers")) or [c.get("discovered_by")])]
            n = {
                "leads": sum(
                    1 for lead in leads if name in (_list(lead.get("providers")) or [lead.get("discovered_by")])
                ),
                "captured": sum(1 for c in mine if c.get("status") in ("captured", "confirmed", "rejected")),
                "accepted": sum(1 for c in mine if c.get("status") == "confirmed"),
                "passed": sum(1 for c in mine if c.get("decision") == "trusted"),
                "sources": sum(
                    1
                    for o in objectives
                    if (o.get("discovery_provider") or _dict(o.get("discovered_by")).get("provider")) == name
                ),
            }
            basis = "leads.json"
        else:  # only the round ledger survived: its per-provider yield (sources there = confirmed and auto-trusted)
            n = {
                "leads": y.get("leads"),
                "captured": y.get("captured"),
                "accepted": y.get("confirmed"),
                "passed": y.get("sources"),
                "sources": None,
            }
            basis = "discovery.json provider_yield"
        top = max([v for v in n.values() if isinstance(v, int)] + [1])
        stages = [
            {"key": k, "label": label, "n": n[k], "pct": round(100 * (n[k] or 0) / top, 1)} for k, label in STAGES
        ]
        rows.append(
            {
                "provider": name,
                "stages": stages,
                "basis": basis,
                "calls": y.get("calls"),
                "credits": y.get("credits"),
                "cache_hits": y.get("cache_hits"),
                "n_leads": n["leads"],
                "n_sources": n["sources"],
            }
        )
    return rows


def plans(a: Artifacts, case_id: str) -> list[dict]:
    dirs: dict[str, list[str]] = {}
    for rel in a.glob("04-local/*/*"):
        parts = rel.split("/")
        if len(parts) == 3 and not parts[2].startswith("."):
            dirs.setdefault(parts[1], []).append(rel)
    out = []
    for name, files in sorted(dirs.items()):
        source_id, _, objective_id = name.partition("__")
        base = f"04-local/{name}"
        tdd = a.json(f"{base}/tdd.json")
        tdd = tdd if isinstance(tdd, dict) else None
        lprd = a.json(f"{base}/local-prd.json")
        lprd = lprd if isinstance(lprd, dict) else None
        md = a.text(f"{base}/tdd.md", limit=4000)
        title = next((ln.lstrip("#").strip() for ln in (md or "").splitlines() if ln.startswith("#")), None)
        steps, modes = [], set()
        for s in _list((tdd or {}).get("steps")):
            if isinstance(s, dict):
                allowed = [m for m in _list(s.get("allowed_modes")) if m in MODES]
                modes.update(allowed + ([s["starting_mode"]] if s.get("starting_mode") in MODES else []))
                steps.append(
                    {
                        "id": s.get("id"),
                        "description": s.get("description"),
                        "start": s.get("starting_mode"),
                        "allowed": allowed,
                        "channel": s.get("observation_channel"),
                        "risk": s.get("risk_tier"),
                        "until": s.get("termination_predicate"),
                    }
                )
        ranked = sorted(modes, key=lambda m: live.MODE_RANK.get(m, 9))
        url = (tdd or {}).get("source_url")
        path = urlsplit(url).path if url else None
        raw = f"{base}/tdd.json" if tdd else (f"{base}/tdd.md" if md is not None else files[0])
        out.append(
            {
                "dir": base,
                "source_id": source_id,
                "objective_id": objective_id or None,
                "parsed": tdd is not None,
                "title": title,
                "raw_href": file_href(case_id, raw),
                "raw_path": raw,
                "files": [{"path": f, "href": file_href(case_id, f)} for f in files],
                "source_url": url,
                "url_path": path or None,
                "target_fields": [str(x) for x in _list((tdd or lprd or {}).get("target_fields"))],
                "method": (tdd or {}).get("extraction_method"),
                "mode_range": (ranked[0] if len(ranked) == 1 else f"{ranked[0]}–{ranked[-1]}") if ranked else None,
                "allowed_domains": [str(x) for x in _list((tdd or {}).get("allowed_domains"))],
                "budget_usd": (tdd or {}).get("budget_usd"),
                "rate": (tdd or {}).get("rate_limit_per_minute"),
                "volume": (tdd or {}).get("target_volume"),
                "tools": [str(x) for x in _list((tdd or {}).get("allowed_tools"))],
                "steps": steps,
                "local_dod": [
                    {"metric": c.get("metric"), "operator": c.get("operator"), "target": c.get("target")}
                    for c in _list((lprd or {}).get("local_definition_of_done"))
                    if isinstance(c, dict)
                ],
                "requirements": [str(x) for x in _list((lprd or {}).get("global_requirement_ids"))],
                "generated_by": gen_by(tdd),
            }
        )
    return out


def trace_sources(steps: list[dict], plan_dirs: dict[str, str], case_id: str) -> list[dict]:
    """Sources the run trace names (phase 3 mapping, phase 4 scoping, phase 5 steps) when no candidate ledger exists."""
    rows: dict[str, dict] = {}
    for s in steps:
        sid = s.get("source_id")
        if not sid:
            continue
        r = rows.setdefault(sid, {"source_id": sid, "objectives": [], "by_phase": {}, "first": None, "tdd_path": None})
        if s.get("objective_id") and s["objective_id"] not in r["objectives"]:
            r["objectives"].append(s["objective_id"])
        ph = s.get("phase")
        r["by_phase"][str(ph)] = r["by_phase"].get(str(ph), 0) + 1
        if ph == 3 and r["first"] is None:
            r["first"] = s.get("observed") or s.get("requested")
        if s.get("tdd_path") and not r["tdd_path"]:
            r["tdd_path"] = s["tdd_path"]
    out = []
    for r in rows.values():
        plan = r["tdd_path"] or next((p for d, p in plan_dirs.items() if d.startswith(r["source_id"] + "__")), None)
        out.append(
            {
                **r,
                "phases": [{"phase": k, "n": v} for k, v in sorted(r["by_phase"].items())],
                "n_steps": sum(r["by_phase"].values()),
                "plan_href": file_href(case_id, plan) if plan else None,
                "plan_path": plan,
                "site_href": f"/cases/{case_id}/pages?source={quote(r['source_id'])}",
            }
        )
        del out[-1]["by_phase"]
    return sorted(out, key=lambda r: -r["n_steps"])


def p3_loop(steps: list[dict]) -> list[dict]:
    out = []
    for t in live.loop_threads(steps):
        if t["phase"] != 3:
            continue
        objections = [o for it in t["iterations"] for s in it["steps"] for o in s["detail"]["objections"]]
        out.append(
            {
                "id": t["id"],
                "iterations": len(t["iterations"]),
                "stop_label": t["stop_label"],
                "usd": t["usd"],
                "objections": objections[-6:],
                "n_objections": len(objections),
            }
        )
    return out


def model(case, run: str | None = None) -> dict:
    a = Artifacts(case)
    cid = case.id
    run_id, run_ids = resolve_run(a, run)
    steps = a.steps(run_id)
    leads_doc = a.json("03-fanout/surface-map/leads.json")
    leads_doc = leads_doc if isinstance(leads_doc, dict) else None
    ledger = a.json("03-fanout/surface-map/discovery.json")
    ledger = ledger if isinstance(ledger, dict) else None
    rounds_raw = [r for r in _list((ledger or {}).get("rounds")) if isinstance(r, dict)]
    last = rounds_raw[-1] if rounds_raw else {}
    objectives, obj_rel = objectives_doc(a)
    by_source: dict[str, list] = {}
    for o in objectives:
        by_source.setdefault(o.get("source_id"), []).append(
            {
                "id": o.get("id"),
                "priority": o.get("priority"),
                "target_fields": _list(o.get("target_fields")),
                "contribution": o.get("expected_contribution"),
                "tier": o.get("authority_tier"),
                "href": file_href(cid, obj_rel) if obj_rel else None,
            }
        )

    # candidates: leads.json, else the per-source manifests
    raw = [c for c in _list((leads_doc or {}).get("candidates")) if isinstance(c, dict)]
    cand_src = "03-fanout/surface-map/leads.json"
    if not raw:
        for rel in a.glob("03-fanout/sources/*/candidate.json"):
            doc = a.json(rel)
            if isinstance(doc, dict):
                raw.append({**doc, "status": "confirmed", "discovered_by": doc.get("provider")})
        cand_src = "03-fanout/sources/*/candidate.json"
    manifests = {rel.split("/")[2] for rel in a.glob("03-fanout/sources/*/candidate.json")}
    site_graphs = {rel.split("/")[2] for rel in a.glob("03-fanout/surface-map/*/site-graph.json")}
    candidates = []
    for c in raw:
        sid = c.get("source_id")
        cp = source_checkpoint(a, cid, sid)
        decision, reason = decide(c, cp)
        c["decision"] = decision  # used by the funnel count below (a working copy of the loaded JSON)
        critic = [{"property": k, "reason": v} for k, v in _dict(c.get("critic")).items() if v]
        candidates.append(
            {
                "url": c.get("landing_url") or c.get("url"),
                "host": host(c.get("landing_url") or c.get("url")),
                "title": c.get("title"),
                "provider": c.get("discovered_by") or c.get("provider"),
                "providers": _list(c.get("providers")),
                "status": c.get("status"),
                "source_id": sid,
                "source_type": c.get("source_type"),
                "tier": c.get("authority_tier"),
                "authority": c.get("authority"),
                "decision": decision,
                "state": DECISION_STATE.get(decision, "none"),
                "reason": reason,
                "covers": _list(c.get("covers")),
                "critic": critic,
                "iteration": c.get("iteration"),
                "checkpoint": cp,
                "objectives": by_source.get(sid, []),
                "manifest_href": file_href(cid, f"03-fanout/sources/{sid}/candidate.json")
                if sid in manifests
                else None,
                "site_href": f"/cases/{cid}/pages?source={quote(sid)}" if sid else None,
                "has_site_graph": sid in site_graphs,
            }
        )
    order = {"trusted": 0, "review": 1, "rejected": 2, "not captured": 3}
    candidates.sort(key=lambda c: (order.get(c["decision"], 9), c["host"]))
    candidate_urls = {c.get("url") for c in raw}
    leads = [x for x in _list((leads_doc or {}).get("leads")) if isinstance(x, dict)]
    uncaptured = [
        {
            "url": x.get("url"),
            "host": host(x.get("url")),
            "title": x.get("title"),
            "providers": _list(x.get("providers")) or [x.get("discovered_by")],
            "properties": _list(x.get("property_ids")),
            "query": x.get("query"),
        }
        for x in leads
        if x.get("url") not in candidate_urls
    ]
    yield_by = _dict(last.get("provider_yield"))
    prov = funnels(leads, raw if leads_doc else [], objectives, yield_by) if (leads_doc or yield_by) else []
    for p in prov:
        if p["basis"] == "leads.json":
            p["basis"] = "leads.json" if cand_src.endswith("leads.json") else "leads.json · sources/*/candidate.json"
    totals = {k: sum(s["n"] or 0 for p in prov for s in p["stages"] if s["key"] == k) for k, _ in STAGES}

    rounds = []
    for i, r in enumerate(rounds_raw, 1):
        cov = [
            {
                "property": k,
                "required": _dict(v).get("required"),
                "hosts": _list(_dict(v).get("hosts")),
                "met": len(_list(_dict(v).get("hosts"))) >= (_dict(v).get("required") or 0),
            }
            for k, v in _dict(r.get("coverage")).items()
        ]
        rounds.append(
            {
                "n": i,
                "at": (gen_by(r) or {}).get("at"),
                "mode": r.get("mode"),
                "iterations": r.get("iterations"),
                "stop_reason": r.get("stop_reason"),
                "stop_label": live.STOP_LABELS.get(r.get("stop_reason"), r.get("stop_reason")),
                "usd": r.get("usd"),
                "gaps": _list(r.get("gaps")),
                "coverage": cov,
                "objections": [str(o) for o in _list(r.get("objections"))],
                "n_queries": len(_list(r.get("queries"))),
                "n_attempts": len(_list(r.get("attempts"))),
                "n_leads": r.get("lead_count"),
                "n_candidates": r.get("candidate_count"),
                "selected": _list(r.get("selected_source_ids")),
            }
        )

    plan_rows = plans(a, cid)
    plan_dirs = {p["dir"].split("/", 1)[1]: p["raw_path"] for p in plan_rows}
    missing = [
        rel
        for rel, doc in (
            ("03-fanout/surface-map/leads.json", leads_doc),
            ("03-fanout/surface-map/discovery.json", ledger),
        )
        if doc is None
    ]
    metrics = run_metrics(a, run_id)
    backends = {(gen_by(d) or {}).get("backend") for d in (leads_doc, ledger)}
    m = {
        "case_id": cid,
        "run_id": run_id,
        "run_ids": run_ids,
        "live": run is None,
        "pending": source_pending(a, cid),
        "funnel": {
            "providers": prov,
            "totals": totals,
            "stages": [{"key": k, "label": lbl} for k, lbl in STAGES],
            "round_n": len(rounds_raw),
            "n_uncaptured": len(uncaptured),
            "empty": None
            if prov
            else gap(
                None,
                "Leads per provider narrowed to captured pages, critic-accepted pages, authority-passed "
                "pages and sources. Missing: " + (" and ".join(missing) or "provider data") + ".",
                "03-fanout/surface-map/leads.json · discovery.json (written by phase 3)",
            ),
        },
        "rounds": rounds,
        "p3_loops": p3_loop(steps),
        "candidates": {
            "rows": candidates,
            "source": cand_src,
            "by_decision": {k: sum(1 for c in candidates if c["decision"] == k) for k in order},
            "uncaptured": uncaptured[:200],
            "empty": None
            if candidates
            else gap(
                None,
                "Every captured candidate with host, provider and the authority decision with its "
                "reason. Missing: 03-fanout/surface-map/leads.json and 03-fanout/sources/*/candidate.json.",
                "03-fanout/surface-map/leads.json · 03-fanout/sources/<id>/candidate.json",
            ),
        },
        "objectives": {"path": obj_rel, "n": len(objectives)},
        "site_graphs": {
            "n": len(site_graphs),
            "empty": None
            if site_graphs
            else gap(
                "R15",
                "A site graph per source: page types, sample instances, edges and property hints.",
                "03-fanout/surface-map/<source>/site-graph.json",
            ),
        },
        "trace_sources": trace_sources(steps, plan_dirs, cid) if not candidates else [],
        "plans": {
            "rows": plan_rows,
            "n_parsed": sum(1 for p in plan_rows if p["parsed"]),
            "empty": None
            if plan_rows
            else gap(
                None,
                "One plan per (source, objective): target properties, "
                "path, method, mode range, allowed domains and budget.",
                "04-local/<source>__<objective>/tdd.json (written by phase 4)",
            ),
        },
    }
    m["backend"] = "recorded" if "recorded" in backends else backend_of(metrics, steps)
    return m


def install(ctx: VizContext) -> None:
    app, render = ctx.app, ctx.render
    ctx.case_view("discovery", "discovery", "Discovery", 30)

    @app.get("/cases/{case_id}/discovery", response_class=HTMLResponse)
    def discovery(request: Request, case_id: str, run: str | None = None):
        case = ctx.get_case(case_id)
        m = model(case, run)
        return render(request, "viz/discovery.html", nav="discovery", case=case, m=m, backend=m["backend"])

    @app.get("/cases/{case_id}/api/viz/discovery")
    def discovery_api(case_id: str, run: str | None = None) -> dict:
        return model(ctx.get_case(case_id), run)
