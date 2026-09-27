"""Q2 · How was this question turned into a contract?

A vertical path through the case package: the question (brief.md) → the PRD drafts, each followed by the human
decision that produced the next one, with a line diff of the key sections → the definition-of-done criteria with
their basis → the authority policy → factors → taxonomies → the ontology graph → the DoD compiled to queries →
critic objections from the phase 1 and 2 loops. Everything is read from the engine's own artifacts:
`brief.md`, `01-scope/prd.json`, `01-scope/revisions/<n>/`, `decisions.jsonl`, `02-ontology/factors/factors.json`,
`02-ontology/ontology.json`, `02-ontology/taxonomies/*`, `02-ontology/dod-queries.json`, the run's metrics and trace.
"""

from __future__ import annotations

import difflib
import math
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import approvals as ap
from .. import dod as dodmod
from .. import live
from ..gold import backend_of
from .core import Artifacts, VizContext, gap

ORDER = 20
CP_LABELS = {"prd": "PRD", "factors": "Factors", "ontology": "Ontology", "source": "Source", "action": "Action"}
PRD_SECTIONS = (
    ("personas", "persona"),
    ("jobs_to_be_done", "job"),
    ("requirements", "requirement"),
    ("constraints", "constraint"),
    ("definition_of_done", "done when"),
)
BASIS_STATE = {"brief": "done", "human": "run", "proposed": "need"}
BASIS_WORDS = {"brief": "from the brief", "human": "from a person", "proposed": "proposed by the engine"}
TIER_ORDER = ("primary", "secondary", "review")
TIER_WORDS = {
    "primary": "trusted as evidence",
    "secondary": "cross-check only, needs review",
    "review": "needs human review",
}


# shared helpers (the discovery view imports these too) ------------------------------------------------------------
def file_href(case_id: str, rel: str) -> str:
    return f"/cases/{case_id}/files/{quote(rel)}"


def gen_by(doc) -> dict | None:
    g = (doc or {}).get("generated_by") if isinstance(doc, dict) else None
    if not isinstance(g, dict):
        return None
    return {"model": g.get("model"), "backend": g.get("backend"), "at": g.get("at")}


def resolve_run(a: Artifacts, run: str | None) -> tuple[str | None, list[str]]:
    """The run whose trace and metrics the view reads: `?run=<id>` when it exists (404 otherwise), else the latest."""
    ids = list(dict.fromkeys([*a.run_ids(), *a.gold_run_ids()]))
    if run:
        if run not in ids:
            raise HTTPException(404, "unknown run")
        return run, ids
    return a.latest_run_id() or (ids[-1] if ids else None), ids


def run_metrics(a: Artifacts, run_id: str | None) -> dict:
    if not run_id:
        return {}
    g = a.gold(run_id)
    if g is not None and getattr(g, "run_id", None) == run_id and isinstance(g.metrics, dict):
        return g.metrics
    m = a.status(run_id).get("metrics")
    return m if isinstance(m, dict) else {}


def pending_strips(a: Artifacts, case_id: str, checkpoints: tuple[str, ...]) -> list[dict]:
    out = []
    for item in a.approvals():
        if item.approved is not None or (item.checkpoint or "") not in checkpoints:
            continue
        cp = item.checkpoint or "checkpoint"
        out.append(
            {
                "state": "need",
                "title": f"{CP_LABELS.get(cp, cp)}: waiting for your review",
                "detail": " · ".join(str(x) for x in (item.phase_dir, item.meta.get("reason")) if x),
                "meta": str(item.meta.get("requested_at") or "")[:16].replace("T", " "),
                "href": f"/cases/{case_id}/approvals/{item.phase_dir}",
            }
        )
    return out


def _dict(x) -> dict:
    return x if isinstance(x, dict) else {}


def _list(x) -> list:
    return x if isinstance(x, list) else []


