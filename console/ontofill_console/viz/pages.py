"""Q4 · Which pages did it visit, and what did it do on each? Every page the engine touched in one run, grouped by
source: capture thumbnails with URL path, mode, verdict and time, each linked to its step on the run page; the site
graph a spider built for the source (page types, sample instances, edges); and the live-view link of its cell.

Reads the run's `trace.live.jsonl` (or the gold `trace.jsonl`): steps whose observed/requested/executed carry a `url`,
a `screenshot_key` or a `bronze_key`, plus the evidence of the values a step emitted; bronze sidecars
(`bronze/sha256/<hex>.meta.json`); `jobs.jsonl` and `status.json` for live-view links; and
`03-fanout/surface-map/<source>/site-graph.json` (CONTRACT v1.0.3, tolerated in partial shapes).
"""

from __future__ import annotations

from urllib.parse import quote, urlencode, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import live
from ..gold import backend_of
from .core import SAFE_ERRORS, Artifacts, VizContext, gap
from .operation import safe_url

ORDER = 40
KEY, SLUG, LABEL = "pages", "pages", "Pages visited"
SOURCES = ("runs/<case>/<run>/trace.live.jsonl · bronze/sha256/<hex>.meta.json · jobs.jsonl · "
           "03-fanout/surface-map/<source>/site-graph.json")
VERDICTS = [  # key, label, state css; most notable first
    ("quarantined", "quarantined", "quar"), ("failed", "failed", "block"), ("killed", "stopped by a limit", "block"),
    ("stopped", "stopped (hard stop)", "block"),
    ("not_achieved", "not achieved", "block"), ("uncertain", "uncertain", "pause"), ("achieved", "achieved", "done"),
    ("captured", "captured", "none")]
RANK = {k: i for i, (k, _, _) in enumerate(VERDICTS)}
VERDICT_CSS = {k: css for k, _, css in VERDICTS}
VERDICT_LABEL = {k: label for k, label, _ in VERDICTS}
PER_SOURCE = 36
NO_SOURCE = "(no source)"


# pages ------------------------------------------------------------------------------------------------------------
def _field(step: dict, key: str):
    for part in ("observed", "executed", "requested"):
        v = step.get(part)
        if isinstance(v, dict) and v.get(key):
            return v[key]
    return step.get(key)


def verdict_of(step: dict) -> str:
    kind = step.get("kind")
    if kind == "quarantine":
        return "quarantined"
    if kind == "kill" or step.get("event") == "limit_kill":
        return "killed"
    if kind == "verify":
        v = (step.get("detail") or {}).get("verdict") or "uncertain"
        return v if v in RANK else "uncertain"
    if step.get("event") == "hard_stop":
        return "stopped"
    if step.get("event") == "failure" or live._failed(step):
        return "failed"
    return "captured"


def _captures(step: dict, run) -> list[dict]:
    """(url, screenshot_key, bronze_key) triples this step touched: its own fields, then its values' evidence."""
    shot = step.get("screenshot_key") or (step.get("detail") or {}).get("screenshot_key") or _field(step, "screenshot_key")
    bkey = _field(step, "bronze_key") or _field(step, "html_key")
    url = _field(step, "url") or _field(step, "final_url") or _field(step, "requested_url")
    out = []
    if url or shot or bkey:
        out.append({"url": url if isinstance(url, str) else None, "shot": shot, "bronze": bkey})
    if run is not None:
        for vid in step.get("value_ids") or []:
            ref = run.values.get(vid)
            for e in ((ref.data.get("evidence") if ref else None) or []):
                if isinstance(e, dict) and (e.get("url") or e.get("screenshot_key")):
                    out.append({"url": e.get("url"), "shot": e.get("screenshot_key"), "bronze": e.get("bronze_key"),
                                "source_id": e.get("source_id"), "at": e.get("captured_at")})
    return out


