"""Operation (Q1 per case, Q6/Q8 in part) and Pages visited (Q4): HTML renders for a full case and an empty one, the
JSON view-models have their documented shape, key numbers reach the page, unknown ids are 404, and neither page
scrolls sideways at 1280 or 390 px."""

import json
import threading
import time

import jsonschema
import pytest
import uvicorn
from conftest import spec_for

from ontofill_console.web import create_app, settings_from_env

BAD = ("built-in method", "Undefined", "Traceback")

EMPTY = {"type": ["object", "null"], "required": ["what", "source", "row", "row_desc"]}
SHARE = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["key", "label", "n", "pct", "usd", "css"],
        "properties": {
            "n": {"type": "integer", "minimum": 1},
            "pct": {"type": "number"},
            "usd": {"type": ["number", "null"]},
        },
    },
}
PHASE = {
    "type": "object",
    "required": [
        "n",
        "name",
        "state",
        "css",
        "current",
        "steps",
        "elapsed_s",
        "elapsed",
        "usd",
        "checkpoint",
        "reopened",
    ],
    "properties": {
        "state": {"enum": ["done", "current", "paused", "failed", "pending"]},
        "steps": {"type": "integer"},
        "reopened": {"type": "integer"},
        "usd": {"type": ["number", "null"]},
        "elapsed_s": {"type": ["number", "null"]},
    },
}
CELL = {
    "type": "object",
    "required": ["role", "text", "cls"],
    "properties": {
        "role": {"enum": ["propose", "critique", "revise", "check"]},
        "cls": {"enum": ["ok", "obj", "fail", "none"]},
    },
}
THREAD = {
    "type": "object",
    "required": ["id", "label", "phase", "n_iterations", "n_objections", "stop_label", "usd", "iterations"],
    "properties": {
        "iterations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "cells", "objections", "stop", "step_id"],
                "properties": {
                    "cells": {"type": "array", "items": CELL, "minItems": 4, "maxItems": 4},
                    "objections": {"type": "array", "items": {"type": "object", "required": ["text", "resolution"]}},
                },
            },
        }
    },
}
RUN = {
    "type": "object",
    "required": [
        "run_id",
        "state",
        "status",
        "kind",
        "live",
        "replay",
        "recorded",
        "latest",
        "selected",
        "detail",
        "when",
        "href",
    ],
    "properties": {
        "state": {"enum": ["run", "pause", "block", "done", "none"]},
        "kind": {"enum": ["live", "replay", "recorded", "run"]},
        "href": {"pattern": "^/cases/"},
    },
}
OPERATION = {
    "type": "object",
    "required": [
        "case_id",
        "question",
        "run_id",
        "runs",
        "n_runs",
        "run_href",
        "pipe",
        "threads",
        "reopens",
        "deciders",
        "modes",
        "live_view",
        "n_steps",
        "usd_total",
        "priced_steps",
        "empty",
        "sources",
    ],
    "properties": {
        "runs": {"type": "array", "items": RUN},
        "pipe": {"type": "array", "items": PHASE},
        "threads": {"type": "array", "items": THREAD},
        "deciders": SHARE,
        "modes": SHARE,
        "live_view": {"type": "array", "items": {"type": "object", "required": ["label", "url"]}},
        "n_steps": {"type": "integer"},
        "usd_total": {"type": ["number", "null"]},
        "empty": EMPTY,
    },
}
CARD = {
    "type": "object",
    "required": [
        "source_id",
        "url",
        "path",
        "mode",
        "verdict",
        "verdicts",
        "ts",
        "step_id",
        "step_href",
        "img",
        "screenshot_key",
        "captures",
    ],
    "properties": {
        "verdict": {
            "enum": ["quarantined", "failed", "killed", "stopped", "not_achieved", "uncertain", "achieved", "captured"]
        },
        "img": {"type": ["string", "null"], "pattern": "^/cases/[a-z0-9-]+/bronze/"},
    },
}
PAGES = {
    "type": "object",
    "required": [
        "case_id",
        "run_id",
        "feed",
        "groups",
        "n_pages",
        "n_shown",
        "n_sources",
        "n_images",
        "source_filters",
        "verdict_filters",
        "n_site_graphs",
        "empty",
        "graph_empty",
        "live_view_url",
    ],
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["source_id", "n_pages", "cards", "more", "site_graph", "live_view_url"],
                "properties": {"cards": {"type": "array", "items": CARD}},
            },
        },
        "n_pages": {"type": "integer"},
        "empty": EMPTY,
        "graph_empty": EMPTY,
    },
}

