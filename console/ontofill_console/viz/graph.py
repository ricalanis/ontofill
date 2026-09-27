"""Entity graph: a run's gold entities as nodes (coloured and shaped by ontology class), their links as edges
(labelled by the ontology's relation labels; a symmetric relation is drawn once), and shared-value clusters: when
three or more entities are linked through the same value that each of them holds (e.g. rule-derived "same value"
links), the clique collapses into one hub showing that value. Click a node for the entity with its evidence, an edge
for the link's evidence values.

Reads `gold/<case>/<run>/entities.jsonl` (links[], flags[]) and `ontology.json` (classes, relations with
label/domain/range/symmetric and an optional `match` when a rule derives them, rules). Rule-derived links arrive
as ordinary links (GAPS R6) and are treated the same way.

The layout is deterministic and computed here, in plain Python: a force layout with a fixed seed and a fixed number
of iterations for linked entities, class-grouped rows for entities without links, rings around the entity in focus.
Everything works without JavaScript through query parameters; `static/viz-graph.js` only adds hover highlight,
in-place selection and arrow-key navigation.
"""

from __future__ import annotations

import hashlib
import math
import random
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse

from ..domain import humanize
from .core import Artifacts, VizContext, gap
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

ORDER = 56
DEFAULT_LIMIT = 150
MAX_LIMIT = 500
LIMITS = (50, 150, 300, 500)
N_COLORS = 3          # validated categorical slots (all pairs, both themes); later classes are neutral + own shape
SHAPES = ("circle", "square", "diamond", "triangle", "hexagon", "pentagon")
DASHES = ("", "7 4", "2 3", "10 3 2 3", "1 4")
LABEL_ALL_BELOW = 60  # show every node label when the graph is this small
EDGE_LABELS_BELOW = 24


def _poly(n: int, r: float, rot: float = -math.pi / 2) -> str:
    pts = [(r * math.cos(rot + 2 * math.pi * i / n), r * math.sin(rot + 2 * math.pi * i / n)) for i in range(n)]
    return "M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z"


SHAPE_PATHS = {  # centred on 0,0; about 15 px across so each class reads by shape as well as colour
    "circle": "M-7,0A7,7 0 1,0 7,0A7,7 0 1,0 -7,0Z", "square": "M-6,-6H6V6H-6Z", "diamond": _poly(4, 8.2),
    "triangle": _poly(3, 8.5), "hexagon": _poly(6, 7.5, 0), "pentagon": _poly(5, 7.8),
    "dot": "M-4.5,0A4.5,4.5 0 1,0 4.5,0A4.5,4.5 0 1,0 -4.5,0Z", "hub": "M-10,-7H10V7H-10Z"}
SOURCE = "gold/<case>/<run>/entities.jsonl (links[], flags[]) · ontology.json (classes, relations, rules)"


def _hid(prefix: str, *parts: str) -> str:
    return prefix + hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:10]


def _norm(value) -> str:
    return " ".join(str(value).split()).casefold() if value not in (None, "") else ""


def _clip(text: str, n: int = 24) -> str:
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