def collect(a: Artifacts, case_id: str, rid: str, steps: list[dict], run) -> list[dict]:
    base = f"/cases/{case_id}"
    pages: dict[tuple, dict] = {}
    for s in steps:
        v = verdict_of(s)
        for c in _captures(s, run):
            meta = a.bronze_meta(c["shot"]) or a.bronze_meta(c["bronze"])
            url = c["url"] or meta.get("url")
            src = s.get("source_id") or c.get("source_id") or meta.get("source_id") or NO_SOURCE
            key = (src, url or c["shot"] or c["bronze"])
            p = pages.get(key)
            if p is None:
                parts = urlsplit(url) if url else None
                p = pages[key] = {
                    "source_id": src, "url": safe_url(url), "host": parts.hostname if parts else None,
                    "path": ((parts.path or "/") + (f"?{parts.query}" if parts.query else "")) if parts else None,
                    "mode": s.get("mode"), "verdict": v, "ts": s.get("ts") or c.get("at") or meta.get("captured_at"),
                    "step_id": s.get("step_id"), "phase": s.get("phase"), "screenshot_key": None, "bronze_key": None,
                    "content_type": None, "captures": 0, "steps": 0, "_steps": set(), "verdicts": []}
            if c["shot"] and not p["screenshot_key"]:
                smeta = a.bronze_meta(c["shot"])
                if str(smeta.get("content_type") or "image/").startswith("image/"):
                    p["screenshot_key"] = c["shot"]
            if c["bronze"] and not p["bronze_key"]:
                p["bronze_key"] = c["bronze"]
                p["content_type"] = (a.bronze_meta(c["bronze"]) or {}).get("content_type")
            p["captures"] += 1
            p["_steps"].add(s.get("step_id"))
            if v not in p["verdicts"]:
                p["verdicts"].append(v)
            if RANK[v] < RANK[p["verdict"]]:  # keep the most notable judgement and point at that step
                p.update(verdict=v, step_id=s.get("step_id"), mode=s.get("mode") or p["mode"], ts=s.get("ts") or p["ts"])
    out = []
    for p in pages.values():
        p["steps"] = len(p.pop("_steps"))
        p["verdict_label"] = VERDICT_LABEL[p["verdict"]]
        p["css"] = VERDICT_CSS[p["verdict"]]
        p["verdicts"].sort(key=RANK.get)
        p["also"] = [VERDICT_LABEL[x] for x in p["verdicts"] if x != p["verdict"]]
        p["step_href"] = f"{base}/runs/{quote(rid)}#{p['step_id']}" if p["step_id"] else None
        p["img"] = f"{base}/bronze/{quote(p['screenshot_key'], safe='')}" if p["screenshot_key"] else None
        p["raw"] = f"{base}/bronze/{quote(p['bronze_key'], safe='')}" if p["bronze_key"] else None
        out.append(p)
    out.sort(key=lambda p: (p["source_id"], RANK[p["verdict"]], p["ts"] or "", p["path"] or ""))  # notable first
    return out


# site graph (CONTRACT v1.0.3) ---------------------------------------------------------------------------------------
NODE_W, NODE_H, COL_GAP, ROW_GAP, PAD = 176, 46, 64, 16, 14


def _clip(text, n: int) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


def _hints(t: dict) -> list[str]:
    raw = t.get("property_hints") or t.get("sample_property_hints") or t.get("properties") or t.get("hints") or []
    if isinstance(raw, dict):
        raw = list(raw)
    out = []
    for h in raw if isinstance(raw, list) else []:
        h = h.get("property_id") or h.get("property") or h.get("id") if isinstance(h, dict) else h
        if h:
            out.append(str(h))
    return out


