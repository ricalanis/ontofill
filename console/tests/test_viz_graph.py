"""Entity graph view: render, JSON view-model schema, filters, focus, edge and cluster evidence, the node cap on a
synthetic 500-entity export (written only into this test's private copy of the fixture lake), empty states, 404s."""

import json
import re
import time
from urllib.parse import quote

import pytest
from jsonschema import validate

from ontofill_console.viz.graph import DEFAULT_LIMIT, force_layout

LIB = "/cases/libraries"
SCALE_RUN = "run-scale-0500"

NODE = {
    "type": "object",
    "required": ["id", "kind", "title", "x", "y", "href", "sel_href", "aria", "path", "color"],
    "properties": {
        "id": {"type": "string"},
        "kind": {"enum": ["entity", "cluster"]},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "color": {"type": "integer"},
        "flags": {"type": "array", "items": {"type": "string"}},
        "context": {"type": "boolean"},
    },
}
EDGE = {
    "type": "object",
    "required": [
        "id",
        "kind",
        "source",
        "target",
        "relation",
        "label",
        "symmetric",
        "derived",
        "via_value_ids",
        "sel_href",
    ],
    "properties": {
        "kind": {"enum": ["link", "spoke"]},
        "symmetric": {"type": "boolean"},
        "via_value_ids": {"type": "array", "items": {"type": "string"}},
    },
}
SCHEMA = {
    "type": "object",
    "required": [
        "case_id",
        "run_id",
        "filters",
        "classes",
        "relations",
        "signals",
        "nodes",
        "edges",
        "clusters",
        "groups",
        "selected",
        "n_entities",
        "n_matched",
        "n_nodes",
        "n_edges",
        "n_links",
        "capped",
        "width",
        "height",
        "empty",
        "empty_links",
        "source",
    ],
    "properties": {
        "filters": {"type": "object", "required": ["cls", "rel", "signal", "q", "focus", "depth", "limit"]},
        "classes": {
            "type": "array",
            "items": {"type": "object", "required": ["id", "label", "n", "primary", "color", "shape", "path"]},
        },
        "relations": {
            "type": "array",
            "items": {"type": "object", "required": ["id", "label", "symmetric", "derived", "n"]},
        },
        "signals": {"type": "array", "items": {"type": "object", "required": ["id", "label", "n"]}},
        "nodes": {"type": "array", "items": NODE},
        "edges": {"type": "array", "items": EDGE},
        "clusters": {
            "type": "array",
            "items": {"type": "object", "required": ["id", "relation", "value", "members", "n_members", "expanded"]},
        },
        "groups": {"type": "array", "items": {"type": "object", "required": ["class", "label", "rows"]}},
        "n_entities": {"type": "integer"},
        "n_nodes": {"type": "integer"},
        "n_edges": {"type": "integer"},
        "capped": {"type": "boolean"},
        "width": {"type": "number"},
        "height": {"type": "number"},
        "selected": {"type": ["object", "null"]},
    },
}


def _ok(r):
    assert r.status_code == 200, r.text[:500]
    assert "built-in method" not in r.text and "Undefined" not in r.text
    return r


def test_graph_renders_libraries(client):
    m = client.get(f"{LIB}/api/viz/graph").json()
    validate(m, SCHEMA)
    assert m["empty"] is None and m["empty_links"] is None
    assert m["n_entities"] == 28 and m["n_nodes"] == 28 and not m["capped"]
    assert {r["id"] for r in m["relations"]} >= {"same_operator", "operated_by"}
    # a symmetric relation is drawn once per pair; a directed one per link
    sym = [e for e in m["edges"] if e["relation"] == "same_operator"]
    assert len({tuple(sorted((e["source"], e["target"]))) for e in sym}) == len(sym)
    assert all(e["symmetric"] for e in sym) and all(e["n_links"] >= 1 for e in sym)
    assert sum(1 for e in m["edges"] if e["relation"] == "operated_by") == 24
    lib = next(c for c in m["classes"] if c["id"] == "library")
    op = next(c for c in m["classes"] if c["id"] == "operator")
    assert lib["primary"] and op["related"] and lib["shape"] != op["shape"] and lib["color"] != op["color"]
    html = _ok(client.get(f"{LIB}/graph")).text
    assert f"Showing <b>{m['n_nodes']}</b> of {m['n_entities']}" in html
    assert f"{m['n_edges']} edges" in html
    assert html.count('class="gnode ') == m["n_nodes"]
    assert "Same operator" in html and "Operated by" in html and 'id="graph-panel"' in html
    assert 'href="/cases/libraries/entities/lib%3Afixture-001?run=run-libraries-0001"' in html  # node → entity
    assert "As a list" in html and "/static/viz-graph.js" in html