SITE_GRAPH = {  # the proposed CONTRACT v1.0.3 shape (coord/status/codex-ontofill.md), synthetic
    "schema_version": "1.0.3",
    "bronze_key": "sha256:" + "0" * 64,
    "generated_by": {"backend": "recorded"},
    "graph": {
        "source_id": "registry-example",
        "source_url": "https://bibliotecas-registro.example/",
        "crawl": {
            "attempted_pages": 5,
            "fetched_pages": 5,
            "max_depth": 2,
            "page_cap": 40,
            "stop_reason": "queue_exhausted",
        },
        "types": [
            {"id": "home", "url_template": "/", "label": "listing", "property_hints": []},
            {
                "id": "detail",
                "url_template": "/ficha/{code}",
                "label": "detail",
                "class_id": "library",
                "property_hints": ["name", "address"],
            },
            {"id": "search", "url_template": "/buscar?q={q}", "label": "search", "property_hints": []},
        ],
        "instances": [
            {"url": "https://bibliotecas-registro.example/", "type_id": "home", "depth": 0, "status": 200},
            {"url": "https://bibliotecas-registro.example/ficha/ZZ-LIB-0001", "type_id": "detail", "depth": 1},
            {"url": "https://bibliotecas-registro.example/ficha/ZZ-LIB-0002", "type_id": "detail", "depth": 1},
            {"url": "https://bibliotecas-registro.example/buscar?q=a", "type_id": "search", "depth": 1},
        ],
        "edges": [
            {
                "from_url": "https://bibliotecas-registro.example/",
                "to_url": "https://bibliotecas-registro.example/ficha/ZZ-LIB-0001",
                "method": "GET",
                "risk_tier": "SAFE",
                "followed": True,
            },
            {
                "from_url": "https://bibliotecas-registro.example/",
                "to_url": "https://bibliotecas-registro.example/ficha/ZZ-LIB-0002",
                "method": "GET",
                "risk_tier": "SAFE",
                "followed": True,
            },
            {
                "from_url": "https://bibliotecas-registro.example/",
                "to_url": "https://bibliotecas-registro.example/buscar?q=a",
                "method": "GET",
                "risk_tier": "SAFE",
                "followed": True,
            },
            {
                "from_url": "https://bibliotecas-registro.example/buscar?q=a",
                "to_url": "https://bibliotecas-registro.example/ficha/ZZ-LIB-0001",
                "followed": False,
                "reason": "depth_limit",
            },
        ],
        "coverage": {
            "target": ["name", "address", "opening_hours"],
            "hinted": ["name", "address"],
            "uncovered": ["opening_hours"],
        },
    },
}


def clean(page: str) -> None:
    for bad in BAD:
        assert bad not in page, bad


# Operation -----------------------------------------------------------------------------------------------------------
def test_operation_full_case(client):
    m = client.get("/cases/libraries/api/viz/operation").json()
    jsonschema.validate(m, OPERATION)
    assert m["run_id"] == "run-libraries-0001" and m["runs"][0]["selected"] and m["runs"][0]["recorded"]
    pipe = {p["n"]: p for p in m["pipe"]}
    assert pipe[5]["state"] == "paused" and pipe[5]["checkpoint"] == "action"  # status: paused at the action gate
    assert pipe[3]["reopened"] == 1  # the gap loop reopened P3 once
    assert sum(p["steps"] for p in m["pipe"]) == m["n_steps"]
    assert sum(r["n"] for r in m["deciders"]) == m["n_steps"] == sum(r["n"] for r in m["modes"])
    assert {r["key"] for r in m["deciders"]} >= {"vultr", "human"}
    t = m["threads"][0]
    assert t["phase"] == 1 and t["n_iterations"] == 2 and t["n_objections"] == 1
    obj = t["iterations"][0]["objections"][0]
    assert "person" in obj["resolution"]  # the human revision answered the critic's objection
    assert m["usd_total"] is not None and m["priced_steps"] > 0
    assert m["live_view"] and m["live_view"][0]["url"].startswith("https://")

    page = client.get("/cases/libraries/operation").text
    clean(page)
    assert page.count('class="it"') == sum(t["n_iterations"] for t in m["threads"])
    assert "reopened P3 ×1" in page and "paused at action" in page
    assert f"{m['n_steps']} steps" in page
    for r in m["deciders"]:
        assert f"{r['label']} <b>{r['n']}</b>" in page
    assert m["run_href"] in page and m["live_view"][0]["url"] in page
    assert obj["text"] in page
    assert f"${m['usd_total']:.3f}" in page