# PRD revisions and diffs --------------------------------------------------------------------------------------------
def prd_text(doc: dict) -> list[str]:
    """A normalized, line-per-item rendering of the PRD's key sections, stable enough to diff."""
    lines: list[str] = []
    for key, word in PRD_SECTIONS:
        for item in _list(doc.get(key)):
            if isinstance(item, str):
                lines.append(f"{word}: {item}")
            elif isinstance(item, dict) and key == "definition_of_done":
                ratio = f" (min ratio {item['min_ratio']})" if item.get("min_ratio") is not None else ""
                basis = f" [basis: {item['basis']}]" if item.get("basis") else ""
                lines.append(
                    f"{word}: {item.get('id', '?')} · {item.get('metric', '?')} {item.get('operator', '')} "
                    f"{item.get('target', '')}{ratio}{basis}"
                )
            elif isinstance(item, dict):
                owner = item.get("persona_id") or item.get("job_id")
                lines.append(
                    f"{word}: {item.get('id', '?')}{f' ({owner})' if owner else ''} · {item.get('description', '')}"
                )
    return lines


def line_diff(old: list[str], new: list[str]) -> dict:
    rows, n_add, n_del = [], 0, 0
    for ln in difflib.unified_diff(old, new, lineterm="", n=1):
        if ln.startswith(("---", "+++")):
            continue
        if ln.startswith("@@"):
            rows.append({"op": "hunk", "text": "···"})
        elif ln.startswith("+"):
            rows.append({"op": "add", "text": "+ " + ln[1:]})
            n_add += 1
        elif ln.startswith("-"):
            rows.append({"op": "del", "text": "- " + ln[1:]})
            n_del += 1
        else:
            rows.append({"op": "ctx", "text": "  " + ln[1:]})
    return {"lines": rows, "n_add": n_add, "n_del": n_del}


def prd_timeline(a: Artifacts, case_id: str) -> dict:
    current = a.json("01-scope/prd.json")
    current = current if isinstance(current, dict) else None
    recorded = {r.get("n"): r for r in _list((current or {}).get("revisions")) if isinstance(r, dict)}
    archives = {}
    for rel in a.glob("01-scope/revisions/*/prd.json"):
        n = rel.split("/")[2]
        if n.isdigit():
            archives[int(n)] = rel
    for rel in a.glob("01-scope/revisions/*/APPROVED"):
        n = rel.split("/")[2]
        if n.isdigit():
            archives.setdefault(int(n), None)
    logged = [d for d in reversed(a.decisions()) if d.get("phase_dir") == "01-scope" or d.get("checkpoint") == "prd"]
    n_prior = max([*archives, *[n for n in recorded if isinstance(n, int)], 0])
    drafts = []
    for n in range(1, n_prior + 1):
        rel = archives.get(n)
        doc = a.json(rel) if rel else None
        marker = a.json(f"01-scope/revisions/{n}/APPROVED")
        dec = _dict(marker) or _dict(recorded.get(n))
        drafts.append(
            {
                "n": n,
                "path": rel,
                "doc": doc if isinstance(doc, dict) else None,
                "decision": {
                    "decision": dec.get("decision") or "deny",
                    "reason": dec.get("reason"),
                    "approver": dec.get("approver"),
                    "date": dec.get("date"),
                    "logged_at": (logged[n - 1].get("ts") if len(logged) >= n else None),
                },
            }
        )
    if current is not None:
        approved = a.json("01-scope/APPROVED")
        pending = a.text("01-scope/APPROVAL_PENDING.md") is not None
        if isinstance(approved, dict):
            dec = {
                "decision": approved.get("decision") or "approve",
                "reason": approved.get("reason"),
                "approver": approved.get("approver"),
                "date": approved.get("date"),
                "logged_at": (logged[n_prior].get("ts") if len(logged) > n_prior else None),
            }
        else:
            dec = {
                "decision": "pending" if pending else None,
                "reason": None,
                "approver": None,
                "date": None,
                "logged_at": None,
            }
        drafts.append({"n": n_prior + 1, "path": "01-scope/prd.json", "doc": current, "decision": dec})
    rows, diffs = [], []
    for i, d in enumerate(drafts):
        doc = d["doc"]
        dec = d["decision"]
        state = {"deny": "block", "approve": "done", "pending": "need"}.get(dec.get("decision") or "", "none")
        rows.append(
            {
                "n": d["n"],
                "path": d["path"],
                "href": file_href(case_id, d["path"]) if d["path"] else None,
                "version": (doc or {}).get("version"),
                "generated_by": gen_by(doc),
                "n_criteria": len(_list((doc or {}).get("definition_of_done"))),
                "current": d["path"] == "01-scope/prd.json",
                "decision": dec,
                "state": state,
            }
        )
        if i + 1 < len(drafts):
            nxt = drafts[i + 1]
            if doc is not None and nxt["doc"] is not None:
                diff = line_diff(prd_text(doc), prd_text(nxt["doc"]))
            else:
                diff = None
            diffs.append(
                {
                    "from_n": d["n"],
                    "to_n": nxt["n"],
                    "reason": dec.get("reason"),
                    "missing": None
                    if diff is not None
                    else f"01-scope/revisions/{d['n']}/prd.json (the archived draft) is not in the case package",
                    **(diff or {"lines": [], "n_add": 0, "n_del": 0}),
                }
            )
    return {
        "path": "01-scope/prd.json",
        "present": current is not None,
        "drafts": rows,
        "diffs": diffs,
        "empty": None
        if current is not None
        else gap(
            None,
            "The PRD drafts the engine wrote from the brief, and the human decisions between them.",
            "01-scope/prd.json (written by phase 1)",
        ),
    }