def test_layout_is_deterministic(client):
    a = client.get(f"{LIB}/api/viz/graph").json()
    b = client.get(f"{LIB}/api/viz/graph").json()
    assert [(n["id"], n["x"], n["y"]) for n in a["nodes"]] == [(n["id"], n["x"], n["y"]) for n in b["nodes"]]
    ids = ["a", "b", "c", "d"]
    assert force_layout(ids, [("a", "b"), ("b", "c")]) == force_layout(ids, [("a", "b"), ("b", "c")])


def test_filters(client):
    m = client.get(f"{LIB}/api/viz/graph", params={"cls": "operator"}).json()
    assert m["n_nodes"] == 4 and {n["class"] for n in m["nodes"]} == {"operator"} and m["n_edges"] == 0
    m = client.get(f"{LIB}/api/viz/graph", params={"rel": "operated_by"}).json()
    assert {e["relation"] for e in m["edges"]} == {"operated_by"} and m["n_edges"] == 24
    m = client.get(f"{LIB}/api/viz/graph", params={"signal": "hours_not_published"}).json()
    flagged = [n for n in m["nodes"] if not n["context"]]
    assert m["n_matched"] == 4 and len(flagged) == 4 and all("hours_not_published" in n["flags"] for n in flagged)
    assert m["n_context"] > 0  # their neighbours come along, faded
    m = client.get(f"{LIB}/api/viz/graph", params={"q": "ZZ-LIB-0007"}).json()
    assert [n["id"] for n in m["nodes"] if not n["context"]] == ["lib:fixture-007"]
    html = _ok(client.get(f"{LIB}/graph", params={"q": "no such thing"})).text
    assert "No entity matches these filters" in html


def test_focus_neighbourhood(client):
    m1 = client.get(f"{LIB}/api/viz/graph", params={"focus": "lib:fixture-001", "depth": 1}).json()
    ids = {n["id"] for n in m1["nodes"]}
    assert "lib:fixture-001" in ids and "op:fixture-1" in ids
    assert next(n for n in m1["nodes"] if n["id"] == "lib:fixture-001")["focus"]
    assert all(n["depth"] in (0, 1) for n in m1["nodes"])
    m2 = client.get(f"{LIB}/api/viz/graph", params={"focus": "lib:fixture-001", "depth": 2}).json()
    assert len(m2["nodes"]) >= len(m1["nodes"])
    html = _ok(client.get(f"{LIB}/graph", params={"focus": "lib:fixture-001", "depth": 2})).text
    assert "Neighbourhood of" in html and "back to the whole graph" in html
    # an unknown focus is an honest note, never a 500
    m = client.get(f"{LIB}/api/viz/graph", params={"focus": "lib:nope"}).json()
    assert m["focus_note"] and m["n_nodes"] == 28


def test_edge_selection_shows_link_evidence(client):
    m = client.get(f"{LIB}/api/viz/graph").json()
    edge = next(e for e in m["edges"] if e["relation"] == "operated_by")
    s = client.get(f"{LIB}/api/viz/graph", params={"sel": edge["id"]}).json()["selected"]
    assert s["kind"] == "edge" and s["label"] == "Operated by" and len(s["vias"]) == 1
    via = s["vias"][0]
    assert via["found"] and via["value_id"] == edge["via_value_ids"][0]
    assert via["lineage_href"] == f"/cases/libraries/lineage/{quote(via['value_id'], safe='')}?run=run-libraries-0001"
    ev = via["evidence"][0]
    assert ev["url"].startswith("https://") and ev["selector"] and ev["screenshot_href"]
    html = _ok(client.get(f"{LIB}/graph", params={"sel": edge["id"]})).text
    assert "Why is this value here?" in html and via["lineage_href"].replace("&", "&amp;") in html
    assert ev["selector"] in html


def test_node_selection_panel(client):
    s = client.get(f"{LIB}/api/viz/graph", params={"sel": "lib:fixture-006"}).json()["selected"]
    assert s["kind"] == "entity" and s["href"].startswith("/cases/libraries/entities/lib%3Afixture-006")
    assert s["flags"] and s["props"] and s["links"]
    assert any(p["lineage_href"] for p in s["props"])
    html = _ok(client.get(f"{LIB}/graph", params={"sel": "lib:fixture-006"})).text
    assert "Open entity with evidence" in html and "Signals need verification" in html
    s = client.get(f"{LIB}/api/viz/graph", params={"sel": "zz:unknown"}).json()["selected"]
    assert s["kind"] == "missing"


