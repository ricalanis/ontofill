"""Q4 · Which pages did it visit, and what did it do on each? The site graph a bounded read-only spider built for
each source it explored (GAPS R15): page types as nodes (ontology label, instance count, quoted property hints), typed
links between them (followed solid, not followed dashed with the reason), a coverage overlay by definition-of-done
property, the fetched pages of a type with their capture, status, depth and trace step, and the crawl's limits,
robots decisions and stop reason.

Reads `03-fanout/surface-map/<source>/site-graph.json` (CONTRACT v1.0.3: `{schema_version, bronze_key, generated_by,
graph}`), tolerating partial files and the bare `graph` object; bronze sidecars (`bronze/sha256/<hex>.meta.json`) to
decide whether a page's capture is an image; the case ontology for class and property labels.
"""

from __future__ import annotations

import json
import math
from urllib.parse import quote, urlencode, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from .core import SAFE_ERRORS, Artifacts, VizContext, gap
from .operation import safe_url
from .pages import _clip

ORDER = 45
KEY, SLUG, LABEL = "sites", "sites", "Site graphs"
PATTERN = "03-fanout/surface-map/*/site-graph.json"
SOURCES = "03-fanout/surface-map/<source>/site-graph.json · bronze/sha256/<hex>.meta.json · 02-ontology/ontology.json"

KIND_WORD = {"listing": "listing", "detail": "detail page", "search": "search page", "download": "download",
             "other": "page"}
LINK_KIND = {"navigate": "link", "paginate": "next page", "search_results": "search → results",
             "download": "download"}
STOP_LABEL = {"queue_exhausted": "every reachable link was visited", "depth_limit": "depth limit reached",
              "page_cap": "page cap reached", "robots": "robots.txt stopped the crawl",
              "login_or_captcha": "login or captcha: stopped and flagged", "network_error": "network error"}
STOP_STATE = {"queue_exhausted": "done", "depth_limit": "pause", "page_cap": "pause", "robots": "block",
              "login_or_captcha": "need", "network_error": "block"}
ROBOTS_LABEL = {"allow": "allowed", "disallow": "disallowed", "conservative_stop": "stopped (no usable robots.txt)"}
COV = {  # per page type, for one chosen property: glyph, short label, meaning
    "covered": ("●", "covered", "quoted on a page of the property's own class"),
    "hinted": ("◐", "hinted", "quoted on another page type"),
    "none": ("○", "not seen", "no quote for it on this page type"),
}

# layout (px, SVG user units) --------------------------------------------------------------------------------------
COL_W, PAD, TOP, R_MAX, R_UNIT = 210, 44, 52, 30.0, 12.0
TEXT_H, ROW_GAP, GHOST_W, GHOST_H = 46, 22, 150, 44


# parsing (tolerant) -----------------------------------------------------------------------------------------------
def _dicts(value) -> list[dict]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _strs(value) -> list[str]:
    return [str(x) for x in value if isinstance(x, (str, int))] if isinstance(value, list) else []


def graph_of(doc) -> tuple[dict | None, dict]:
    """(graph, envelope) from a v1.0.3 document or a bare graph object."""
    if not isinstance(doc, dict):
        return None, {}
    if isinstance(doc.get("graph"), dict):
        return doc["graph"], doc
    if any(k in doc for k in ("types", "instances", "edges", "page_types", "pages")):
        return doc, {}
    return None, doc


def _hint_rows(t: dict) -> list[dict]:
    raw = t.get("property_hints") or t.get("sample_property_hints") or t.get("hints") or []
    if isinstance(raw, dict):
        raw = [{"property_id": k, "evidence_quote": v if isinstance(v, str) else None} for k, v in raw.items()]
    out, seen = [], set()
    for h in raw if isinstance(raw, list) else []:
        if isinstance(h, dict):
            pid = h.get("property_id") or h.get("property") or h.get("id")
            row = {"property_id": str(pid) if pid else None, "quote": h.get("evidence_quote") or h.get("quote"),
                   "sample_instance_id": h.get("sample_instance_id")}
        else:
            row = {"property_id": str(h) if h else None, "quote": None, "sample_instance_id": None}
        if row["property_id"] and (row["property_id"], row["quote"]) not in seen:
            seen.add((row["property_id"], row["quote"]))
            out.append(row)
    return out