def site_graph(doc, path: str) -> dict | None:
    """Page types as nodes, type→type edges with counts, sample instances, property hints and a column layout."""
    if not isinstance(doc, dict):
        return None
    g = doc.get("graph") if isinstance(doc.get("graph"), dict) else doc
    types_raw = [t for t in (g.get("types") or g.get("page_types") or []) if isinstance(t, dict)]
    insts = [i for i in (g.get("instances") or g.get("pages") or []) if isinstance(i, dict)]
    if not types_raw:
        return None
    types, by_id = [], {}
    for n, t in enumerate(types_raw):
        tid = str(t.get("id") or t.get("type_id") or t.get("url_template") or f"type-{n + 1}")
        node = {"id": tid, "label": str(t.get("label") or t.get("page_type") or t.get("kind") or tid),
                "class_id": t.get("class_id"), "template": t.get("url_template") or t.get("template"),
                "hints": _hints(t), "samples": [], "n": 0, "depth": None}
        for u in t.get("sample_urls") or t.get("samples") or []:
            u = u.get("url") if isinstance(u, dict) else u
            if isinstance(u, str) and len(node["samples"]) < 3:
                node["samples"].append(u)
        types.append(node)
        by_id[tid] = node
    url_type: dict[str, str] = {}
    for inst in insts:
        tid = str(inst.get("type_id") or inst.get("type") or inst.get("page_type_id") or "")
        node = by_id.get(tid)
        url = inst.get("url") or inst.get("final_url") or inst.get("requested_url")
        if node is None:
            continue
        node["n"] += 1
        if isinstance(url, str):
            url_type[url] = tid
            if len(node["samples"]) < 3 and url not in node["samples"]:
                node["samples"].append(url)
        d = inst.get("depth")
        if isinstance(d, int):
            node["depth"] = d if node["depth"] is None else min(node["depth"], d)
    for node in types:
        node["n"] = node["n"] or len(node["samples"])

    def ref(e: dict, side: str) -> str | None:
        for k in (f"{side}_type", f"{side}_type_id", side):
            v = e.get(k)
            if isinstance(v, str) and v in by_id:
                return v
        u = e.get(f"{side}_url") or e.get(side)
        return url_type.get(u) if isinstance(u, str) else None

    agg: dict[tuple, dict] = {}
    for e in g.get("edges") or []:
        if not isinstance(e, dict):
            continue
        f, t = ref(e, "from"), ref(e, "to")
        if not f or not t:
            continue
        row = agg.setdefault((f, t), {"from": f, "to": t, "n": 0, "followed": False, "kind": e.get("kind")})
        row["n"] += 1
        row["followed"] = row["followed"] or e.get("followed") is not False
    edges = list(agg.values())
    # columns: crawl depth when instances give it, else breadth-first from the type of the source URL
    seed = url_type.get(g.get("source_url")) or (types[0]["id"] if types else None)
    if all(n["depth"] is None for n in types) and seed:
        by_id[seed]["depth"], frontier = 0, [seed]
        while frontier:
            nxt = []
            for f in frontier:
                for e in edges:
                    if e["from"] == f and by_id[e["to"]]["depth"] is None:
                        by_id[e["to"]]["depth"] = by_id[f]["depth"] + 1
                        nxt.append(e["to"])
            frontier = nxt
    maxd = max((n["depth"] for n in types if n["depth"] is not None), default=0)
    cols: dict[int, list] = {}
    for n in types:
        col = n["depth"] if n["depth"] is not None else maxd + 1
        cols.setdefault(col, []).append(n)
    for ci, col in enumerate(sorted(cols)):
        for ri, n in enumerate(cols[col]):
            n["x"], n["y"] = PAD + ci * (NODE_W + COL_GAP), PAD + 12 + ri * (NODE_H + ROW_GAP)
            n["seed"] = n["id"] == seed
    width = PAD * 2 + len(cols) * NODE_W + (len(cols) - 1) * COL_GAP + 44  # room for same-column arcs
    height = PAD * 2 + 12 + max(len(c) for c in cols.values()) * (NODE_H + ROW_GAP)
    for e in edges:
        a, b = by_id[e["from"]], by_id[e["to"]]
        if a is b:
            x, y = a["x"] + NODE_W, a["y"]
            e["d"] = f"M{x - 40},{y} C{x - 40},{y - 14} {x - 6},{y - 14} {x - 6},{y}"
        elif a["x"] == b["x"]:  # same column: arc on the right side
            sx, sy, ty = a["x"] + NODE_W, a["y"] + NODE_H / 2, b["y"] + NODE_H / 2
            e["d"] = f"M{sx},{sy} C{sx + 40},{sy} {sx + 40},{ty} {sx},{ty}"
        elif b["x"] < a["x"]:  # back edge: from the left of the later column to the right of the earlier one
            sx, sy, tx, ty = a["x"], a["y"] + NODE_H / 2, b["x"] + NODE_W, b["y"] + NODE_H / 2
            e["d"] = f"M{sx},{sy} C{sx - 40},{sy} {tx + 40},{ty} {tx},{ty}"
        else:
            sx, sy = a["x"] + NODE_W, a["y"] + NODE_H / 2
            tx, ty = b["x"], b["y"] + NODE_H / 2
            bend = max(40, abs(tx - sx) / 2)
            e["d"] = f"M{sx},{sy} C{sx + bend},{sy} {tx - bend},{ty} {tx},{ty}"
    for n in types:
        n["label_short"] = _clip(n["label"] + (f" · {n['class_id']}" if n["class_id"] else ""), 26)
        n["sub"] = _clip(f"{n['template'] or ''}", 22) + f" · {n['n']}"
    crawl = g.get("crawl") if isinstance(g.get("crawl"), dict) else {}
    cov = g.get("coverage") if isinstance(g.get("coverage"), dict) else {}
    return {"path": path, "source_url": g.get("source_url"), "types": types, "edges": edges,
            "n_types": len(types), "n_edges": sum(e["n"] for e in edges), "n_instances": len(insts),
            "width": width, "height": height,
            "crawl": {k: crawl.get(k) for k in ("attempted_pages", "fetched_pages", "max_depth", "page_cap", "stop_reason")
                      if crawl.get(k) is not None},
            "coverage": {k: [str(x) for x in cov.get(k) or []] for k in ("target", "hinted", "uncovered")},
            "generated_by": (doc.get("generated_by") or {}).get("backend") if isinstance(doc.get("generated_by"), dict) else None}