def test_parks_and_404s(client):
    m = client.get("/cases/parks/api/viz/graph").json()
    validate(m, SCHEMA)
    assert m["empty"] and m["nodes"] == []
    html = _ok(client.get("/cases/parks/graph")).text
    assert "No entities yet" in html
    assert client.get("/cases/nope/graph").status_code == 404
    assert client.get("/cases/nope/api/viz/graph").status_code == 404
    assert client.get(f"{LIB}/graph", params={"run": "run-does-not-exist"}).status_code == 404
    assert client.get(f"{LIB}/api/viz/graph", params={"run": "run-does-not-exist"}).status_code == 404


def test_listed_in_case_nav(client):
    html = client.get(f"{LIB}/entities").text
    assert 'href="/cases/libraries/graph"' in html and "Entity graph" in html


# synthetic exports in this test's private copy of the lake --------------------------------------------------------
def _lake_run(cases_dir):
    lake = cases_dir / "libraries" / "lake" / "gold"
    case_id = next(p.name for p in lake.iterdir() if p.is_dir())
    return lake / case_id


def _field(vid, value, url):
    return {
        "value_id": vid,
        "value": value,
        "confidence": 0.95,
        "status": "gold",
        "generated_by": {"backend": "recorded", "model": "synthetic", "at": "2026-09-26T18:00:00+00:00"},
        "evidence": [
            {"url": url, "selector": "dd.v", "captured_at": "2026-09-26T18:00:00+00:00", "source_id": "src-example"}
        ],
    }


def write_scale_run(cases_dir, n=500, group=4, links=True) -> str:
    """n entities of three classes; every `group` primary entities share an address, and a rule-derived symmetric
    relation links each of them to the others through that shared value (the R6 `same_value` shape)."""
    base = _lake_run(cases_dir)
    run_dir = base / (SCALE_RUN if links else "run-scale-nolinks")
    run_dir.mkdir(parents=True)
    onto = {
        "primary_class": "site",
        "classes": [
            {"id": "site", "label": "Site", "title_property": "name", "identifier_property": "code"},
            {"id": "body", "label": "Body", "title_property": "name"},
            {"id": "place", "label": "Place", "title_property": "name"},
        ],
        "properties": [
            {"id": "name", "label": "Name", "domain": "site", "datatype": "xsd:string", "dod": True},
            {"id": "code", "label": "Code", "domain": "site", "datatype": "xsd:string", "dod": True},
            {"id": "address", "label": "Address", "domain": "site", "datatype": "xsd:string", "dod": True},
            {"id": "name", "label": "Name", "domain": "body", "datatype": "xsd:string"},
            {"id": "name", "label": "Name", "domain": "place", "datatype": "xsd:string"},
        ],
        "relations": [
            {
                "id": "same_address",
                "label": "Same address",
                "domain": "site",
                "range": "site",
                "symmetric": True,
                "match": {"op": "same_value", "domain_property": "address", "range_property": "address"},
            },
            {"id": "run_by", "label": "Run by", "domain": "site", "range": "body", "symmetric": False},
        ],
        "rules": [{"id": "shared_address", "label": "Shares an address", "checks": "x", "verify": ["y"]}],
    }
    n_body, n_place = 20, 20
    n_site = n - n_body - n_place
    ents = []
    for i in range(n_body):
        ents.append(
            {
                "id": f"body:b{i:03d}",
                "class": "body",
                "classified_as": [],
                "links": [],
                "flags": [],
                "properties": {"name": _field(f"val:b{i}-name", f"Body {i}", f"https://x.example/b/{i}")},
            }
        )
    for i in range(n_place):
        ents.append(
            {
                "id": f"place:p{i:03d}",
                "class": "place",
                "classified_as": [],
                "links": [],
                "flags": [],
                "properties": {"name": _field(f"val:p{i}-name", f"Place {i}", f"https://x.example/p/{i}")},
            }
        )
    for i in range(n_site):
        g = i // group
        props = {
            "name": _field(f"val:s{i}-name", f"Site {i:03d}", f"https://x.example/s/{i}"),
            "code": _field(f"val:s{i}-code", f"ZZ-{i:04d}", f"https://x.example/s/{i}"),
            "address": _field(f"val:s{i}-address", f"Street {g}", f"https://x.example/s/{i}"),
        }
        ln, fl = [], []
        if links:
            ln.append({"property": "run_by", "target": f"body:b{i % n_body:03d}", "via_value_id": f"val:s{i}-name"})
            for j in range(g * group, min(n_site, (g + 1) * group)):
                if j != i:
                    ln.append(
                        {"property": "same_address", "target": f"site:s{j:03d}", "via_value_id": f"val:s{i}-address"}
                    )
            if g % 5 == 0:
                fl.append(
                    {
                        "rule_id": "shared_address",
                        "label": "Shares an address",
                        "explanation": "e",
                        "evidence_value_ids": [f"val:s{i}-address"],
                    }
                )
        ents.append(
            {
                "id": f"site:s{i:03d}",
                "class": "site",
                "classified_as": [],
                "properties": props,
                "links": ln,
                "flags": fl,
            }
        )
    (run_dir / "entities.jsonl").write_text("".join(json.dumps(e) + "\n" for e in ents))
    (run_dir / "ontology.json").write_text(json.dumps(onto))
    return run_dir.name