def _label(t: dict, domain) -> tuple[str, str | None, str | None]:
    """(display label, kind, class id): the ontology class label for typed pages, else the page kind."""
    raw = t.get("label")
    kind = cls = text = None
    if isinstance(raw, dict):
        kind, cls = raw.get("kind"), raw.get("class_id")
    elif isinstance(raw, str) and raw:
        if raw in KIND_WORD:
            kind = raw
        else:
            text = raw
    kind = kind or t.get("kind") or t.get("page_kind")
    cls = cls or t.get("class_id")
    if cls:
        text = f"{domain.class_label(cls)} {KIND_WORD.get(kind or 'other', kind or 'page')}"
    elif not text:
        text = KIND_WORD.get(kind or "other", str(kind or "page")).capitalize()
    return text, (str(kind) if kind else None), (str(cls) if cls else None)


def _prop_domain(domain, pid: str) -> str | None:
    for cls, props in domain.properties.items():
        if any(p.id == pid for p in props):
            return cls
    return None


def _r(n: int, n_max: int) -> float:
    """Circle radius: area proportional to the instance count; one scale per graph (shown in the legend)."""
    return round(_unit(n_max) * math.sqrt(max(n, 0)), 1)


def _unit(n_max: int) -> float:
    return min(R_UNIT, R_MAX / math.sqrt(max(n_max, 1)))