def test_operation_empty_case(client):
    m = client.get("/cases/parks/api/viz/operation").json()
    jsonschema.validate(m, OPERATION)
    assert m["runs"] == [] and m["run_id"] is None and m["empty"]["what"]
    page = client.get("/cases/parks/operation").text
    clean(page)
    assert "No runs yet" in page and "Which parks have drinking water?" in page


def test_operation_recorded_run_and_404s(client):
    assert client.get("/cases/libraries/operation?run=run-libraries-0001").status_code == 200
    assert client.get("/cases/libraries/operation?run=no-such-run").status_code == 404
    assert client.get("/cases/libraries/api/viz/operation?run=no-such-run").status_code == 404
    assert client.get("/cases/nope/operation").status_code == 404
    assert client.get("/cases/nope/api/viz/operation").status_code == 404


def test_operation_live_replay_and_no_cost(cases_dir, make_client):
    """A second, replayed run with no usage fields: listed as a replay, latest first, cost shown as an empty state."""
    lake = cases_dir / "libraries" / "lake" / "runs" / "fixture-libraries"
    src = lake / "run-libraries-0001"
    rid = "run-libraries-0001-replay-120000"
    (lake / rid).mkdir()
    steps = [json.loads(x) for x in (src / "trace.live.jsonl").read_text().splitlines()]
    for s in steps:
        s.pop("usage", None)
    (lake / rid / "trace.live.jsonl").write_text("".join(json.dumps(s) + "\n" for s in steps[:10]))
    (lake / rid / "status.json").write_text(
        json.dumps({"run_id": rid, "state": "running", "phase": 1, "updated_at": "2026-09-26T19:00:00+00:00"})
    )
    (lake / "latest.json").write_text(json.dumps({"run_id": rid}))
    c = make_client()
    m = c.get("/cases/libraries/api/viz/operation").json()
    jsonschema.validate(m, OPERATION)
    assert [r["run_id"] for r in m["runs"]] == [rid, "run-libraries-0001"]
    assert m["runs"][0]["live"] and m["runs"][0]["replay"]
    assert m["usd_total"] is None and m["cost_empty"]
    assert [p["state"] for p in m["pipe"]] == ["current", "pending", "pending", "pending", "pending"]
    page = c.get("/cases/libraries/operation").text
    clean(page)
    assert "No cost in this trace" in page and "live" in page
    older = c.get("/cases/libraries/api/viz/operation?run=run-libraries-0001").json()
    assert older["run_id"] == "run-libraries-0001" and older["runs"][1]["selected"]


# Pages visited -------------------------------------------------------------------------------------------------------
def test_pages_full_case(client):
    m = client.get("/cases/libraries/api/viz/pages").json()
    jsonschema.validate(m, PAGES)
    assert m["n_pages"] > 0 and m["n_sources"] == 3 and m["n_images"] > 0
    cards = [c for g in m["groups"] for c in g["cards"]]
    assert any(c["verdict"] == "quarantined" for c in cards)
    assert any("achieved" in c["verdicts"] for c in cards)  # the vision check on the same page is kept
    assert all(c["step_href"].startswith("/cases/libraries/runs/run-libraries-0001#step:") for c in cards)
    assert m["graph_empty"]["row"] == "R15" and m["n_site_graphs"] == 0
    page = client.get("/cases/libraries/pages").text
    clean(page)
    assert page.count('class="cap st-') == len(cards)
    assert page.count('loading="lazy"') == sum(1 for c in cards if c["img"])
    assert f"{m['n_pages']} pages" in page and "No site graph yet" in page and "R15" in page
    img = next(c["img"] for c in cards if c["img"])
    r = client.get(img)
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/")