# layout ----------------------------------------------------------------------------------------------------------
def force_layout(ids: list[str], edges: list[tuple[str, str]], seed: int = 17, k: float = 72.0) -> dict:
    """Fruchterman-Reingold with a fixed seed, fixed iterations and grid-bounded repulsion. Deterministic for the
    same input order; ids must already be in a stable order."""
    n = len(ids)
    if n == 0:
        return {}
    rng = random.Random(seed)
    index = {x: i for i, x in enumerate(ids)}
    xs, ys = [], []
    for i in range(n):  # golden-angle spiral start, small seeded jitter
        r, th = k * 0.9 * math.sqrt(i + 0.5), i * 2.399963
        xs.append(r * math.cos(th) + rng.uniform(-1, 1) * k * 0.1)
        ys.append(r * math.sin(th) + rng.uniform(-1, 1) * k * 0.1)
    pairs = [(index[a], index[b]) for a, b in edges if a in index and b in index and a != b]
    iters = 90 if n <= 120 else 60 if n <= 300 else 40
    kk, cell, t0 = k * k, 2 * k, k * 2.0
    for it in range(iters):
        dx, dy = [0.0] * n, [0.0] * n
        grid: dict[tuple[int, int], list[int]] = {}
        for i in range(n):
            grid.setdefault((int(xs[i] // cell), int(ys[i] // cell)), []).append(i)
        for (cx, cy), members in grid.items():
            near = [j for ox in (-1, 0, 1) for oy in (-1, 0, 1) for j in grid.get((cx + ox, cy + oy), ())]
            for i in members:
                xi, yi, fx, fy = xs[i], ys[i], 0.0, 0.0
                for j in near:
                    if j == i:
                        continue
                    ddx, ddy = xi - xs[j], yi - ys[j]
                    d2 = ddx * ddx + ddy * ddy
                    if d2 < 0.01:
                        ddx, ddy, d2 = (i - j) * 0.1, 0.1, 0.02
                    if d2 < cell * cell:
                        f = kk / d2
                        fx += ddx * f
                        fy += ddy * f
                dx[i] += fx
                dy[i] += fy
        for a, b in pairs:
            ddx, ddy = xs[a] - xs[b], ys[a] - ys[b]
            d = math.sqrt(ddx * ddx + ddy * ddy) or 0.01
            f = d / k
            dx[a] -= ddx * f
            dy[a] -= ddy * f
            dx[b] += ddx * f
            dy[b] += ddy * f
        t = t0 * (1 - it / iters) + k * 0.05
        for i in range(n):
            gx, gy = dx[i] - xs[i] * 0.04, dy[i] - ys[i] * 0.04  # weak pull to the centre keeps components close
            d = math.sqrt(gx * gx + gy * gy)
            if d > 0:
                s = min(d, t) / d
                xs[i] += gx * s
                ys[i] += gy * s
    return {x: (xs[i], ys[i]) for i, x in enumerate(ids)}


def radial_layout(focus: str, dist: dict[str, int], neighbours: dict[str, set], order_key) -> dict:
    """The entity in focus at the centre, depth-1 neighbours on the first ring, depth-2 on the second ring next to
    the depth-1 entity that reaches them."""
    ring1 = sorted([x for x, d in dist.items() if d == 1], key=order_key)
    ring2 = [x for x, d in dist.items() if d == 2]
    pos = {focus: (0.0, 0.0)}
    r1 = max(190.0, len(ring1) * 30 / (2 * math.pi))
    angle = {}
    for i, x in enumerate(ring1):
        a = 2 * math.pi * i / max(1, len(ring1)) - math.pi / 2
        angle[x] = a
        pos[x] = (r1 * math.cos(a), r1 * math.sin(a))

    def parent_angle(x):
        ps = [angle[p] for p in neighbours.get(x, ()) if p in angle]
        return min(ps) if ps else 0.0

    ring2.sort(key=lambda x: (parent_angle(x), order_key(x)))
    r2 = max(r1 + 170.0, len(ring2) * 24 / (2 * math.pi))
    for i, x in enumerate(ring2):
        a = 2 * math.pi * i / max(1, len(ring2)) - math.pi / 2
        pos[x] = (r2 * math.cos(a), r2 * math.sin(a))
    return pos


# model -----------------------------------------------------------------------------------------------------------
def _catalog(domain, entities) -> list[dict]:
    counts: dict[str, int] = {}
    for e in entities:
        counts[e.get("class") or "?"] = counts.get(e.get("class") or "?", 0) + 1
    known = [domain.primary_class] + [c for c in domain.classes if c != domain.primary_class]
    known += sorted(c for c in counts if c not in known)
    related = {domain.primary_class}
    for r in domain.relations.values():
        if r.get("domain") == domain.primary_class and r.get("range"):
            related.add(r["range"])
        if r.get("range") == domain.primary_class and r.get("domain"):
            related.add(r["domain"])
    out = []
    for i, c in enumerate(known):
        if c not in counts and c not in domain.classes:
            continue
        out.append({"id": c, "label": domain.class_label(c) if c in domain.classes else humanize(c),
                    "n": counts.get(c, 0), "primary": c == domain.primary_class,
                    "related": c in related and c != domain.primary_class, "in_ontology": c in domain.classes,
                    "color": i + 1 if i < N_COLORS else 0, "shape": SHAPES[i] if i < len(SHAPES) else "dot",
                    "path": SHAPE_PATHS[SHAPES[i] if i < len(SHAPES) else "dot"]})
    return out


def _relation_catalog(domain, used: dict[str, int]) -> list[dict]:
    out = []
    ids = list(domain.relations) + sorted(r for r in used if r not in domain.relations)
    for i, rid in enumerate(ids):
        r = domain.relations.get(rid) or {}
        match = r.get("match") if isinstance(r.get("match"), dict) else None
        derived = bool(match or r.get("derived_by") or r.get("rule_id"))
        rule_text = None
        if match:
            left, right = match.get("domain_property") or "?", match.get("range_property") or "?"
            rule_text = (f"derived by rule: same {domain.prop_label(left)}" if left == right else
                         f"derived by rule: {domain.prop_label(left)} equals {domain.prop_label(right)}")
        elif derived:
            rule_text = f"derived by rule {r.get('derived_by') or r.get('rule_id')}"
        out.append({"id": rid, "label": domain.relation_label(rid), "symmetric": bool(r.get("symmetric")),
                    "domain": r.get("domain"), "range": r.get("range"), "in_ontology": rid in domain.relations,
                    "derived": derived, "rule_text": rule_text, "n": used.get(rid, 0),
                    "dash": DASHES[i] if i < len(DASHES) else ""})
    return out


class _Graph:
    """All edges of a run (not only the displayed ones), so any edge can be selected by id."""

    def __init__(self, g, domain, rel_meta: dict):
        self.by_id = g.entities_by_id
        self.edges: dict[str, dict] = {}
        self.n_links, self.n_dangling = 0, 0
        self.used: dict[str, int] = {}
        for e in g.entities:
            for ln in e.get("links") or []:
                if not isinstance(ln, dict):
                    continue
                self.n_links += 1
                s, t = e["id"], str(ln.get("target") or "")
                rid = str(ln.get("property") or ln.get("type") or "")
                if t not in self.by_id or t == s:
                    self.n_dangling += 1
                    continue
                sym = bool((rel_meta.get(rid) or {}).get("symmetric"))
                a, b = (min(s, t), max(s, t)) if sym else (s, t)
                eid = _hid("e-", rid, a, b)
                edge = self.edges.get(eid)
                if edge is None:
                    edge = self.edges[eid] = {"id": eid, "source": a, "target": b, "relation": rid,
                                              "symmetric": sym, "via_value_ids": [], "n_links": 0}
                    self.used[rid] = self.used.get(rid, 0) + 1
                edge["n_links"] += 1
                via = ln.get("via_value_id")
                if via and via not in edge["via_value_ids"]:
                    edge["via_value_ids"].append(via)


def _url(case_id: str, params: dict, **over) -> str:
    p = {**params, **over}
    qs = urlencode([(k, v) for k, v in p.items() if v not in (None, "", 0) or (k == "depth" and p.get("focus"))])
    return f"/cases/{case_id}/graph" + (f"?{qs}" if qs else "")


def graph_model(case, run: str | None = None, cls: str = "", rel: str = "", signal: str = "", text: str = "",
                focus: str = "", depth: int = 1, limit: int = DEFAULT_LIMIT, sel: str = "", expand: str = "") -> dict:
    a = Artifacts(case)
    g, run_id, note = resolve_run(a, run)
    text, cls, rel, signal = (text or "").strip()[:200], (cls or "").strip()[:100], (rel or "").strip()[:100], \
        (signal or "").strip()[:100]
    focus, sel = (focus or "").strip()[:300], (sel or "").strip()[:300]
    depth = 2 if depth == 2 else 1
    try:
        limit = max(10, min(MAX_LIMIT, int(limit or DEFAULT_LIMIT)))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    expanded = [x for x in (expand or "").split(",") if x.startswith("c-")][:20]
    params = {"run": run_id if run else "", "cls": cls, "rel": rel, "signal": signal, "q": text, "focus": focus,
              "depth": depth if focus else 0, "limit": limit if limit != DEFAULT_LIMIT else 0,
              "expand": ",".join(expanded)}
    base = {"case_id": case.id, "run_id": run_id, "gold_run_ids": a.gold_run_ids(), "brief": brief_text(a),
            "filters": {"run": run_id if run else "", "cls": cls, "rel": rel, "signal": signal, "q": text, "focus": focus, "depth": depth,
                        "limit": limit, "expand": expanded},
            "limits": list(LIMITS), "source": SOURCE, "backend": None, "classes": [], "relations": [], "signals": [],
            "nodes": [], "edges": [], "clusters": [], "groups": [], "selected": None, "n_entities": 0,
            "n_matched": 0, "n_context": 0, "n_nodes": 0, "n_edges": 0, "n_links": 0, "n_dangling": 0,
            "capped": False, "width": 0, "height": 0, "focus_note": None, "empty_links": None,
            "clear_href": _url(case.id, {"run": run_id if run else ""})}
    if g is None:
        what = ("Every entity of the run as a node coloured by its ontology class, the links between them labelled "
                "by relation, and clusters of entities that share a value; each node opens the entity's evidence.")
        src = ("gold/<case>/<run>/entities.jsonl (this run has a live feed but no gold export yet)"
               if note == "live-only" else "gold/<case>/<run>/entities.jsonl (written when a run publishes gold)")
        return {**base, "empty": gap(None, what, src)}

    domain = domain_for(g)
    classes = _catalog(domain, g.entities)
    cmeta = {c["id"]: c for c in classes}
    corder = {c["id"]: i for i, c in enumerate(classes)}
    rel_meta = dict(domain.relations)
    graph = _Graph(g, domain, rel_meta)
    relations = _relation_catalog(domain, graph.used)
    rmeta = {r["id"]: r for r in relations}
    sig_counts: dict[str, int] = {}
    sig_labels: dict[str, str] = {}
    for e in g.entities:
        for fl in e.get("flags") or []:
            if isinstance(fl, dict) and fl.get("rule_id"):
                rid = str(fl["rule_id"])
                sig_counts[rid] = sig_counts.get(rid, 0) + 1
                sig_labels.setdefault(rid, fl.get("label") or "")
    signals = [{"id": rid, "label": domain.rule(rid, sig_labels.get(rid, ""))["label"], "n": sig_counts.get(rid, 0)}
               for rid in list(domain.rules) + sorted(r for r in sig_counts if r not in domain.rules)]

    titles = {e["id"]: entity_title(domain, e) for e in g.entities}
    idents = {e["id"]: entity_identifier(domain, e) for e in g.entities}

    def order_key(x):
        return (corder.get(g.entities_by_id[x].get("class") or "?", 99), titles[x].casefold(), x) \
            if x in g.entities_by_id else (99, x, x)

    considered = [ed for ed in graph.edges.values() if not rel or ed["relation"] == rel]
    nbrs: dict[str, set] = {}
    for ed in considered:
        nbrs.setdefault(ed["source"], set()).add(ed["target"])
        nbrs.setdefault(ed["target"], set()).add(ed["source"])
    degree = {x: len(v) for x, v in nbrs.items()}

    dist: dict[str, int] = {}
    focus_note = None
    if focus and focus not in g.entities_by_id:
        focus_note = f"No entity {focus} in run {run_id}; showing the whole graph."
        focus = ""
        params["focus"], params["depth"] = "", 0
    context: set[str] = set()
    if focus:
        dist = {focus: 0}
        frontier = [focus]
        for d in range(1, depth + 1):
            nxt = []
            for x in frontier:
                for y in sorted(nbrs.get(x, ())):
                    if y not in dist:
                        dist[y] = d
                        nxt.append(y)
            frontier = nxt
        matched = list(dist)
    else:
        needle = text.casefold()
        flagged = {e["id"] for e in g.entities for fl in e.get("flags") or []
                   if isinstance(fl, dict) and str(fl.get("rule_id")) == signal} if signal else set()
        matched = []
        for e in g.entities:
            x = e["id"]
            if cls and (e.get("class") or "?") != cls:
                continue
            if signal and x not in flagged:
                continue
            if rel and x not in nbrs:
                continue
            if needle and not any(needle in str(v).casefold() for v in (titles[x], idents[x] or "", x)):
                continue
            matched.append(x)
        if text or signal:
            ms = set(matched)
            context = {y for x in matched for y in nbrs.get(x, ()) if y not in ms}
    n_matched = len(matched)
    pool = list(dict.fromkeys(matched + sorted(context)))
    pool.sort(key=lambda x: (dist.get(x, 0), x in context, -degree.get(x, 0), order_key(x)))
    shown = pool[:limit]
    shown_set = set(shown)
    context &= shown_set

    # displayed edges, then shared-value clusters ------------------------------------------------------------------
    disp = [ed for ed in considered if ed["source"] in shown_set and ed["target"] in shown_set]
    held: dict[str, set] = {}

    def holds(x: str) -> set:
        if x not in held:
            held[x] = {_norm(f.get("value")) for f in (g.entities_by_id[x].get("properties") or {}).values()
                       if isinstance(f, dict) and f.get("value") not in (None, "")}
        return held[x]

    groups_by_key: dict[tuple, dict] = {}
    for ed in disp:
        for vid in ed["via_value_ids"]:
            ref = g.values.get(vid)
            val = _norm(ref.data.get("value")) if ref else ""
            if val and val in holds(ed["source"]) and val in holds(ed["target"]):
                grp = groups_by_key.setdefault((ed["relation"], val), {"edges": {}, "members": set(), "vias": [],
                                                                       "ref": ref})
                grp["edges"][ed["id"]] = ed
                grp["members"].update((ed["source"], ed["target"]))
                if vid not in grp["vias"]:
                    grp["vias"].append(vid)
                break
    clusters, hidden_edges, spokes = [], set(), []
    for (rid, _val), grp in sorted(groups_by_key.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        members = sorted(grp["members"], key=order_key)
        if len(members) < 3 or len(grp["edges"]) <= len(members):
            continue  # a hub only when it replaces more lines than it draws
        free = [e for e in grp["edges"] if e not in hidden_edges]
        if len(free) <= len(members):
            continue
        cid = _hid("c-", rid, _val)
        ref = grp["ref"]
        is_open = cid in expanded
        clusters.append({"id": cid, "relation": rid, "relation_label": rmeta.get(rid, {}).get("label") or humanize(rid),
                         "value": str(ref.data.get("value")), "prop": ref.prop,
                         "prop_label": domain.prop_label(ref.prop), "members": members, "n_members": len(members),
                         "n_edges": len(free), "via_value_ids": grp["vias"][:50], "expanded": is_open,
                         "toggle_href": _url(case.id, params, expand=",".join(
                             [x for x in expanded if x != cid] if is_open else expanded + [cid]), sel=cid),
                         "sel_href": _url(case.id, params, sel=cid)})
        if not is_open:
            hidden_edges.update(free)
            spokes += [{"id": f"{cid}-{i}", "source": m, "target": cid, "relation": rid, "kind": "spoke"}
                       for i, m in enumerate(members)]
    ccount: dict[str, list[str]] = {}
    for c in clusters:
        for m in c["members"]:
            ccount.setdefault(m, []).append(c["id"])

    # layout -------------------------------------------------------------------------------------------------------
    hubs = [c for c in clusters if not c["expanded"]]
    draw_edges = [ed for ed in disp if ed["id"] not in hidden_edges]
    lay_edges = [(ed["source"], ed["target"]) for ed in draw_edges] + [(s["source"], s["target"]) for s in spokes]
    linked = {x for pair in lay_edges for x in pair}
    pad, lab = 40.0, 150.0
    pos: dict[str, tuple[float, float]] = {}
    grid_rows: list[dict] = []
    if focus:
        pos = {x: p for x, p in radial_layout(focus, dist, nbrs, order_key).items() if x in shown_set}
        for c in hubs:
            pts = [pos[m] for m in c["members"] if m in pos]
            ang = math.atan2(sum(p[1] for p in pts), sum(p[0] for p in pts) or 1e-6)
            rr = sum(math.hypot(*p) for p in pts) / max(1, len(pts)) * 0.55
            pos[c["id"]] = (rr * math.cos(ang), rr * math.sin(ang))
        isolated = []
    else:
        ids = sorted([x for x in shown if x in linked], key=order_key) + [c["id"] for c in hubs]
        pos = force_layout(ids, lay_edges)
        if len(pos) > 1:  # a small graph is spread to a readable width; labels keep their size
            xs_, ys_ = [p[0] for p in pos.values()], [p[1] for p in pos.values()]
            span = max(max(xs_) - min(xs_), max(ys_) - min(ys_), 1.0)
            f = min(2.5, 720.0 / span) if span < 720.0 else 1.0
            pos = {x: (p[0] * f, p[1] * f) for x, p in pos.items()}
        isolated = [x for x in shown if x not in linked]
    if pos:
        minx, maxx = min(p[0] for p in pos.values()), max(p[0] for p in pos.values())
        miny, maxy = min(p[1] for p in pos.values()), max(p[1] for p in pos.values())
        pos = {x: (p[0] - minx + pad, p[1] - miny + pad + 10) for x, p in pos.items()}
        width, height = maxx - minx + 2 * pad + lab, maxy - miny + 2 * pad + 20
    else:
        width, height = 0.0, 0.0
    if isolated:  # entities without links: rows grouped by class under the linked part
        width = max(width, 820.0)
        colw, rowh = 180.0, 26.0
        cols = max(1, int((width - 2 * pad) // colw))
        y = height + (24.0 if height else 16.0)
        by_cls: dict[str, list[str]] = {}
        for x in sorted(isolated, key=order_key):
            by_cls.setdefault(g.entities_by_id[x].get("class") or "?", []).append(x)
        for c, xs in by_cls.items():
            grid_rows.append({"y": round(y + 12, 1), "x": pad - 12,
                              "label": f"{cmeta.get(c, {}).get('label', humanize(c))} · {len(xs)} without links"})
            y += 26.0
            for i, x in enumerate(xs):
                pos[x] = (pad + (i % cols) * colw, y + (i // cols) * rowh + 8)
            y += math.ceil(len(xs) / cols) * rowh + 12
        height = y + 8
    width, height = round(max(width, 320.0)), round(max(height, 120.0))

    # view-model ---------------------------------------------------------------------------------------------------
    rq = f"?run={q(run_id)}" if run_id else ""
    top = set(sorted(shown, key=lambda x: -degree.get(x, 0))[:25]) if len(shown) > LABEL_ALL_BELOW else set(shown)
    isolated_set = set(isolated)
    nodes = []
    for x in shown:
        e = g.entities_by_id[x]
        c = cmeta.get(e.get("class") or "?", {})
        flags = [str(fl.get("rule_id")) for fl in e.get("flags") or [] if isinstance(fl, dict) and fl.get("rule_id")]
        px, py = pos.get(x, (pad, pad))
        label_on = x in top or x in isolated_set or x == focus or (focus and dist.get(x) == 1 and len(dist) <= 40)
        nodes.append({"id": x, "kind": "entity", "class": e.get("class") or "?", "class_label": c.get("label"),
                      "color": c.get("color", 0), "shape": c.get("shape", "dot"),
                      "path": c.get("path") or SHAPE_PATHS["dot"], "title": titles[x],
                      "short": _clip(titles[x]), "identifier": idents[x], "x": round(px, 1), "y": round(py, 1),
                      "degree": degree.get(x, 0), "flags": flags, "context": x in context, "focus": x == focus,
                      "depth": dist.get(x), "label_on": bool(label_on), "clusters": ccount.get(x, []),
                      "href": f"/cases/{case.id}/entities/{q(x)}{rq}", "sel_href": _url(case.id, params, sel=x),
                      "focus_href": _url(case.id, {**params, "cls": "", "signal": "", "q": ""}, focus=x,
                                         depth=depth, sel=x),
                      "aria": f"{c.get('label') or 'Entity'}: {titles[x]}"
                              + (f", {len(flags)} signal{'s' if len(flags) != 1 else ''}" if flags else "")
                              + f", {degree.get(x, 0)} linked"})
    for c in hubs:
        px, py = pos.get(c["id"], (pad, pad))
        nodes.append({"id": c["id"], "kind": "cluster", "class": None, "class_label": None, "color": 0,
                      "shape": "hub", "path": SHAPE_PATHS["hub"], "title": c["value"], "short": _clip(c["value"], 28), "identifier": None,
                      "x": round(px, 1), "y": round(py, 1), "degree": c["n_members"], "flags": [],
                      "context": False, "focus": False, "depth": None, "label_on": True, "clusters": [],
                      "href": c["sel_href"], "sel_href": c["sel_href"], "focus_href": None,
                      "aria": f"Shared {c['prop_label']}: {c['value']}, {c['n_members']} entities, "
                              f"{c['relation_label']}"})
    npos = {n["id"]: (n["x"], n["y"]) for n in nodes}
    show_edge_labels = not focus and len(draw_edges) <= EDGE_LABELS_BELOW
    edges = []
    for ed in draw_edges:
        r = rmeta.get(ed["relation"], {})
        (x1, y1), (x2, y2) = npos[ed["source"]], npos[ed["target"]]
        if not ed["symmetric"]:  # stop short of the target so the arrowhead shows
            d = math.hypot(x2 - x1, y2 - y1) or 1.0
            x2, y2 = round(x2 - (x2 - x1) / d * 11, 1), round(y2 - (y2 - y1) / d * 11, 1)
        edges.append({"id": ed["id"], "kind": "link", "source": ed["source"], "target": ed["target"],
                      "relation": ed["relation"], "label": r.get("label") or humanize(ed["relation"]),
                      "symmetric": ed["symmetric"], "derived": bool(r.get("derived")), "dash": r.get("dash", ""),
                      "via_value_ids": ed["via_value_ids"], "n_links": ed["n_links"],
                      "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                      "label_on": show_edge_labels or (bool(focus) and focus in (ed["source"], ed["target"])
                                                       and len(dist) <= 30),
                      "sel_href": _url(case.id, params, sel=ed["id"]),
                      "aria": f"{r.get('label') or humanize(ed['relation'])}: {titles[ed['source']]} "
                              f"{'—' if ed['symmetric'] else '→'} {titles[ed['target']]}"})
    for s in spokes:
        r = rmeta.get(s["relation"], {})
        (x1, y1), (x2, y2) = npos[s["source"]], npos[s["target"]]
        edges.append({"id": s["id"], "kind": "spoke", "source": s["source"], "target": s["target"],
                      "relation": s["relation"], "label": r.get("label") or humanize(s["relation"]),
                      "symmetric": True, "derived": bool(r.get("derived")), "dash": r.get("dash", ""),
                      "via_value_ids": [], "n_links": 0, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "label_on": False,
                      "sel_href": _url(case.id, params, sel=s["target"]),
                      "aria": f"{titles[s['source']]} in the shared-value cluster"})

    # list fallback (390 px and screen readers): entities grouped by class with their links and clusters ------------
    inc: dict[str, list[dict]] = {}
    for ed in disp:
        for me, other in ((ed["source"], ed["target"]), (ed["target"], ed["source"])):
            direction = "—" if ed["symmetric"] else ("→" if me == ed["source"] else "←")
            inc.setdefault(me, []).append({"label": rmeta.get(ed["relation"], {}).get("label") or ed["relation"],
                                           "direction": direction, "title": titles[other],
                                           "href": f"/cases/{case.id}/entities/{q(other)}{rq}",
                                           "sel_href": _url(case.id, params, sel=ed["id"])})
    cl_by_id = {c["id"]: c for c in clusters}
    groups = []
    for c in classes:
        rows = [{"entity_id": x, "title": titles[x],
                 "identifier": idents[x] if str(idents[x] or "") != titles[x] else None, "context": x in context,
                 "href": f"/cases/{case.id}/entities/{q(x)}{rq}", "sel_href": _url(case.id, params, sel=x),
                 "links": inc.get(x, [])[:8], "n_links": len(inc.get(x, [])),
                 "clusters": [{"id": cid, "text": f"{cl_by_id[cid]['prop_label']}: {cl_by_id[cid]['value']}",
                               "href": cl_by_id[cid]["sel_href"]} for cid in ccount.get(x, [])]}
                for x in sorted((x for x in shown if (g.entities_by_id[x].get("class") or "?") == c["id"]), key=order_key)]
        if rows:
            groups.append({"class": c["id"], "label": c["label"], "color": c["color"], "shape": c["shape"], "path": c["path"],
                           "rows": rows})

    selected = _selected(case, a, g, domain, sel, graph, cl_by_id, rmeta, titles, idents, params, rq, depth) \
        if sel else None
    return {**base, "backend": g.inference_backend, "empty": None, "classes": classes, "relations": relations,
            "signals": signals, "nodes": nodes, "edges": edges, "clusters": clusters, "groups": groups,
            "selected": selected, "n_entities": len(g.entities), "n_matched": n_matched, "n_context": len(context),
            "n_nodes": len(shown), "n_pool": len(pool), "n_edges": len(draw_edges), "n_links": graph.n_links,
            "n_dangling": graph.n_dangling, "capped": len(pool) > len(shown), "width": width, "height": height,
            "grid_rows": grid_rows, "focus_note": focus_note,
            "focus_title": titles.get(focus) if focus else None,
            "clear_focus_href": _url(case.id, params, focus="", depth=0, sel="") if focus else None,
            "empty_links": None if graph.n_links else gap(
                "R6", "Links between entities: ontology relations, including links a rule derives from a shared "
                      "value, each with the value it was read through. Until then the entities are grouped by class.",
                "entities.jsonl links[] · ontology.json relations[]")}


def _value_view(case, a, g, domain, vid: str, titles: dict, rq: str) -> dict:
    ref = g.values.get(vid)
    if ref is None:
        return {"value_id": vid, "found": False, "lineage_href": None, "evidence": []}
    f = ref.data
    return {"value_id": vid, "found": True, "prop": ref.prop, "prop_label": domain.prop_label(ref.prop),
            "value": f.get("value"), "status": f.get("status"), "state": value_state(f),
            "entity_id": ref.entity_id, "entity_title": titles.get(ref.entity_id, ref.entity_id),
            "entity_href": f"/cases/{case.id}/entities/{q(ref.entity_id)}{rq}#p-{ref.prop}",
            "lineage_href": f"/cases/{case.id}/lineage/{q(vid)}{rq}",
            "evidence": [evidence_view(case.id, a, ev) for ev in (f.get("evidence") or [])[:3]]}


def _selected(case, a, g, domain, sel, graph, clusters, rmeta, titles, idents, params, rq, depth) -> dict:
    if sel in graph.edges:
        ed = graph.edges[sel]
        r = rmeta.get(ed["relation"], {})
        return {"kind": "edge", "id": sel, "relation": ed["relation"],
                "label": r.get("label") or humanize(ed["relation"]), "symmetric": ed["symmetric"],
                "rule_text": r.get("rule_text"), "n_links": ed["n_links"],
                "ends": [{"entity_id": x, "title": titles[x], "href": f"/cases/{case.id}/entities/{q(x)}{rq}",
                          "sel_href": _url(case.id, params, sel=x)} for x in (ed["source"], ed["target"])],
                "vias": [_value_view(case, a, g, domain, v, titles, rq) for v in ed["via_value_ids"][:6]],
                "empty_via": None if ed["via_value_ids"] else gap(None, "The value this link was read through.",
                                                                  "entities.jsonl links[].via_value_id")}
    if sel in clusters:
        c = clusters[sel]
        return {"kind": "cluster", "id": sel, "relation_label": c["relation_label"], "value": c["value"],
                "prop_label": c["prop_label"], "expanded": c["expanded"], "toggle_href": c["toggle_href"],
                "members": [{"entity_id": x, "title": titles[x], "href": f"/cases/{case.id}/entities/{q(x)}{rq}",
                             "sel_href": _url(case.id, params, sel=x)} for x in c["members"]],
                "vias": [_value_view(case, a, g, domain, v, titles, rq) for v in c["via_value_ids"][:4]],
                "n_vias": len(c["via_value_ids"])}
    e = g.entities_by_id.get(sel)
    if e is None:
        return {"kind": "missing", "id": sel}
    cls = e.get("class") or "?"
    props, d = props_for(domain, g, cls)
    values = e.get("properties") or {}
    rows = []
    for p in sorted(props, key=lambda p: (not p.dod, p.order))[:6]:
        f = values.get(p.id) if isinstance(values.get(p.id), dict) else None
        ev = [evidence_view(case.id, a, x, with_meta=False) for x in (f or {}).get("evidence") or []][:1]
        rows.append({"id": p.id, "label": p.label, "dod": p.dod, "value": (f or {}).get("value"),
                     "state": value_state(f), "evidence": ev,
                     "lineage_href": f"/cases/{case.id}/lineage/{q(f['value_id'])}{rq}" if f and f.get("value_id")
                     else None})
    links = []
    for ed in graph.edges.values():
        if sel not in (ed["source"], ed["target"]):
            continue
        other = ed["target"] if ed["source"] == sel else ed["source"]
        links.append({"label": rmeta.get(ed["relation"], {}).get("label") or humanize(ed["relation"]),
                      "direction": "—" if ed["symmetric"] else ("→" if ed["source"] == sel else "←"),
                      "title": titles[other], "href": f"/cases/{case.id}/entities/{q(other)}{rq}",
                      "sel_href": _url(case.id, params, sel=ed["id"])})
    links.sort(key=lambda x: (x["label"], x["title"]))
    flags = [{"rule_id": fl.get("rule_id"), "label": fl.get("label") or d.rule(str(fl.get("rule_id") or ""))["label"],
              "explanation": fl.get("explanation")} for fl in e.get("flags") or [] if isinstance(fl, dict)]
    return {"kind": "entity", "id": sel, "title": titles[sel],
            "identifier": idents[sel] if str(idents[sel] or "") != titles[sel] else None,
            "class_label": d.class_label(cls) if cls in d.classes else humanize(cls),
            "href": f"/cases/{case.id}/entities/{q(sel)}{rq}",
            "focus_href": _url(case.id, {**params, "cls": "", "signal": "", "q": ""}, focus=sel, depth=depth, sel=sel),
            "props": rows, "links": links[:20], "n_links": len(links), "flags": flags}


def install(ctx: VizContext) -> None:
    app, render = ctx.app, ctx.render
    ctx.case_view("graph", "graph", "Entity graph", 56)

    def _model(case_id, run, cls, rel, signal, q_, focus, depth, limit, sel, expand):
        return graph_model(ctx.get_case(case_id), run, cls, rel, signal, q_, focus, depth, limit, sel, expand)

    @app.get("/cases/{case_id}/graph", response_class=HTMLResponse)
    def graph_page(request: Request, case_id: str, run: str | None = None, cls: str = "", rel: str = "",
                   signal: str = "", q: str = "", focus: str = "", depth: int = 1, limit: int = DEFAULT_LIMIT,
                   sel: str = "", expand: str = ""):
        case = ctx.get_case(case_id)
        m = graph_model(case, run, cls, rel, signal, q, focus, depth, limit, sel, expand)
        return render(request, "viz/graph.html", nav="graph", case=case, m=m, backend=m["backend"])

    @app.get("/cases/{case_id}/api/viz/graph")
    def graph_api(case_id: str, run: str | None = None, cls: str = "", rel: str = "", signal: str = "", q: str = "",
                  focus: str = "", depth: int = 1, limit: int = DEFAULT_LIMIT, sel: str = "", expand: str = "") -> dict:
        return _model(case_id, run, cls, rel, signal, q, focus, depth, limit, sel, expand)