def dod_rows(prd: dict | None) -> dict:
    rows = []
    for c in _list((prd or {}).get("definition_of_done")):
        if not isinstance(c, dict):
            continue
        basis = c.get("basis") if c.get("basis") in BASIS_STATE else None
        rows.append(
            {
                "id": c.get("id"),
                "metric": c.get("metric"),
                "operator": c.get("operator"),
                "target": c.get("target"),
                "min_ratio": c.get("min_ratio"),
                "basis": basis or "unstated",
                "basis_state": BASIS_STATE.get(basis or "", "none"),
                "basis_words": BASIS_WORDS.get(basis or "", "no basis recorded"),
                "quote": c.get("basis_quote"),
                "rationale": c.get("rationale"),
                "feasibility": c.get("feasibility"),
            }
        )
    tally = {k: sum(1 for r in rows if r["basis"] == k) for k in ("brief", "human", "proposed", "unstated")}
    return {
        "rows": rows,
        "by_basis": tally,
        "empty": None
        if rows
        else gap(
            None,
            "Each done-when criterion with its basis: a quote from the brief, "
            "a person's words, or the engine's proposal with rationale.",
            "01-scope/prd.json · definition_of_done[]",
        ),
    }


def authority(prd: dict | None) -> dict:
    pol = _dict((prd or {}).get("authority_policy"))
    tiers: dict[str, list] = {}
    for p in _list(pol.get("trusted_publishers")):
        if isinstance(p, dict):
            tier = p.get("tier") if p.get("tier") in TIER_ORDER else "primary"
            tiers.setdefault(tier, []).append(
                {
                    "kind": p.get("kind"),
                    "domains": [str(d) for d in _list(p.get("domains"))],
                    "rationale": p.get("rationale"),
                    "jurisdiction": p.get("jurisdiction"),
                }
            )
    rows = [
        {
            "tier": t,
            "words": TIER_WORDS[t],
            "state": {"primary": "done", "secondary": "pause", "review": "need"}[t],
            "publishers": tiers[t],
            "n_domains": sum(len(p["domains"]) for p in tiers[t]),
        }
        for t in TIER_ORDER
        if t in tiers
    ]
    return {
        "jurisdiction": pol.get("jurisdiction"),
        "unknown_source_action": pol.get("unknown_source_action"),
        "tiers": rows,
        "empty": None
        if pol
        else gap(
            None,
            "Which publishers count as evidence, by tier, and what happens to an unknown source.",
            "01-scope/prd.json · authority_policy",
        ),
    }