def test_pages_filters(client):
    q = client.get("/cases/libraries/api/viz/pages?verdict=quarantined").json()
    assert q["n_shown"] == 1 and all(c["verdict"] == "quarantined" for g in q["groups"] for c in g["cards"])
    s = client.get("/cases/libraries/api/viz/pages?source=municipal-example").json()
    assert [g["source_id"] for g in s["groups"]] == ["municipal-example"]
    page = client.get("/cases/libraries/pages?source=municipal-example").text
    clean(page)
    assert 'title="municipal-example" aria-current="true"' in page  # the chip reads as its host; the id is its title
    none = client.get("/cases/libraries/api/viz/pages?source=nope").json()
    assert none["n_shown"] == 0 and none["empty"]
    assert "No page matches" in client.get("/cases/libraries/pages?source=nope").text
    bogus = client.get("/cases/libraries/api/viz/pages?verdict=bogus").json()  # unknown verdicts are ignored
    assert bogus["n_shown"] == bogus["n_pages"]


def test_pages_site_graph(cases_dir, make_client):
    d = cases_dir / "libraries" / "case" / "03-fanout" / "surface-map" / "registry-example"
    d.mkdir(parents=True)
    (d / "site-graph.json").write_text(json.dumps(SITE_GRAPH))
    c = make_client()
    m = c.get("/cases/libraries/api/viz/pages").json()
    jsonschema.validate(m, PAGES)
    g = next(g for g in m["groups"] if g["source_id"] == "registry-example")["site_graph"]
    assert m["n_site_graphs"] == 1 and m["graph_empty"] is None
    assert g["n_types"] == 3 and g["n_instances"] == 4 and g["n_edges"] == 4
    by = {(e["from"], e["to"]): e for e in g["edges"]}
    assert by[("home", "detail")]["n"] == 2 and by[("search", "detail")]["followed"] is False
    assert g["coverage"]["uncovered"] == ["opening_hours"]
    page = c.get("/cases/libraries/pages").text
    clean(page)
    assert "3 page types · 4 links · 4 pages" in page and page.count('<g class="node') == 3
    assert "/ficha/{code}" in page and "edge--skip" in page


def test_pages_empty_case_and_404s(client):
    m = client.get("/cases/parks/api/viz/pages").json()
    jsonschema.validate(m, PAGES)
    assert m["run_id"] is None and m["groups"] == [] and m["empty"] and m["graph_empty"]["row"] == "R15"
    page = client.get("/cases/parks/pages").text
    clean(page)
    assert "No pages to show" in page and "No site graph yet" in page
    assert client.get("/cases/libraries/pages?run=no-such-run").status_code == 404
    assert client.get("/cases/libraries/api/viz/pages?run=no-such-run").status_code == 404
    assert client.get("/cases/nope/pages").status_code == 404


def test_subnav_links(client):
    page = client.get("/cases/libraries").text
    assert 'href="/cases/libraries/operation"' in page and 'href="/cases/libraries/pages"' in page


# browser: no sideways scroll -------------------------------------------------------------------------------------------
@pytest.fixture
def server(cases_dir):
    d = cases_dir / "libraries" / "case" / "03-fanout" / "surface-map" / "registry-example"
    d.mkdir(parents=True)
    (d / "site-graph.json").write_text(json.dumps(SITE_GRAPH))
    app = create_app(
        settings_from_env({"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir), "ONTOFILL_CONSOLE_IDENTITY": "local"})
    )
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8493, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    yield "http://127.0.0.1:8493"
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.mark.ui
def test_no_horizontal_scroll(server):
    playwright = pytest.importorskip("playwright.sync_api")
    urls = [
        "/cases/libraries/operation",
        "/cases/parks/operation",
        "/cases/libraries/pages",
        "/cases/parks/pages",
        "/cases/libraries/pages?verdict=quarantined",
    ]
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for width in (1280, 390):
            page.set_viewport_size({"width": width, "height": 900})
            for url in urls:
                page.goto(server + url)
                scroll = page.evaluate("document.documentElement.scrollWidth")
                assert scroll <= width, (url, width, scroll)
        assert not errors, errors
        browser.close()