def _scale(n_max: int) -> list[dict]:
    steps = sorted({1, max(1, n_max // 4), max(1, n_max // 2), max(n_max, 1)})
    return [{"n": n, "r": _r(n, n_max), "d": round(2 * _r(n, n_max) + 2, 1)} for n in steps]


def parse(doc, path: str, domain) -> dict:
    """The site graph as page types, typed type→type links, fetched pages and crawl facts; never raises on shape."""
    g, env = graph_of(doc)
    problems: list[str] = []
    if g is None:
        return {"path": path, "readable": False,
                "problems": ["not a site graph document (no graph, page types or pages)"]}
    if env and env.get("schema_version") not in (None, "1.0.3"):
        problems.append(f"schema_version {env.get('schema_version')} (this view reads 1.0.3)")
    types_raw = _dicts(g.get("types") or g.get("page_types"))
    insts_raw = _dicts(g.get("instances") or g.get("pages"))
    edges_raw = _dicts(g.get("edges"))
    for k in ("types", "instances", "edges", "crawl", "coverage"):
        if k not in g and not (k == "types" and "page_types" in g) and not (k == "instances" and "pages" in g):
            problems.append(f"graph.{k} missing")

    # page types
    types, by_id = [], {}
    for n, t in enumerate(types_raw):
        tid = str(t.get("type_id") or t.get("id") or t.get("url_template") or f"type-{n + 1}")
        if tid in by_id:
            continue
        label, kind, cls = _label(t, domain)
        node = {"type_id": tid, "idx": len(types), "label": label, "kind": kind, "class_id": cls,
                "class_label": domain.class_label(cls) if cls else None,
                "template": t.get("url_template") or t.get("template"), "skeleton": t.get("dom_skeleton_hash"),
                "sample_ids": _strs(t.get("sample_instance_ids")), "hints": _hint_rows(t),
                "n": 0, "depth": None, "insts": []}
        for h in node["hints"]:
            h["label"] = domain.prop_label(h["property_id"])
        node["hint_ids"] = sorted({h["property_id"] for h in node["hints"]})
        types.append(node)
        by_id[tid] = node

    # fetched pages
    insts, inst_by_id, inst_by_url, untyped = [], {}, {}, 0
    for n, i in enumerate(insts_raw):
        iid = str(i.get("instance_id") or i.get("id") or f"page-{n + 1}")
        url = i.get("url") or i.get("final_url") or i.get("requested_url")
        url = url if isinstance(url, str) else None
        tid = str(i.get("type_id") or i.get("type") or i.get("page_type_id") or "")
        depth = i.get("depth") if isinstance(i.get("depth"), int) else None
        status = i.get("http_status", i.get("status"))
        chain = _strs(i.get("redirect_chain"))
        parts = urlsplit(url) if url else None
        row = {"instance_id": iid, "url": url, "safe_url": safe_url(url) if url else None,
               "path": ((parts.path or "/") + (f"?{parts.query}" if parts.query else "")) if parts else None,
               "depth": depth, "http_status": status if isinstance(status, int) else None,
               "content_type": i.get("content_type"), "link_kind": i.get("link_kind"),
               "link_kind_label": LINK_KIND.get(i.get("link_kind") or "", i.get("link_kind")),
               "parent_instance_id": i.get("parent_instance_id"), "type_id": tid or None,
               "bronze_key": i.get("bronze_key") if isinstance(i.get("bronze_key"), str) else None,
               "screenshot_key": i.get("screenshot_key") if isinstance(i.get("screenshot_key"), str) else None,
               "trace_step_id": i.get("trace_step_id") or i.get("step_id"), "job_id": i.get("job_id"),
               "redirects": chain[1:] if len(chain) > 1 else [], "redirect_chain": chain}
        insts.append(row)
        inst_by_id[iid] = row
        for u in [url, *chain]:
            if u:
                inst_by_url.setdefault(u, row)
        node = by_id.get(tid)
        if node is None:
            untyped += 1
            continue
        node["n"] += 1
        node["insts"].append(row)
        if depth is not None:
            node["depth"] = depth if node["depth"] is None else min(node["depth"], depth)
    for node in types:
        if not node["n"] and node["sample_ids"]:
            problems.append(f"page type {node['type_id']} lists samples but no fetched page carries its id")
    if untyped:
        problems.append(f"{untyped} fetched page{'s' if untyped != 1 else ''} without a known page type")

    # links, aggregated by (from type, to type, kind, followed); unfetched targets go to one 'not fetched' sink
    def inst_of(e: dict, side: str) -> dict | None:
        ref = e.get(f"{side}_instance_id")
        if isinstance(ref, str) and ref in inst_by_id:
            return inst_by_id[ref]
        u = e.get(f"{side}_url")
        return inst_by_url.get(u) if isinstance(u, str) else None

    agg: dict[tuple, dict] = {}
    unfetched: dict[str, dict] = {}
    n_followed = n_not = 0
    for e in edges_raw:
        a = inst_of(e, "from")
        src = by_id.get(a["type_id"]) if a else None
        if src is None:
            continue
        b = inst_of(e, "to")
        dst = by_id.get(b["type_id"]) if b else None
        followed = e.get("followed") is True
        reason = e.get("reason") if isinstance(e.get("reason"), str) else None
        n_followed += followed
        n_not += not followed
        kind = e.get("kind") if isinstance(e.get("kind"), str) else "navigate"
        to = dst["type_id"] if dst else "__unfetched__"
        row = agg.setdefault((src["type_id"], to, kind, followed), {
            "from": src["type_id"], "to": to, "kind": kind, "kind_label": LINK_KIND.get(kind, kind),
            "followed": followed, "n": 0, "reasons": {}, "risk": set(), "method": set()})
        row["n"] += 1
        if reason:
            row["reasons"][reason] = row["reasons"].get(reason, 0) + 1
        row["risk"].add(str(e.get("risk_tier") or "?"))
        row["method"].add(str(e.get("method") or "GET"))
        if dst is None:
            u = str(e.get("to_url") or "")
            r = reason or ("not fetched" if not followed else "fetched, page not recorded")
            grp = unfetched.setdefault(r, {"reason": r, "n": 0, "urls": []})
            grp["n"] += 1
            if u and len(grp["urls"]) < 5 and u not in grp["urls"]:
                grp["urls"].append(u)
    links = list(agg.values())
    for row in links:
        row["risk"] = sorted(row["risk"])
        row["method"] = sorted(row["method"])
        row["reason_list"] = [{"reason": k, "n": v} for k, v in sorted(row["reasons"].items(), key=lambda kv: -kv[1])]
        del row["reasons"]

    # depth columns: crawl depth from pages; else breadth-first from the entry URL's type; unknown last
    seed = None
    su = g.get("source_url")
    if isinstance(su, str) and su in inst_by_url and inst_by_url[su]["type_id"] in by_id:
        seed = inst_by_url[su]["type_id"]
    if seed is None:
        zero = [n for n in types if n["depth"] == 0]
        seed = zero[0]["type_id"] if zero else (types[0]["type_id"] if types else None)
    if types and all(n["depth"] is None for n in types) and seed:
        by_id[seed]["depth"], frontier = 0, [seed]
        while frontier:
            nxt = []
            for f in frontier:
                for e in links:
                    if e["from"] == f and e["to"] in by_id and by_id[e["to"]]["depth"] is None:
                        by_id[e["to"]]["depth"] = by_id[f]["depth"] + 1
                        nxt.append(e["to"])
            frontier = nxt

    crawl = g.get("crawl") if isinstance(g.get("crawl"), dict) else {}
    robots = []
    for rb in _dicts(crawl.get("robots")):
        d = rb.get("decision")
        robots.append({"origin": rb.get("origin"), "url": rb.get("url"), "http_status": rb.get("http_status"),
                       "decision": d, "decision_label": ROBOTS_LABEL.get(d or "", d or "—"),
                       "crawl_delay_seconds": rb.get("crawl_delay_seconds"), "bronze_key": rb.get("bronze_key")
                       if isinstance(rb.get("bronze_key"), str) else None})
    stop = crawl.get("stop_reason")
    cov = g.get("coverage") if isinstance(g.get("coverage"), dict) else {}
    coverage = {k: _strs(cov.get(f"{k}_property_ids") or cov.get(k)) for k in ("target", "hinted", "uncovered")}
    gen = env.get("generated_by") if isinstance(env.get("generated_by"), dict) else {}
    host = urlsplit(su).hostname if isinstance(su, str) else None
    return {
        "path": path, "readable": bool(types), "problems": problems,
        "schema_version": env.get("schema_version"), "bronze_key": env.get("bronze_key"),
        "generated_by": {"backend": gen.get("backend"), "model": gen.get("model"), "at": gen.get("at")},
        "source_id": g.get("source_id"), "source_url": su if isinstance(su, str) else None,
        "source_href": safe_url(su) if isinstance(su, str) else None, "host": host,
        "source_fingerprint": g.get("source_fingerprint"), "ontology_version": g.get("ontology_version"),
        "run_id": g.get("run_id") if isinstance(g.get("run_id"), str) else None, "job_ids": _strs(g.get("job_ids")),
        "crawl": {"same_registrable_domain": crawl.get("same_registrable_domain"),
                  "max_depth": crawl.get("max_depth"), "page_cap": crawl.get("page_cap"),
                  "delay_seconds": crawl.get("delay_seconds", crawl.get("delay")),
                  "attempted_pages": crawl.get("attempted_pages"), "fetched_pages": crawl.get("fetched_pages"),
                  "stop_reason": stop, "stop_label": STOP_LABEL.get(stop or "", stop),
                  "stop_state": STOP_STATE.get(stop or "", "none"), "robots": robots},
        "coverage": coverage, "types": types, "instances": insts, "links": links, "seed": seed,
        "unfetched": sorted(unfetched.values(), key=lambda u: -u["n"]),
        "n_types": len(types), "n_instances": len(insts), "n_edges": len(edges_raw),
        "n_followed": n_followed, "n_not_followed": n_not,
    }


def read_all(a: Artifacts, domain) -> dict[str, dict]:
    out = {}
    for rel in a.glob(PATTERN):
        src = rel.split("/")[2]
        try:
            out[src] = parse(a.json(rel), rel, domain)
        except SAFE_ERRORS as exc:  # a shape this reader did not foresee: say so, never 500
            out[src] = {"path": rel, "readable": False, "problems": [f"unreadable: {type(exc).__name__}"]}
        out[src]["source_dir"] = src
    return out


# layout -----------------------------------------------------------------------------------------------------------
def layout(sg: dict) -> dict:
    """Deterministic, layered by crawl depth: the entry type left, depth 1, 2 to its right, 'not fetched' last."""
    types = sg["types"]
    n_max = max((t["n"] for t in types), default=1) or 1
    maxd = max((t["depth"] for t in types if t["depth"] is not None), default=0)
    cols: dict[int, list] = {}
    for t in types:
        cols.setdefault(t["depth"] if t["depth"] is not None else maxd + 1, []).append(t)
    for col in cols.values():
        col.sort(key=lambda t: (t["type_id"] != sg["seed"], -t["n"], t["label"], t["type_id"]))
    heads, x = [], PAD
    height = TOP
    for depth in sorted(cols):
        heads.append({"x": x + COL_W / 2 - 20, "text": ("entry · depth 0" if depth == 0 else f"depth {depth}")
                      if depth <= maxd else "depth unknown"})
        y = TOP
        for t in cols[depth]:
            t["r"] = max(_r(t["n"], n_max), 2.0)
            t["cx"], t["cy"] = x + COL_W / 2 - 20, y + R_MAX
            t["tx"], t["ty"] = t["cx"], t["cy"] + R_MAX + 14
            y += 2 * R_MAX + TEXT_H + ROW_GAP
        height = max(height, y)
        x += COL_W
    ghost = None
    if any(e["to"] == "__unfetched__" for e in sg["links"]):
        n_un = sum(u["n"] for u in sg["unfetched"])
        ghost = {"x": x + 10, "y": TOP + R_MAX - GHOST_H / 2, "w": GHOST_W, "h": GHOST_H,
                 "cx": x + 10, "cy": TOP + R_MAX, "n": n_un,
                 "label": "Not fetched", "sub": f"{n_un} link{'s' if n_un != 1 else ''} · {len(sg['unfetched'])} reason"
                                             f"{'s' if len(sg['unfetched']) != 1 else ''}"}
        heads.append({"x": x + 10 + GHOST_W / 2, "text": "not fetched"})
        x += GHOST_W + 30
    width = max(x + PAD, 360)
    by_id = {t["type_id"]: t for t in types}
    pairs: dict[tuple, int] = {}
    for e in sorted(sg["links"], key=lambda e: (e["from"], e["to"], not e["followed"], e["kind"])):
        a = by_id[e["from"]]
        k = pairs.get((e["from"], e["to"]), 0)
        pairs[(e["from"], e["to"])] = k + 1
        off = k * 7
        if e["to"] == "__unfetched__":
            tx, ty = ghost["x"], ghost["cy"] + (k * 5 - 5)
            if a["cx"] + COL_W >= tx:  # last type column: straight across
                sx, sy = a["cx"] + a["r"], a["cy"] + off
                bend = max(30, (tx - sx) / 2)
                e["d"] = f"M{sx:.1f},{sy:.1f} C{sx + bend:.1f},{sy:.1f} {tx - bend:.1f},{ty:.1f} {tx:.1f},{ty:.1f}"
                e["mx"], e["my"] = (sx + tx) / 2, (sy + ty) / 2 - 4
            else:  # earlier columns: up from the circle into a lane above the nodes, so no node is crossed
                # out to the gutter right of the column, up to the lane, across, down into the sink
                sx, sy = a["cx"] + a["r"], a["cy"] + off
                gx, lane = a["cx"] + 106 + 3 * k, TOP - 30 + 3 * k
                e["d"] = (f"M{sx:.1f},{sy:.1f} L{gx - 8:.1f},{sy:.1f} Q{gx:.1f},{sy:.1f} {gx:.1f},{sy - 8:.1f} "
                          f"L{gx:.1f},{lane + 8:.1f} Q{gx:.1f},{lane:.1f} {gx + 8:.1f},{lane:.1f} "
                          f"L{tx - 50:.1f},{lane:.1f} C{tx - 20:.1f},{lane:.1f} {tx - 20:.1f},{ty:.1f} {tx:.1f},{ty:.1f}")
                e["mx"], e["my"] = gx + 18, lane - 3
            continue
        b = by_id[e["to"]]
        if a is b:  # self loop above the circle (pagination, sibling links)
            cx, top = a["cx"], a["cy"] - a["r"]
            w = 14 + 6 * k
            e["d"] = f"M{cx - 5:.1f},{top:.1f} C{cx - w:.1f},{top - 24 - 4 * k:.1f} {cx + w:.1f},{top - 24 - 4 * k:.1f} {cx + 5:.1f},{top:.1f}"
            e["mx"], e["my"] = cx + w + 4, top - 18 - 4 * k
        elif a["cx"] == b["cx"]:  # same column: arc on the left
            sx, sy, ty = a["cx"] - a["r"], a["cy"] + off, b["cy"] + off
            tx = b["cx"] - b["r"]
            e["d"] = f"M{sx:.1f},{sy:.1f} C{sx - 40:.1f},{sy:.1f} {tx - 40:.1f},{ty:.1f} {tx:.1f},{ty:.1f}"
            e["mx"], e["my"] = min(sx, tx) - 34, (sy + ty) / 2
        elif b["cx"] < a["cx"]:  # back link: bow under both circles
            sx, sy = a["cx"], a["cy"] + a["r"]
            tx, ty = b["cx"], b["cy"] + b["r"]
            low = max(sy, ty) + 36 + 6 * k
            e["d"] = f"M{sx:.1f},{sy:.1f} C{sx:.1f},{low:.1f} {tx:.1f},{low:.1f} {tx:.1f},{ty:.1f}"
            e["mx"], e["my"] = (sx + tx) / 2, low - 6
        else:
            sx, sy = a["cx"] + a["r"], a["cy"] + off
            tx, ty = b["cx"] - b["r"], b["cy"] + off
            bend = max(30, (tx - sx) / 2)
            e["d"] = f"M{sx:.1f},{sy:.1f} C{sx + bend:.1f},{sy:.1f} {tx - bend:.1f},{ty:.1f} {tx:.1f},{ty:.1f}"
            e["mx"], e["my"] = (sx + tx) / 2, (sy + ty) / 2 - 4
    for e in sg["links"]:
        e["mx"], e["my"] = round(e.get("mx", 0), 1), round(e.get("my", 0), 1)
    height = max(height, (ghost["y"] + ghost["h"] + 20) if ghost else 0) + 30
    return {"width": round(width), "height": round(height), "heads": heads, "ghost": ghost,
            "scale": _scale(n_max), "unit": round(_unit(n_max), 2), "n_max": n_max}


# view models ------------------------------------------------------------------------------------------------------
def _check_run(a: Artifacts, run_id: str | None, graphs: dict) -> None:
    if run_id is None:
        return
    known = set(a.run_ids()) | set(a.gold_run_ids()) | {g.get("run_id") for g in graphs.values()}
    if run_id not in known:
        raise HTTPException(404, f"no run {run_id}")


def _backend(graphs) -> str | None:
    kinds = {(g.get("generated_by") or {}).get("backend") for g in graphs}
    return "recorded" if "recorded" in kinds else next((k for k in kinds if k), None)


def list_model(case, domain, run_id: str | None = None) -> dict:
    a = Artifacts(case)
    base = f"/cases/{case.id}"
    graphs = read_all(a, domain)
    _check_run(a, run_id, graphs)
    rows = []
    for src, g in sorted(graphs.items()):
        if run_id and g.get("run_id") != run_id:
            continue
        cov = g.get("coverage") or {}
        n_target = len(cov.get("target") or [])
        n_hint = len([p for p in cov.get("hinted") or [] if not cov.get("target") or p in cov["target"]])
        crawl = g.get("crawl") or {}
        q = urlencode({"run": run_id}) if run_id else ""
        rows.append({
            "source_id": src, "href": f"{base}/{SLUG}/{quote(src, safe='')}" + (f"?{q}" if q else ""),
            "readable": g.get("readable", False), "problems": g.get("problems") or [], "path": g["path"],
            "host": g.get("host"), "source_url": g.get("source_url"), "run_id": g.get("run_id"),
            "n_types": g.get("n_types", 0), "n_instances": g.get("n_instances", 0), "n_edges": g.get("n_edges", 0),
            "n_followed": g.get("n_followed", 0), "n_not_followed": g.get("n_not_followed", 0),
            "type_labels": [t["label"] for t in (g.get("types") or [])][:6],
            "attempted": crawl.get("attempted_pages"), "fetched": crawl.get("fetched_pages"),
            "page_cap": crawl.get("page_cap"), "max_depth": crawl.get("max_depth"),
            "stop_reason": crawl.get("stop_reason"), "stop_label": crawl.get("stop_label"),
            "stop_state": crawl.get("stop_state", "none"),
            "n_target": n_target, "n_hinted": n_hint, "n_uncovered": len(cov.get("uncovered") or []),
            "cov_pct": round(100 * n_hint / n_target) if n_target else None,
            "backend": (g.get("generated_by") or {}).get("backend")})
    readable = [r for r in rows if r["readable"]]
    m = {"case_id": case.id, "question": case.brief, "sources": SOURCES, "run_id": run_id,
         "backend": _backend(graphs.values()), "rows": rows, "n_graphs": len(readable),
         "n_files": len(rows), "n_instances": sum(r["n_instances"] for r in readable),
         "n_types": sum(r["n_types"] for r in readable), "empty": None}
    if not graphs:
        m["empty"] = gap("R15", "One graph per source a spider explored: page types labelled from the ontology with "
                                "their page counts and quoted property hints, the links between them (followed or "
                                "not, and why), which definition-of-done properties each type shows, and the crawl's "
                                "limits, robots.txt decisions and stop reason.",
                         "03-fanout/surface-map/<source>/site-graph.json")
    elif not rows:
        m["empty"] = gap(None, f"No site graph in this case was built by run {run_id}. The case package keeps the "
                               "latest graph per source.", PATTERN)
    return m


def _thumbs(a: Artifacts, base: str, inst: dict, budget: list[int]) -> None:
    """Pick a displayable capture: an explicit screenshot key, a screenshot named in the bronze sidecar, or the
    page capture itself when it is an image. HTML captures get a link, never an inline render."""
    inst["img"] = inst["raw"] = None
    key = inst.get("bronze_key")
    meta = {}
    if key and budget[0] > 0:
        budget[0] -= 1
        meta = a.bronze_meta(key)
    inst["capture_type"] = meta.get("content_type") or None
    shot = inst.get("screenshot_key") or meta.get("screenshot_key")
    if not shot and str(meta.get("content_type") or "").startswith("image/"):
        shot = key
    if shot:
        inst["img"] = f"{base}/bronze/{quote(str(shot), safe='')}"
    if key:
        inst["raw"] = f"{base}/bronze/{quote(key, safe='')}"


def _cov_state(t: dict, pid: str, pdom: str | None) -> str:
    if pid not in t["hint_ids"]:
        return "none"
    return "covered" if pdom and t.get("class_id") == pdom else "hinted"


def graph_model(case, domain, source: str, run_id: str | None = None, type_id: str | None = None,
                overlay: str | None = None) -> dict:
    a = Artifacts(case)
    base = f"/cases/{case.id}"
    graphs = read_all(a, domain)
    if source not in graphs:
        raise HTTPException(404, f"no site graph for source {source}")
    _check_run(a, run_id, graphs)
    sg = graphs[source]
    live_ids = set(a.run_ids()) | set(a.gold_run_ids())
    m = {"case_id": case.id, "question": case.brief, "sources": SOURCES, "source_id": source,
         "list_href": f"{base}/{SLUG}" + (f"?{urlencode({'run': run_id})}" if run_id else ""),
         "run_filter": run_id, "backend": _backend([sg]), "graph": None, "empty": None, "run_note": None}
    if not sg.get("readable"):
        m["empty"] = gap("R15", f"{sg['path']} exists but carries no page types this view can read"
                                + (f" ({'; '.join(sg.get('problems') or [])})" if sg.get("problems") else "") + ".",
                         sg["path"])
        m["graph"] = {k: sg.get(k) for k in ("path", "problems")}
        return m
    lay = layout(sg)
    rid = sg.get("run_id")
    if run_id and rid != run_id:
        m["run_note"] = (f"This graph was built by run {rid or '(unknown)'}, not {run_id}; the case package keeps "
                         "the latest graph per source.")
    run_href = f"{base}/runs/{quote(rid, safe='')}" if rid and rid in live_ids else None
    budget = [80]

    # overlay options: target properties first, then any other hinted property
    cov = sg["coverage"]
    opts = list(dict.fromkeys([*cov["target"], *domain_dod(domain), *cov["hinted"],
                               *(p for t in sg["types"] for p in t["hint_ids"])]))
    if overlay not in opts:
        overlay = None
    type_ids = {t["type_id"] for t in sg["types"]}
    if type_id not in type_ids:
        type_id = None

    def href(**kw) -> str:
        q = {"run": run_id, "type": type_id, "overlay": overlay} | kw
        q = {k: v for k, v in q.items() if v}
        return f"{base}/{SLUG}/{quote(source, safe='')}" + (f"?{urlencode(q)}" if q else "")

    prop_rows = []
    for pid in opts:
        pdom = _prop_domain(domain, pid)
        states = {t["type_id"]: _cov_state(t, pid, pdom) for t in sg["types"]}
        status = "hinted" if pid in cov["hinted"] else "uncovered" if pid in cov["uncovered"] else \
            ("hinted" if any(s != "none" for s in states.values()) else "not listed")
        prop_rows.append({"property_id": pid, "label": domain.prop_label(pid), "class_id": pdom,
                          "class_label": domain.class_label(pdom) if pdom else None,
                          "target": pid in cov["target"], "status": status,
                          "n_covered": sum(s == "covered" for s in states.values()),
                          "n_hinted": sum(s == "hinted" for s in states.values()),
                          "n_none": sum(s == "none" for s in states.values()),
                          "states": states, "href": href(overlay=pid), "active": pid == overlay})
    by_prop = {p["property_id"]: p for p in prop_rows}

    nodes = []
    for t in sg["types"]:
        for inst in t["insts"]:
            _thumbs(a, base, inst, budget)
            inst["step_href"] = f"{base}/runs/{quote(rid, safe='')}#{inst['trace_step_id']}" \
                if rid and inst.get("trace_step_id") else None
        t["insts"].sort(key=lambda i: (i["depth"] if i["depth"] is not None else 9, i["path"] or ""))
        state = by_prop[overlay]["states"][t["type_id"]] if overlay else None
        hints = ", ".join(h["label"] for h in t["hints"][:3]) + (f" +{len(t['hints']) - 3}" if len(t["hints"]) > 3 else "")
        nodes.append(t | {
            "href": href(type=t["type_id"]) + "#sel", "selected": t["type_id"] == type_id,
            "seed": t["type_id"] == sg["seed"], "cov": state, "glyph": COV[state][0] if state else "",
            "cov_json": json.dumps({p["property_id"]: p["states"][t["type_id"]] for p in prop_rows}),
            "label_short": _clip(t["label"], 26), "sub": f"{t['n']} page{'s' if t['n'] != 1 else ''}"
                                                          + (f" · {t['template']}" if t["template"] else ""),
            "sub_short": _clip(f"{t['n']} page{'s' if t['n'] != 1 else ''}", 26), "hints_short": _clip(hints, 28),
            "aria": f"{t['label']}: {t['n']} fetched page{'s' if t['n'] != 1 else ''}"
                    + (f", property hints {', '.join(h['label'] for h in t['hints'])}" if t["hints"] else "")
                    + (", entry page" if t["type_id"] == sg["seed"] else "") + ". Select to list its pages."})
    by_id = {n["type_id"]: n for n in nodes}
    links = []
    for e in sg["links"]:
        a_lbl = by_id[e["from"]]["label"]
        b_lbl = by_id[e["to"]]["label"] if e["to"] in by_id else "not fetched"
        why = "; ".join(f"{r['reason']} ×{r['n']}" for r in e["reason_list"])
        links.append(e | {"from_label": a_lbl, "to_label": b_lbl,
                          "title": f"{a_lbl} → {b_lbl}: {e['n']} {e['kind_label']} link{'s' if e['n'] != 1 else ''}, "
                                   f"{'followed' if e['followed'] else 'not followed'} · {'/'.join(e['method'])} "
                                   f"{'/'.join(e['risk'])}" + (f" · {why}" if why else "")})
    sel = by_id.get(type_id) if type_id else None
    graph = {k: sg[k] for k in ("path", "problems", "schema_version", "bronze_key", "generated_by", "source_url",
                                "source_href", "host", "source_fingerprint", "ontology_version", "run_id", "job_ids",
                                "crawl", "unfetched", "n_types", "n_instances", "n_edges", "n_followed",
                                "n_not_followed")}
    for rb in graph["crawl"]["robots"]:
        rb["href"] = f"{base}/bronze/{quote(rb['bronze_key'], safe='')}" if rb["bronze_key"] else None
    graph |= {"run_href": run_href,
              "bronze_href": f"{base}/bronze/{quote(sg['bronze_key'], safe='')}" if isinstance(sg.get("bronze_key"), str) else None, "nodes": nodes, "links": links, "layout": lay,
              "coverage": {k: [{"property_id": p, "label": domain.prop_label(p)} for p in v] for k, v in cov.items()},
              "props": prop_rows, "overlay": overlay, "overlay_prop": by_prop.get(overlay),
              "selected": sel["type_id"] if sel else None, "clear_type": href(type=None),
              "clear_overlay": href(overlay=None),
              "cov_legend": [{"state": k, "glyph": g, "label": lbl, "meaning": mean} for k, (g, lbl, mean) in COV.items()],
              "n_thumbs": sum(1 for n in nodes for i in n["insts"] if i["img"])}
    m["graph"] = graph
    return m


def domain_dod(domain) -> list[str]:
    return [p.id for p in domain.dod_props()]


def install(ctx: VizContext) -> None:
    app, render = ctx.app, ctx.render
    ctx.case_view(KEY, SLUG, LABEL, ORDER)

    @app.get("/cases/{case_id}/" + SLUG, response_class=HTMLResponse)
    def sites_view(request: Request, case_id: str, run: str | None = None):
        case = ctx.get_case(case_id)
        m = list_model(case, ctx.case_domain(case), run or None)
        return render(request, "viz/sites.html", nav=KEY, case=case, m=m, backend=m["backend"])

    @app.get("/cases/{case_id}/api/viz/" + SLUG)
    def sites_api(case_id: str, run: str | None = None) -> dict:
        case = ctx.get_case(case_id)
        return list_model(case, ctx.case_domain(case), run or None)

    @app.get("/cases/{case_id}/" + SLUG + "/{source_id}", response_class=HTMLResponse)
    def site_view(request: Request, case_id: str, source_id: str, run: str | None = None, type: str | None = None,  # noqa: A002
                  overlay: str | None = None):
        case = ctx.get_case(case_id)
        m = graph_model(case, ctx.case_domain(case), source_id, run or None, type or None, overlay or None)
        return render(request, "viz/site.html", nav=KEY, case=case, m=m, backend=m["backend"])

    @app.get("/cases/{case_id}/api/viz/" + SLUG + "/{source_id}")
    def site_api(case_id: str, source_id: str, run: str | None = None, type: str | None = None,  # noqa: A002
                 overlay: str | None = None) -> dict:
        case = ctx.get_case(case_id)
        return graph_model(case, ctx.case_domain(case), source_id, run or None, type or None, overlay or None)