# ontology side ------------------------------------------------------------------------------------------------------
def factors(a: Artifacts, case_id: str, ontology: dict | None) -> dict:
    rel = "02-ontology/factors/factors.json"
    doc = a.json(rel)
    src = rel
    if not isinstance(doc, dict) and isinstance(ontology, dict) and ontology.get("factors"):
        doc, src = ontology, "02-ontology/ontology.json"
    marker = a.json("02-ontology/factors/APPROVED")
    marker = marker if isinstance(marker, dict) else None
    decisions = _dict((marker or {}).get("decisions"))
    pending = a.text("02-ontology/factors/APPROVAL_PENDING.md") is not None
    rows = []
    for f in _list((doc or {}).get("factors")):
        if not isinstance(f, dict):
            continue
        d = decisions.get(f.get("id"))
        rows.append(
            {
                "id": f.get("id"),
                "label": f.get("label"),
                "kind": f.get("kind"),
                "description": f.get("description"),
                "evidence": [
                    {"url": e.get("url"), "description": e.get("description")}
                    for e in _list(f.get("evidence"))
                    if isinstance(e, dict)
                ],
                "decision": d,
                "state": {"accept": "done", "reject": "block"}.get(
                    d or "", "need" if pending and not marker else "none"
                ),
            }
        )
    if marker:
        state = "denied" if marker.get("decision") == "deny" else "approved"
    else:
        state = "pending" if pending else "none"
    return {
        "path": src,
        "href": file_href(case_id, src),
        "rows": rows,
        "state": state,
        "approver": (marker or {}).get("approver"),
        "date": (marker or {}).get("date"),
        "reason": (marker or {}).get("reason"),
        "n_accepted": sum(1 for r in rows if r["decision"] == "accept"),
        "n_rejected": sum(1 for r in rows if r["decision"] == "reject"),
        "generated_by": gen_by(doc),
        "empty": None
        if rows
        else gap(
            None,
            "The factors of variation the engine proposed from the PRD, each accepted or rejected by a person.",
            rel,
        ),
    }


def _critic_state(label: str | None) -> str:
    low = (label or "").lower()
    if low.startswith("good"):
        return "done"
    if low.startswith("bad"):
        return "block"
    return "none"


def _tax_nodes(children, depth: int = 1, out: list | None = None, limit: int = 40) -> list[dict]:
    """Taxonomy nodes depth-first (at most `limit`), each with its level and the critic's label."""
    out = [] if out is None else out
    for n in _list(children):
        if isinstance(n, dict) and len(out) < limit:
            out.append(
                {
                    "id": n.get("id"),
                    "label": n.get("label"),
                    "level": n.get("level", depth),
                    "critic": n.get("critic_label"),
                    "state": _critic_state(n.get("critic_label")),
                }
            )
            _tax_nodes(n.get("children"), depth + 1, out, limit)
    return out


def taxonomies(a: Artifacts, case_id: str, ontology: dict | None) -> dict:
    found: list[tuple[dict, str]] = [
        (t, "02-ontology/ontology.json") for t in _list((ontology or {}).get("taxonomies")) if isinstance(t, dict)
    ]
    for rel in a.glob("02-ontology/taxonomies/*.json"):
        doc = a.json(rel)
        items = doc.get("taxonomies") if isinstance(doc, dict) and "taxonomies" in doc else [doc]
        for t in _list(items):
            if isinstance(t, dict) and t.get("children") is not None:
                found.append((t, rel))
    rows, seen = [], set()
    for t, rel in found:
        key = (t.get("factor_id"), t.get("root_label"))
        if key in seen:
            continue
        seen.add(key)
        stats = ap.taxonomy_stats(t)
        nodes = _tax_nodes(t.get("children"))
        rows.append(
            {
                "factor_id": t.get("factor_id"),
                "root": t.get("root_label") or t.get("root_id"),
                "soundness": t.get("soundness"),
                "coverage": t.get("coverage"),
                "levels": [{"level": k, "n": v} for k, v in stats["levels"].items()],
                "critic": [{"label": k, "n": v, "state": _critic_state(k)} for k, v in sorted(stats["critic"].items())],
                "n_nodes": stats["nodes"],
                "nodes": nodes,
                "path": rel,
                "href": file_href(case_id, rel),
            }
        )
    return {
        "rows": rows,
        "empty": None
        if rows
        else gap(
            "R10",
            "Each factor expanded into a taxonomy: nodes per level and the critic's label for each node.",
            "02-ontology/ontology.json · taxonomies[] or 02-ontology/taxonomies/*",
        ),
    }


