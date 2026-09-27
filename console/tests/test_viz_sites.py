"""Site graphs (GAPS R15): list + one graph per source, HTML and JSON, over a recorded CONTRACT v1.0.3 site graph that
the tests write into their private copy of the fixture case (never into a real case or the committed fixtures)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

REPO = Path(__file__).resolve().parents[2]
SOURCE = "website-example"
RUN = "run-libraries-0001"
BAD = ("Undefined", "built-in method", "Traceback")


# recorded fixture (shape: CONTRACT v1.0.3, schemas/site-graph.schema.json) ------------------------------------------
def _h(text: str, n: int = 64) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:n]


def pid(u: str) -> str:
    return f"page-{_h('page' + u, 24)}"


def tid(name: str) -> str:
    return f"type-{_h('type' + name, 24)}"


def step(u: str) -> str:
    return f"step:{_h('step' + u, 32)}"


JOB = f"job:{_h('job', 32)}"


def bronze_keys(lake: Path, source: str = SOURCE) -> tuple[list[str], list[str]]:
    """(image keys, html keys) of this source in the fixture lake, so thumbnails and capture links resolve."""
    imgs, htmls = [], []
    for meta in sorted((lake / "bronze" / "sha256").glob("*.meta.json")):
        m = json.loads(meta.read_text())
        key = "sha256:" + meta.name.split(".")[0]
        if m.get("source_id") != source:
            continue
        (imgs if str(m.get("content_type", "")).startswith("image/") else htmls).append(key)
    return imgs, htmls


def recorded_graph(imgs: list[str], htmls: list[str], base: str = "https://libraries-web.example") -> dict:
    img = (imgs or htmls) * 8
    html = (htmls or imgs) * 8
    T = {k: tid(k) for k in ("listing", "detail", "search", "download", "about")}
    pages = []

    def page(path, t, depth, parent, kind, key, status=200, ctype="text/html; charset=utf-8", chain=None):
        u = base + path
        pages.append(
            {
                "instance_id": pid(u),
                "url": u,
                "redirect_chain": chain or [u],
                "depth": depth,
                "http_status": status,
                "content_type": ctype,
                "link_kind": kind,
                "parent_instance_id": pid(base + parent) if parent else None,
                "type_id": T[t],
                "bronze_key": key,
                "trace_step_id": step(u),
                "job_id": JOB,
            }
        )

    page("/", "listing", 0, None, "navigate", html[0])
    page("/libraries?page=2", "listing", 1, "/", "paginate", html[1])
    details = ["north", "river", "old-town", "harbor", "hill", "closed-branch"]
    for i, slug in enumerate(details):
        page(f"/libraries/{slug}", "detail", 1, "/", "navigate", img[i], status=404 if slug == "closed-branch" else 200)
    page("/search?q=library", "search", 1, "/", "search_results", html[2])
    page("/about", "about", 1, "/", "navigate", html[3], chain=[base + "/about-us", base + "/about"])
    page("/exports/registry.csv", "download", 2, "/libraries/north", "download", html[4], ctype="text/csv")

    def edge(frm, to, kind, followed, reason=None, fetched=True):
        return {
            "from_instance_id": pid(base + frm),
            "to_url": to if to.startswith("http") else base + to,
            "to_instance_id": pid(base + to) if fetched else None,
            "kind": kind,
            "method": "GET",
            "risk_tier": "SAFE",
            "followed": followed,
            "reason": reason,
        }

    edges = [
        edge("/", f"/libraries/{s}", "navigate", True, "http_404" if s == "closed-branch" else None) for s in details
    ]
    edges += [
        edge("/", "/libraries?page=2", "paginate", True),
        edge("/", "/search?q=library", "search_results", True),
        edge("/", "/about", "navigate", True),
        edge("/libraries?page=2", "/libraries/north", "navigate", False, "already_queued"),
        edge("/libraries/north", "/exports/registry.csv", "download", True),
        edge("/", "/admin", "navigate", False, "robots_disallow", fetched=False),
        edge("/about", "https://partner.example/", "navigate", False, "cross_registrable_domain", fetched=False),
    ]
    edges += [
        edge(f"/libraries/{s}", f"/libraries/{s}/history", "navigate", False, "depth_limit", fetched=False)
        for s in details[:3]
    ]
    edges += [edge("/exports/registry.csv", "/exports/registry-2.csv", "download", False, "depth_limit", fetched=False)]

    def hint(prop, path, quote):
        return {"property_id": prop, "sample_instance_id": pid(base + path), "evidence_quote": quote}

    def ptype(name, template, kind, cls, samples, hints):
        label = {"kind": kind} | ({"class_id": cls} if cls else {})
        return {
            "type_id": T[name],
            "url_template": template,
            "dom_skeleton_hash": _h("skel" + name),
            "label": label,
            "sample_instance_ids": [pid(base + s) for s in samples],
            "property_hints": hints,
        }

    types = [
        ptype(
            "listing",
            "/libraries?page={n}",
            "listing",
            "library",
            ["/", "/libraries?page=2"],
            [hint("name", "/", "North Branch Library")],
        ),
        ptype(
            "detail",
            "/libraries/{slug}",
            "detail",
            "library",
            ["/libraries/north", "/libraries/river"],
            [
                hint("name", "/libraries/north", "North Branch Library"),
                hint("address", "/libraries/north", "12 Harbor Road, Example City"),
                hint("opening_hours", "/libraries/river", "Mon–Fri 09:00–18:00"),
            ],
        ),
        ptype("search", "/search?q={q}", "search", None, ["/search?q=library"], []),
        ptype(
            "download",
            "/exports/{file}.csv",
            "download",
            None,
            ["/exports/registry.csv"],
            [hint("registry_code", "/exports/registry.csv", "LIB-0042")],
        ),
        ptype("about", "/about", "other", None, ["/about"], [hint("website", "/about", "libraries-web.example")]),
    ]
    graph = {
        "source_id": SOURCE,
        "source_url": base + "/",
        "source_fingerprint": _h("fingerprint"),
        "ontology_version": "ontology-v3",
        "run_id": RUN,
        "job_ids": [JOB],
        "crawl": {
            "same_registrable_domain": True,
            "max_depth": 2,
            "page_cap": 30,
            "delay_seconds": 1.0,
            "attempted_pages": 12,
            "fetched_pages": 11,
            "stop_reason": "queue_exhausted",
            "robots": [
                {
                    "origin": base,
                    "url": base + "/robots.txt",
                    "http_status": 200,
                    "decision": "allow",
                    "crawl_delay_seconds": None,
                    "bronze_key": html[5],
                }
            ],
        },
        "types": types,
        "instances": pages,
        "edges": edges,
        "coverage": {
            "target_property_ids": ["name", "registry_code", "address", "opening_hours"],
            "hinted_property_ids": ["name", "address", "opening_hours", "registry_code"],
            "uncovered_property_ids": [],
        },
    }
    return {
        "schema_version": "1.0.3",
        "bronze_key": "sha256:" + hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest(),
        "generated_by": {"backend": "recorded", "model": "spider-rules", "at": "2026-09-26T18:00:00Z"},
        "graph": graph,
    }


def install_graph(cases_dir: Path, doc, source: str = SOURCE, raw: str | None = None) -> Path:
    path = cases_dir / "libraries" / "case" / "03-fanout" / "surface-map" / source / "site-graph.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if raw is not None else json.dumps(doc, indent=1))
    return path


@pytest.fixture
def graph_doc(cases_dir) -> dict:
    imgs, htmls = bronze_keys(cases_dir / "libraries" / "lake")
    doc = recorded_graph(imgs, htmls)
    install_graph(cases_dir, doc)
    return doc


# view-model schemas ------------------------------------------------------------------------------------------------
GAP = {"type": ["object", "null"], "required": ["what", "row"]}
LIST = {
    "type": "object",
    "required": ["case_id", "sources", "rows", "n_graphs", "n_instances", "n_types", "empty", "backend"],
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "source_id",
                    "href",
                    "readable",
                    "n_types",
                    "n_instances",
                    "n_edges",
                    "n_followed",
                    "n_not_followed",
                    "stop_reason",
                    "n_target",
                    "n_hinted",
                    "path",
                ],
                "properties": {
                    "n_types": {"type": "integer"},
                    "n_instances": {"type": "integer"},
                    "readable": {"type": "boolean"},
                    "href": {"type": "string"},
                },
            },
        },
        "n_graphs": {"type": "integer"},
        "empty": GAP,
    },
}
NODE = {
    "type": "object",
    "required": ["type_id", "label", "n", "r", "cx", "cy", "href", "hints", "insts", "cov_json", "selected", "aria"],
    "properties": {
        "n": {"type": "integer"},
        "r": {"type": "number"},
        "insts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["instance_id", "url", "depth", "http_status", "bronze_key", "img", "raw", "step_href"],
            },
        },
    },
}
DETAIL = {
    "type": "object",
    "required": ["case_id", "source_id", "graph", "empty", "list_href"],
    "properties": {
        "graph": {
            "type": "object",
            "required": [
                "nodes",
                "links",
                "layout",
                "crawl",
                "coverage",
                "props",
                "unfetched",
                "overlay",
                "selected",
                "run_id",
                "n_types",
                "n_instances",
                "n_edges",
            ],
            "properties": {
                "nodes": {"type": "array", "items": NODE},
                "links": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["from", "to", "kind", "followed", "n", "d", "title", "reason_list"],
                    },
                },
                "layout": {"type": "object", "required": ["width", "height", "heads", "scale"]},
                "crawl": {
                    "type": "object",
                    "required": [
                        "max_depth",
                        "page_cap",
                        "delay_seconds",
                        "attempted_pages",
                        "fetched_pages",
                        "stop_reason",
                        "robots",
                    ],
                },
                "props": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["property_id", "label", "status", "n_covered", "n_hinted", "n_none", "states"],
                    },
                },
            },
        },
    },
}


def _ok(resp):
    assert resp.status_code == 200, resp.text[:500]
    for bad in BAD:
        assert bad not in resp.text, bad
    return resp.text


# tests --------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["libraries", "parks"])
def test_empty_state_names_r15(client, case):
    html = _ok(client.get(f"/cases/{case}/sites"))
    assert "R15" in html and "No site graph yet" in html
    m = client.get(f"/cases/{case}/api/viz/sites").json()
    jsonschema.validate(m, LIST)
    assert m["n_graphs"] == 0 and m["empty"]["row"] == "R15"
    assert f'href="/cases/{case}/sites"' in html  # in the case sub-nav


def test_list_with_a_recorded_graph(client, graph_doc):
    m = client.get("/cases/libraries/api/viz/sites").json()
    jsonschema.validate(m, LIST)
    assert m["n_graphs"] == 1 and m["empty"] is None and m["backend"] == "recorded"
    r = m["rows"][0]
    assert (r["source_id"], r["n_types"], r["n_instances"]) == (SOURCE, 5, 11)
    assert (r["n_target"], r["n_hinted"], r["stop_reason"]) == (4, 4, "queue_exhausted")
    assert r["n_followed"] == 10 and r["n_not_followed"] == 7
    html = _ok(client.get("/cases/libraries/sites"))
    for text in (
        SOURCE,
        "libraries-web.example",
        "5 page types",
        "11 pages fetched of 12 attempted",
        "17 links: 10 followed, 7 not",
        "every reachable link was visited",
        "4/4 target properties hinted",
        "Library detail page",
        "Simulated inference",
    ):
        assert text in html, text
    assert r["href"] in html


def test_graph_view_model_and_render(client, graph_doc):
    m = client.get(f"/cases/libraries/api/viz/sites/{SOURCE}").json()
    jsonschema.validate(m, DETAIL)
    g = m["graph"]
    labels = {n["label"]: n for n in g["nodes"]}
    assert set(labels) == {"Library listing", "Library detail page", "Search page", "Download", "Page"}
    detail, listing = labels["Library detail page"], labels["Library listing"]
    assert detail["n"] == 6 and listing["n"] == 2 and listing["seed"] and listing["depth"] == 0
    assert labels["Download"]["depth"] == 2
    # area ∝ count: r² ratio equals the count ratio
    assert abs((detail["r"] / listing["r"]) ** 2 - 3.0) < 0.1
    # layered by depth: entry left of depth 1 left of depth 2 left of the 'not fetched' sink
    assert listing["cx"] < detail["cx"] < labels["Download"]["cx"] < g["layout"]["ghost"]["x"]
    kinds = {(e["from"], e["to"], e["kind"], e["followed"]) for e in g["links"]}
    assert (listing["type_id"], detail["type_id"], "navigate", True) in kinds
    assert (listing["type_id"], listing["type_id"], "paginate", True) in kinds  # self loop
    assert (listing["type_id"], detail["type_id"], "navigate", False) in kinds  # already queued
    assert any(e["to"] == "__unfetched__" and not e["followed"] for e in g["links"])
    reasons = {u["reason"]: u["n"] for u in g["unfetched"]}
    assert reasons == {"depth_limit": 4, "robots_disallow": 1, "cross_registrable_domain": 1}
    assert g["crawl"]["robots"][0]["decision_label"] == "allowed" and g["crawl"]["robots"][0]["href"]
    html = _ok(client.get(f"/cases/libraries/sites/{SOURCE}"))
    for text in (
        "Site graph · libraries-web.example (" + SOURCE + ")",  # the host leads, the id beside it
        "Library detail page",
        "11 fetched pages",
        "17 links (10 followed, 7 not)",
        "every reachable link was visited",
        "11 fetched of 12 attempted",
        "1.0 s between requests",
        "same registrable domain only",
        "robots_disallow",
        "cross_registrable_domain",
        "depth_limit",
        "circle area ∝ fetched pages",
        "not followed",
        "Page types and their pages",
        "Opening hours",
        "viz-sites.js",
        "sg-link--skip",
        RUN,
    ):
        assert text in html, text
    assert html.count('class="sg-node') == 5 and html.count('aria-label="Library detail page: 6 fetched pages') == 1
    assert "stroke-dasharray" not in html  # styles live in CSS, not inline
    assert 'id="sg-pick"' in html and 'id="sg-pick" hidden' not in html  # nothing selected yet


def test_select_a_type_without_js(client, graph_doc):
    g = client.get(f"/cases/libraries/api/viz/sites/{SOURCE}").json()["graph"]
    detail = next(n for n in g["nodes"] if n["label"] == "Library detail page")
    m = client.get(f"/cases/libraries/api/viz/sites/{SOURCE}", params={"type": detail["type_id"]}).json()
    assert m["graph"]["selected"] == detail["type_id"]
    sel = next(n for n in m["graph"]["nodes"] if n["selected"])
    assert len(sel["insts"]) == 6
    inst = sel["insts"][0]
    assert inst["img"].startswith("/cases/libraries/bronze/sha256%3A")
    assert inst["step_href"].startswith(f"/cases/libraries/runs/{RUN}#step:")
    assert client.get(inst["img"]).status_code == 200  # the thumbnail resolves in bronze
    html = _ok(client.get(f"/cases/libraries/sites/{SOURCE}", params={"type": detail["type_id"]}))
    assert 'aria-current="true"' in html and 'id="sg-pick" hidden' in html
    assert f'<div class="sg-drill" data-type="{detail["type_id"]}">' in html  # shown, not hidden
    assert html.count('<div class="sg-drill"') == 5 and html.count("hidden>") >= 4
    assert "12 Harbor Road, Example City" in html and "404 · depth 1" in html
    assert inst["step_href"] in html and inst["img"] in html
    # an HTML capture gets a link, never an inline render
    listing = next(n for n in m["graph"]["nodes"] if n["label"] == "Library listing")
    assert listing["insts"][0]["img"] is None and listing["insts"][0]["raw"]
    # an unknown type id is ignored, never a 500
    assert (
        client.get(f"/cases/libraries/api/viz/sites/{SOURCE}", params={"type": "nope"}).json()["graph"]["selected"]
        is None
    )


def test_coverage_overlay_without_js(client, graph_doc):
    m = client.get(f"/cases/libraries/api/viz/sites/{SOURCE}", params={"overlay": "name"}).json()
    g = m["graph"]
    by = {n["label"]: n["cov"] for n in g["nodes"]}
    assert by == {
        "Library listing": "covered",
        "Library detail page": "covered",
        "Search page": "none",
        "Download": "none",
        "Page": "none",
    }
    assert g["props"][0]["property_id"] == "name" and g["overlay_prop"]["n_covered"] == 2
    m2 = client.get(f"/cases/libraries/api/viz/sites/{SOURCE}", params={"overlay": "registry_code"}).json()
    assert {n["label"]: n["cov"] for n in m2["graph"]["nodes"]}["Download"] == "hinted"
    html = _ok(client.get(f"/cases/libraries/sites/{SOURCE}", params={"overlay": "name"}))
    assert 'id="sg-covlegend" aria-label="Coverage overlay legend">' in html  # not hidden
    assert "cov-covered" in html and "●" in html and "○" in html and "quoted on a page of the property" in html
    assert '<option value="name" selected>' in html
    # an unknown overlay property is ignored
    assert (
        client.get(f"/cases/libraries/api/viz/sites/{SOURCE}", params={"overlay": "x"}).json()["graph"]["overlay"]
        is None
    )


def test_404s_and_runs(client, graph_doc):
    assert client.get("/cases/nope/sites").status_code == 404
    assert client.get("/cases/nope/api/viz/sites").status_code == 404
    assert client.get("/cases/libraries/sites/no-such-source").status_code == 404
    assert client.get("/cases/libraries/api/viz/sites/no-such-source").status_code == 404
    assert client.get("/cases/parks/sites/website-example").status_code == 404
    assert client.get("/cases/libraries/sites", params={"run": "run-missing"}).status_code == 404
    assert client.get(f"/cases/libraries/sites/{SOURCE}", params={"run": "run-missing"}).status_code == 404
    m = client.get("/cases/libraries/api/viz/sites", params={"run": RUN}).json()
    assert m["run_id"] == RUN and m["n_graphs"] == 1
    assert client.get(f"/cases/libraries/api/viz/sites/{SOURCE}", params={"run": RUN}).json()["run_note"] is None


def test_partial_and_broken_files_never_500(client, cases_dir, graph_doc):
    g = dict(graph_doc["graph"])
    for k in ("crawl", "coverage", "edges"):
        g.pop(k)
    install_graph(cases_dir, g, source="partial-example")  # bare graph object, no envelope
    install_graph(cases_dir, None, source="broken-example", raw="{not json")
    install_graph(cases_dir, {"graph": {"types": "oops", "instances": [1, 2]}}, source="odd-example")
    m = client.get("/cases/libraries/api/viz/sites").json()
    jsonschema.validate(m, LIST)
    rows = {r["source_id"]: r for r in m["rows"]}
    assert rows["partial-example"]["readable"] and rows["partial-example"]["n_types"] == 5
    assert not rows["broken-example"]["readable"] and not rows["odd-example"]["readable"]
    html = _ok(client.get("/cases/libraries/sites"))
    assert "2 unreadable files" in html
    part = _ok(client.get("/cases/libraries/sites/partial-example"))
    assert "Partial file: graph.edges missing; graph.crawl missing; graph.coverage missing" in part
    assert "stop: not recorded" in part
    for src in ("broken-example", "odd-example"):
        page = _ok(client.get(f"/cases/libraries/sites/{src}"))
        assert "This graph cannot be drawn" in page and "R15" in page
        assert client.get(f"/cases/libraries/api/viz/sites/{src}").json()["graph"]["problems"]


def test_recorded_fixture_matches_the_engine_schema(cases_dir):
    schema_dir = REPO / "schemas"
    if not (schema_dir / "site-graph.schema.json").is_file():
        pytest.skip("schemas/site-graph.schema.json not merged yet (R15)")
    from referencing import Registry, Resource

    schemas = {p.name: json.loads(p.read_text()) for p in schema_dir.glob("*.schema.json")}
    registry = Registry().with_resources(
        (uri, Resource.from_contents(s)) for name, s in schemas.items() for uri in (name, s.get("$id", name))
    )
    imgs, htmls = bronze_keys(cases_dir / "libraries" / "lake")
    jsonschema.Draft202012Validator(schemas["site-graph.schema.json"], registry=registry).validate(
        recorded_graph(imgs, htmls)
    )


def test_graph_from_another_run_is_said_not_hidden(client, cases_dir, graph_doc):
    doc = json.loads(json.dumps(graph_doc))
    doc["graph"]["run_id"] = "run-libraries-0000"
    install_graph(cases_dir, doc)
    m = client.get("/cases/libraries/api/viz/sites", params={"run": RUN}).json()
    assert m["rows"] == [] and m["empty"]["row"] is None and "run-libraries-0001" in m["empty"]["what"]
    html = _ok(client.get(f"/cases/libraries/sites/{SOURCE}", params={"run": RUN}))
    assert "This graph was built by run run-libraries-0000, not run-libraries-0001" in html
    assert client.get("/cases/libraries/sites", params={"run": "run-libraries-0000"}).status_code == 200


def test_a_crawl_that_fetched_nothing_says_why(client, cases_dir, graph_doc):
    """Live: robots.txt answered 403, the spider stopped conservatively and fetched 0 pages. The graph file is valid
    but has no page types; the view says why, with the crawl record, instead of "cannot be drawn"."""
    doc = json.loads(json.dumps(graph_doc))
    g = doc["graph"]
    g["types"], g["instances"], g["edges"] = [], [], []
    g["crawl"].update(
        attempted_pages=1,
        fetched_pages=0,
        stop_reason="robots",
        robots=[
            {
                "origin": "https://registry.example",
                "url": "https://registry.example/robots.txt",
                "http_status": 403,
                "decision": "conservative_stop",
                "crawl_delay_seconds": None,
            }
        ],
    )
    install_graph(cases_dir, doc, source="stopped-source")
    page = client.get("/cases/libraries/sites/stopped-source").text
    for bad in BAD:
        assert bad not in page
    assert "The spider fetched 0 of 1 pages" in page and "cannot be drawn" not in page
    assert "robots.txt at registry.example answered HTTP 403, so the spider stopped conservatively." in page
    assert "stopped (no usable robots.txt)" in page  # the crawl panel's robots table
    listing = client.get("/cases/libraries/sites").text
    assert "no page to draw: 0 of 1 pages fetched · stop: robots.txt stopped the crawl" in listing
    assert "1 crawl that fetched nothing drawable" in listing and "unreadable file" not in listing