def test_cap_and_clusters_at_500(cases_dir, make_client):
    run = write_scale_run(cases_dir)
    client = make_client()
    t0 = time.perf_counter()
    r = client.get(f"{LIB}/graph", params={"run": run})
    elapsed = time.perf_counter() - t0
    _ok(r)
    assert elapsed < 2.0, elapsed
    m = client.get(f"{LIB}/api/viz/graph", params={"run": run}).json()
    validate(m, SCHEMA)
    assert m["n_entities"] == 500 and m["capped"] and m["n_nodes"] == DEFAULT_LIMIT
    assert sum(1 for n in m["nodes"] if n["kind"] == "entity") == DEFAULT_LIMIT
    assert f"Showing {DEFAULT_LIMIT} of" in r.text and "Narrow with search" in r.text
    # rule-derived relation is marked, and the shared-address cliques collapse into hubs
    rel = next(x for x in m["relations"] if x["id"] == "same_address")
    assert rel["derived"] and "same Address" in rel["rule_text"]
    assert m["clusters"] and all(c["n_members"] >= 3 for c in m["clusters"])
    hub = m["clusters"][0]
    assert hub["value"].startswith("Street ") and hub["prop"] == "address"
    assert not any(
        e["relation"] == "same_address" and e["kind"] == "link"
        for e in m["edges"]
        if e["source"] in hub["members"] and e["target"] in hub["members"]
    )
    assert any(n["kind"] == "cluster" and n["id"] == hub["id"] for n in m["nodes"])
    # selecting the hub shows the shared value's evidence; expanding restores the pairwise links
    s = client.get(f"{LIB}/api/viz/graph", params={"run": run, "sel": hub["id"]}).json()["selected"]
    assert s["kind"] == "cluster" and len(s["members"]) == hub["n_members"] and s["vias"][0]["evidence"]
    e = client.get(f"{LIB}/api/viz/graph", params={"run": run, "expand": hub["id"]}).json()
    assert next(c for c in e["clusters"] if c["id"] == hub["id"])["expanded"]
    assert any(
        x["relation"] == "same_address" and x["kind"] == "link" and x["source"] in hub["members"] for x in e["edges"]
    )
    # the full 500 renders when asked, still within budget
    t0 = time.perf_counter()
    full = client.get(f"{LIB}/api/viz/graph", params={"run": run, "limit": 500}).json()
    assert time.perf_counter() - t0 < 3.0
    assert full["n_nodes"] == 500 and not full["capped"]
    # focus keeps big graphs readable
    f = client.get(f"{LIB}/api/viz/graph", params={"run": run, "focus": "site:s000", "depth": 2}).json()
    assert 1 < f["n_nodes"] < 60 and not f["capped"]


def test_gold_without_links_groups_by_class(cases_dir, make_client):
    run = write_scale_run(cases_dir, n=60, links=False)
    client = make_client()
    m = client.get(f"{LIB}/api/viz/graph", params={"run": run}).json()
    validate(m, SCHEMA)
    assert m["n_links"] == 0 and m["n_edges"] == 0 and m["n_nodes"] == 60
    assert m["empty_links"]["row"] == "R6"
    assert [r["label"].split(" · ")[0] for r in m["grid_rows"]] == ["Site", "Body", "Place"]
    html = _ok(client.get(f"{LIB}/graph", params={"run": run})).text
    assert "No links in this run yet" in html and "Produced by gap R6" in html
    assert len(re.findall(r'class="gnode ', html)) == 60


@pytest.mark.parametrize("sel", ["", "e-0000000000", "c-0000000000"])
def test_unknown_selection_is_honest(client, sel):
    _ok(client.get(f"{LIB}/graph", params={"sel": sel}))