GRAPH_W, GRAPH_H = 720, 400


def _node_w(label: str) -> int:
    return max(96, min(200, 8 * len(label) + 28))


def _clip(fx: float, fy: float, cx: float, cy: float, half: tuple[float, float], pad: float = 0) -> tuple[float, float]:
    """The point where the segment from (fx, fy) toward the centre (cx, cy) of a box meets the box's edge."""
    dx, dy = fx - cx, fy - cy
    hw, hh = half[0] + pad, half[1] + pad
    if dx == 0 and dy == 0:
        return cx, cy
    t = min(hw / abs(dx) if dx else math.inf, hh / abs(dy) if dy else math.inf)
    return cx + dx * t, cy + dy * t


def graph_layout(classes: list[dict], relations: list[dict]) -> dict:
    """Deterministic layout: the primary class at the top of an ellipse, the rest clockwise in ontology order.
    Edges are straight between node centres (curved apart when two relations share a pair), self-relations loop
    above their node. Coordinates are in a fixed viewBox; the page scrolls the SVG sideways on phones."""
    n = len(classes)
    height = 180 if n <= 2 else (GRAPH_H if n <= 6 else GRAPH_H + 40 * (n - 6))
    cx, cy = GRAPH_W / 2, height / 2 + 20
    rx, ry = GRAPH_W / 2 - 120, height / 2 - 60
    pos: dict[str, tuple[float, float]] = {}
    half: dict[str, tuple[float, float]] = {}
    nodes = []
    for i, c in enumerate(classes):
        if n == 1:
            x, y = cx, cy
        elif n == 2:  # side by side reads better than stacked
            x, y = (GRAPH_W * (0.25 if i == 0 else 0.75)), cy
        else:
            ang = -math.pi / 2 + 2 * math.pi * i / n
            x, y = cx + rx * math.cos(ang), cy + ry * math.sin(ang)
        w = _node_w(c["label"])
        pos[c["id"]] = (x, y)
        half[c["id"]] = (w / 2, 22)
        nodes.append(
            {
                "id": c["id"],
                "label": c["label"],
                "x": round(x - w / 2, 1),
                "y": round(y - 22, 1),
                "w": w,
                "h": 44,
                "tx": round(x, 1),
                "ty": round(y - 3, 1),
                "sy": round(y + 13, 1),
                "sub": f"{c['n_props']} props · {c['n_dod']} DoD",
                "primary": c["primary"],
            }
        )
    pair_seen: dict[tuple, int] = {}
    edges = []
    for r in relations:
        d, g = r.get("domain"), r.get("range")
        if d not in pos or g not in pos:
            continue
        x1, y1 = pos[d]
        x2, y2 = pos[g]
        if d == g:
            k = pair_seen.get((d, d), 0)
            pair_seen[(d, d)] = k + 1
            lift = 60 + 18 * k
            path = f"M{x1 - 24:.1f},{y1 - 22:.1f} C{x1 - 44:.1f},{y1 - lift:.1f} {x1 + 44:.1f},{y1 - lift:.1f} {x1 + 24:.1f},{y1 - 22:.1f}"
            lx, ly = x1, y1 - lift + 12
        else:
            key = tuple(sorted((d, g)))
            k = pair_seen.get(key, 0)
            pair_seen[key] = k + 1
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy) or 1
            off = 0 if k == 0 else (44 * ((k + 1) // 2) * (1 if k % 2 else -1))
            qx, qy = mx - dy / length * off, my + dx / length * off
            sx, sy = _clip(qx, qy, x1, y1, half[d])  # leave the source box
            ex, ey = _clip(qx, qy, x2, y2, half[g], pad=4)  # stop at the target box edge so the arrow shows
            path = f"M{sx:.1f},{sy:.1f} Q{qx:.1f},{qy:.1f} {ex:.1f},{ey:.1f}"
            lx, ly = (mx + qx) / 2, (my + qy) / 2 - 4
        edges.append(
            {
                "id": r.get("id"),
                "label": r.get("label") or r.get("id"),
                "path": path,
                "lx": round(lx, 1),
                "ly": round(ly, 1),
                "symmetric": bool(r.get("symmetric")),
            }
        )
    return {"width": GRAPH_W, "height": round(height + 20), "nodes": nodes, "edges": edges}


def ontology_view(case_id: str, ontology: dict | None) -> dict:
    rel = "02-ontology/ontology.json"
    onto = ontology if isinstance(ontology, dict) else {}
    primary = onto.get("primary_class")
    props = [p for p in _list(onto.get("properties")) if isinstance(p, dict)]
    classes = []
    for c in _list(onto.get("classes")):
        if not isinstance(c, dict) or not c.get("id"):
            continue
        mine = sorted(
            (p for p in props if p.get("domain") == c["id"]), key=lambda p: (p.get("order") or 99, p.get("id") or "")
        )
        classes.append(
            {
                "id": c["id"],
                "label": c.get("label") or c["id"],
                "description": c.get("description"),
                "primary": c["id"] == primary,
                "aligned_to": c.get("aligned_to"),
                "title_property": c.get("title_property"),
                "identifier_property": c.get("identifier_property"),
                "n_props": len(mine),
                "n_dod": sum(1 for p in mine if p.get("dod")),
                "props": [
                    {
                        "id": p.get("id"),
                        "label": p.get("label") or p.get("id"),
                        "datatype": p.get("datatype"),
                        "dod": bool(p.get("dod")),
                        "description": p.get("description"),
                    }
                    for p in mine
                ],
            }
        )
    classes.sort(key=lambda c: not c["primary"])
    relations = [
        {
            "id": r.get("id"),
            "label": r.get("label") or r.get("id"),
            "domain": r.get("domain"),
            "range": r.get("range"),
            "symmetric": bool(r.get("symmetric")),
        }
        for r in _list(onto.get("relations"))
        if isinstance(r, dict)
    ]
    return {
        "path": rel,
        "href": file_href(case_id, rel),
        "version": onto.get("version"),
        "primary_class": primary,
        "classes": classes,
        "relations": relations,
        "n_props": sum(c["n_props"] for c in classes),
        "n_dod": sum(c["n_dod"] for c in classes),
        "source_classes": [
            {"id": s.get("id"), "label": s.get("label")}
            for s in _list(onto.get("source_classes"))
            if isinstance(s, dict)
        ],
        "graph": graph_layout(classes, relations) if classes else None,
        "generated_by": gen_by(onto),
        "empty": None
        if classes
        else gap(
            None,
            "The classes, properties and relations the engine derived, with the definition-of-done properties marked.",
            rel,
        ),
    }


def queries(a: Artifacts, case_id: str, metrics: dict) -> dict:
    rel = "02-ontology/dod-queries.json"
    doc = a.json(rel)
    measured = {d.get("criterion_id"): d for d in _list(metrics.get("dod")) if isinstance(d, dict)}
    rows = []
    for q in _list((doc or {}).get("queries")):
        if not isinstance(q, dict):
            continue
        try:
            text = dodmod.query_text(q)
        except (KeyError, TypeError, ValueError):
            text = str(q.get("aggregate"))
        m = measured.pop(q.get("criterion_id"), {})
        rows.append(
            {
                "criterion_id": q.get("criterion_id"),
                "text": text,
                "aggregate": q.get("aggregate"),
                "operator": q.get("operator"),
                "target": q.get("target"),
                "actual": m.get("actual"),
                "met": m.get("met"),
                "state": {True: "done", False: "block"}.get(m.get("met"), "none"),
            }
        )
    source = rel
    for cid, m in measured.items():  # compiled queries the run measured but the case package no longer lists
        rows.append(
            {
                "criterion_id": cid,
                "text": m.get("query"),
                "aggregate": None,
                "operator": None,
                "target": m.get("target"),
                "actual": m.get("actual"),
                "met": m.get("met"),
                "state": {True: "done", False: "block"}.get(m.get("met"), "none"),
            }
        )
        source = f"{rel} · metrics.json dod[]"
    return {
        "path": rel,
        "href": file_href(case_id, rel),
        "source": source,
        "rows": rows,
        "n_met": sum(1 for r in rows if r["met"] is True),
        "measured": bool(metrics.get("dod")),
        "generated_by": gen_by(doc),
        "empty": None
        if rows
        else gap(
            None,
            "Each done-when criterion compiled to a declarative query over gold, with what the run measured.",
            "02-ontology/dod-queries.json · metrics.json dod[]",
        ),
    }


def critic(steps: list[dict]) -> dict:
    threads = []
    for t in live.loop_threads(steps):
        if t["phase"] not in (1, 2):
            continue
        items = []
        flat = [s for it in t["iterations"] for s in it["steps"]]
        for i, s in enumerate(flat):
            d = s["detail"]
            for o in d["objections"]:
                later = next((x for x in flat[i + 1 :] if x["detail"]["role"] in ("revise", "decide")), None)
                res = None
                if later is not None:
                    ld = later["detail"]
                    who = "a person" if ld["human"] else (ld["model"] or "the engine")
                    res = f"{ld['role']} by {who}" + (f": {ld['reason']}" if ld.get("reason") else "")
                items.append(
                    {
                        "iteration": d["iteration"],
                        "role": d["role"],
                        "text": o,
                        "model": d["model"],
                        "resolution": res,
                        "step_id": s.get("step_id"),
                    }
                )
        threads.append(
            {
                "id": t["id"],
                "label": t["label"],
                "phase": t["phase"],
                "iterations": len(t["iterations"]),
                "stop_label": t["stop_label"],
                "usd": t["usd"],
                "objections": items,
            }
        )
    return {
        "threads": threads,
        "n_objections": sum(len(t["objections"]) for t in threads),
        "empty": None
        if threads
        else gap(
            None,
            "What the critic objected to while phases 1 and 2 drafted, and how each objection was resolved.",
            "runs/<case>/<run>/trace.live.jsonl · loop steps (phase 1, 2)",
        ),
    }


def model(case, run: str | None = None) -> dict:
    a = Artifacts(case)
    run_id, run_ids = resolve_run(a, run)
    prd = a.json("01-scope/prd.json")
    prd = prd if isinstance(prd, dict) else None
    ontology = a.json("02-ontology/ontology.json")
    ontology = ontology if isinstance(ontology, dict) else None
    metrics = run_metrics(a, run_id)
    steps = a.steps(run_id)
    brief = a.text("brief.md", limit=4000)
    m = {
        "case_id": case.id,
        "run_id": run_id,
        "run_ids": run_ids,
        "live": run is None,
        "brief": {
            "path": "brief.md",
            "present": brief is not None,
            "lines": [
                ln.strip() for ln in (brief or "").splitlines() if ln.strip() and not ln.lstrip().startswith("#")
            ],
        },
        "pending": pending_strips(a, case.id, ("prd", "factors", "ontology")),
        "prd": prd_timeline(a, case.id),
        "dod": dod_rows(prd),
        "authority": authority(prd),
        "factors": factors(a, case.id, ontology),
        "taxonomies": taxonomies(a, case.id, ontology),
        "ontology": ontology_view(case.id, ontology),
        "queries": queries(a, case.id, metrics),
        "critic": critic(steps),
    }
    backends = {
        (g or {}).get("backend")
        for g in (gen_by(prd), gen_by(ontology), m["queries"]["generated_by"], m["factors"]["generated_by"])
    }
    m["backend"] = "recorded" if "recorded" in backends else backend_of(metrics, steps)
    return m


def install(ctx: VizContext) -> None:
    app, render = ctx.app, ctx.render
    ctx.case_view("definition", "definition", "Definition", 20)

    @app.get("/cases/{case_id}/definition", response_class=HTMLResponse)
    def definition(request: Request, case_id: str, run: str | None = None):
        case = ctx.get_case(case_id)
        m = model(case, run)
        return render(request, "viz/definition.html", nav="definition", case=case, m=m, backend=m["backend"])

    @app.get("/cases/{case_id}/api/viz/definition")
    def definition_api(case_id: str, run: str | None = None) -> dict:
        return model(ctx.get_case(case_id), run)