def graphs(a: Artifacts) -> dict[str, dict]:
    out = {}
    for rel in a.glob("03-fanout/surface-map/*/site-graph.json"):
        src = rel.split("/")[2]
        try:
            g = site_graph(a.json(rel), rel)
        except SAFE_ERRORS:
            g = None
        out[src] = g or {"path": rel, "invalid": True}
    return out


# view model --------------------------------------------------------------------------------------------------------
def _live_links(status: dict, jobs: list[dict]) -> tuple[str | None, dict[str, str]]:
    per: dict[str, str] = {}
    for j in jobs:
        sess = j.get("session") if isinstance(j.get("session"), dict) else {}
        url = safe_url(j.get("live_view_url") or sess.get("live_view_url"))
        if url and j.get("source_id"):
            per[j["source_id"]] = url
    return safe_url(status.get("live_view_url")), per


def model(case, run_id: str | None = None, source: str | None = None, verdict: str | None = None,
          show_all: bool = False) -> dict:
    a = Artifacts(case)
    base = f"/cases/{case.id}"
    live_ids, gold_ids = a.run_ids(), a.gold_run_ids()
    if run_id is not None and run_id not in live_ids and run_id not in gold_ids:
        raise HTTPException(404, f"no run {run_id}")
    rid = run_id or a.latest_run_id() or (live_ids[-1] if live_ids else None)
    run = None
    if rid is None:
        run = a.gold(None)
        rid = run.run_id if run else None
    elif rid in gold_ids:
        run = a.gold(rid)
    steps = a.steps(rid) if rid in live_ids else []
    feed = "trace.live.jsonl"
    if not steps and run is not None:
        steps, feed = live.annotate(list(run.trace)), "gold trace.jsonl"
    status = a.status(rid) if rid in live_ids else {}
    jobs = a.jobs(rid) if rid in live_ids else []
    pages = collect(a, case.id, rid, steps, run) if rid else []
    sg = graphs(a)
    run_live, per_source = _live_links(status, jobs)

    def href(**kw) -> str:
        q = {"run": run_id, "source": source, "verdict": verdict, "all": "1" if show_all else None} | kw
        q = {k: v for k, v in q.items() if v}
        return f"{base}/{SLUG}" + (f"?{urlencode(q)}" if q else "")

    sources = sorted({p["source_id"] for p in pages} | set(sg))
    counts = {k: sum(1 for p in pages if k in p["verdicts"] and (not source or p["source_id"] == source)) for k in RANK}
    shown = [p for p in pages if (not source or p["source_id"] == source) and (not verdict or verdict in p["verdicts"])]
    groups = []
    for src in sources:
        if source and src != source:
            continue
        mine = [p for p in shown if p["source_id"] == src]
        if not mine and src not in sg:
            continue
        cards = mine if show_all else mine[:PER_SOURCE]
        groups.append({"source_id": src, "n_pages": len(mine), "cards": cards, "more": len(mine) - len(cards),
                       "n_images": sum(1 for p in mine if p["img"]),
                       "site_graph": sg.get(src), "live_view_url": per_source.get(src)})
    m = {"case_id": case.id, "question": case.brief, "run_id": rid, "feed": feed if rid else None,
         "run_href": f"{base}/runs/{quote(rid)}" if rid and rid in live_ids else None,
         "runs": sorted(set(live_ids) | set(gold_ids), reverse=True), "sources": SOURCES,
         "backend": backend_of((status or {}).get("metrics") or (run.metrics if run else None), [*steps, status]),
         "filter": {"source": source, "verdict": verdict, "all": show_all},
         "source_filters": [{"source_id": s, "n": sum(1 for p in pages if p["source_id"] == s), "href": href(source=s),
                             "active": s == source} for s in sources],
         "verdict_filters": [{"verdict": k, "label": VERDICT_LABEL[k], "css": VERDICT_CSS[k], "n": n,
                              "href": href(verdict=k), "active": k == verdict} for k, n in counts.items() if n],
         "clear_source": href(source=None), "clear_verdict": href(verdict=None), "all_href": href(all="1"),
         "n_pages": len(pages), "n_shown": len(shown), "n_sources": len({p["source_id"] for p in pages}),
         "n_images": sum(1 for p in pages if p["img"]), "groups": groups, "live_view_url": run_live,
         "n_site_graphs": sum(1 for g in sg.values() if not g.get("invalid")),
         "empty": None, "graph_empty": None}
    if not rid:
        m["empty"] = gap(None, "Pages the engine opened, with their captures, mode and verdict. The engine has not "
                               f"published a run for this case ({case.lake_error or 'no run feed or gold export yet'}).",
                         "runs/<case>/<run>/trace.live.jsonl · bronze/sha256/<hex>.meta.json")
    elif not pages:
        m["empty"] = gap("R3", "No step in this run carries a URL, screenshot or capture key yet. Browser sessions write "
                               "them per step.", f"runs/<case>/{rid}/trace.live.jsonl")
    elif not shown:
        m["empty"] = gap(None, "No page matches these filters.", None)
    if not m["n_site_graphs"]:
        m["graph_empty"] = gap("R15", "Page types, sample pages and the links between them for each source, "
                                      "built by a bounded read-only spider.",
                               "03-fanout/surface-map/<source>/site-graph.json")
    return m


def install(ctx: VizContext) -> None:
    app, render = ctx.app, ctx.render
    ctx.case_view(KEY, SLUG, LABEL, ORDER)

    @app.get("/cases/{case_id}/" + SLUG, response_class=HTMLResponse)
    def pages_view(request: Request, case_id: str, run: str | None = None, source: str | None = None,
                   verdict: str | None = None, all: str | None = None):  # noqa: A002
        case = ctx.get_case(case_id)
        m = model(case, run or None, source or None, verdict if verdict in RANK else None, bool(all))
        return render(request, "viz/pages.html", nav=KEY, case=case, m=m, backend=m["backend"])

    @app.get("/cases/{case_id}/api/viz/" + SLUG)
    def pages_api(case_id: str, run: str | None = None, source: str | None = None, verdict: str | None = None,
                  all: str | None = None) -> dict:  # noqa: A002
        return model(ctx.get_case(case_id), run or None, source or None, verdict if verdict in RANK else None, bool(all))
