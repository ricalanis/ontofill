"""Q2 · Definition (brief → PRD drafts → DoD with basis → authority → factors → taxonomies → ontology → queries → critic)
and Q3 · Discovery (funnel per provider, source review with authority decisions, P4 plans)."""

import html
import json
import socket
import threading
import time

import jsonschema
import pytest
import yaml

from ontofill_console.viz import definition as dv
from ontofill_console.viz import discovery as xv

STR_OR_NULL = {"type": ["string", "null"]}
NUM_OR_NULL = {"type": ["number", "null"]}
EMPTY = {"type": ["object", "null"], "required": ["what", "source", "row", "row_desc"]}
STRIP = {"type": "object", "required": ["state", "title", "detail", "meta", "href"],
         "properties": {"state": {"const": "need"}, "href": {"type": "string", "pattern": "^/cases/"}}}

DEFINITION = {
    "type": "object",
    "required": ["case_id", "run_id", "run_ids", "live", "brief", "pending", "prd", "dod", "authority", "factors",
                 "taxonomies", "ontology", "queries", "critic", "backend"],
    "properties": {
        "run_id": STR_OR_NULL, "run_ids": {"type": "array", "items": {"type": "string"}}, "live": {"type": "boolean"},
        "brief": {"type": "object", "required": ["path", "present", "lines"]},
        "pending": {"type": "array", "items": STRIP},
        "prd": {"type": "object", "required": ["path", "present", "drafts", "diffs", "empty"], "properties": {
            "drafts": {"type": "array", "items": {"type": "object", "required": [
                "n", "path", "href", "version", "generated_by", "n_criteria", "current", "decision", "state"],
                "properties": {"n": {"type": "integer", "minimum": 1},
                               "state": {"enum": ["need", "block", "done", "none"]},
                               "decision": {"type": "object", "required": ["decision", "reason", "approver", "date"]}}}},
            "diffs": {"type": "array", "items": {"type": "object", "required": [
                "from_n", "to_n", "reason", "missing", "lines", "n_add", "n_del"], "properties": {
                "lines": {"type": "array", "items": {"type": "object", "required": ["op", "text"], "properties": {
                    "op": {"enum": ["add", "del", "ctx", "hunk"]}}}}}}},
            "empty": EMPTY}},
        "dod": {"type": "object", "required": ["rows", "by_basis", "empty"], "properties": {
            "rows": {"type": "array", "items": {"type": "object", "required": [
                "id", "metric", "operator", "target", "basis", "basis_state", "quote", "rationale", "feasibility"],
                "properties": {"basis": {"enum": ["brief", "human", "proposed", "unstated"]}}}}}},
        "authority": {"type": "object", "required": ["jurisdiction", "unknown_source_action", "tiers", "empty"],
                      "properties": {"tiers": {"type": "array", "items": {"type": "object", "required": [
                          "tier", "publishers", "n_domains"], "properties": {
                          "tier": {"enum": ["primary", "secondary", "review"]}}}}}},
        "factors": {"type": "object", "required": ["path", "href", "rows", "state", "n_accepted", "n_rejected", "empty"],
                    "properties": {"state": {"enum": ["approved", "denied", "pending", "none"]}}},
        "taxonomies": {"type": "object", "required": ["rows", "empty"], "properties": {"rows": {"type": "array", "items": {
            "type": "object", "required": ["factor_id", "root", "levels", "critic", "n_nodes", "nodes", "path", "href"]}}}},
        "ontology": {"type": "object", "required": ["path", "primary_class", "classes", "relations", "n_props", "n_dod",
                                                    "graph", "empty"], "properties": {
            "classes": {"type": "array", "items": {"type": "object", "required": ["id", "label", "primary", "props",
                                                                                  "n_props", "n_dod"]}},
            "graph": {"type": ["object", "null"], "required": ["width", "height", "nodes", "edges"]}}},
        "queries": {"type": "object", "required": ["path", "rows", "n_met", "measured", "empty"], "properties": {
            "rows": {"type": "array", "items": {"type": "object", "required": [
                "criterion_id", "text", "target", "actual", "met", "state"], "properties": {
                "actual": NUM_OR_NULL, "met": {"type": ["boolean", "null"]}}}}}},
        "critic": {"type": "object", "required": ["threads", "n_objections", "empty"], "properties": {
            "threads": {"type": "array", "items": {"type": "object", "required": [
                "id", "label", "phase", "iterations", "stop_label", "objections"]}}}},
        "backend": STR_OR_NULL,
    },
}

STAGE = {"type": "object", "required": ["key", "label", "n", "pct"],
         "properties": {"key": {"enum": ["leads", "captured", "accepted", "passed", "sources"]},
                        "n": {"type": ["integer", "null"]}, "pct": {"type": "number", "minimum": 0, "maximum": 100}}}
DISCOVERY = {
    "type": "object",
    "required": ["case_id", "run_id", "run_ids", "live", "pending", "funnel", "rounds", "p3_loops", "candidates",
                 "objectives", "site_graphs", "trace_sources", "plans", "backend"],
    "properties": {
        "pending": {"type": "array", "items": STRIP},
        "funnel": {"type": "object", "required": ["providers", "totals", "stages", "round_n", "n_uncaptured", "empty"],
                   "properties": {"providers": {"type": "array", "items": {"type": "object", "required": [
                       "provider", "stages", "basis", "calls", "credits", "cache_hits"], "properties": {
                       "stages": {"type": "array", "minItems": 5, "maxItems": 5, "items": STAGE}}}},
                       "empty": EMPTY}},
        "rounds": {"type": "array", "items": {"type": "object", "required": [
            "n", "iterations", "stop_label", "usd", "coverage", "objections", "n_leads", "n_candidates"]}},
        "candidates": {"type": "object", "required": ["rows", "source", "by_decision", "uncaptured", "empty"],
                       "properties": {"rows": {"type": "array", "items": {"type": "object", "required": [
                           "url", "host", "provider", "status", "source_id", "decision", "state", "reason", "critic",
                           "objectives", "site_href", "has_site_graph"], "properties": {
                           "decision": {"enum": ["trusted", "review", "rejected", "not captured"]}}}}}},
        "site_graphs": {"type": "object", "required": ["n", "empty"]},
        "trace_sources": {"type": "array", "items": {"type": "object", "required": [
            "source_id", "objectives", "phases", "n_steps", "plan_href", "site_href"]}},
        "plans": {"type": "object", "required": ["rows", "n_parsed", "empty"], "properties": {"rows": {"type": "array", "items": {
            "type": "object", "required": ["dir", "source_id", "objective_id", "parsed", "raw_href", "files",
                                           "target_fields", "method", "mode_range", "allowed_domains", "budget_usd",
                                           "steps"]}}}},
    },
}


def clean(page: str) -> None:
    assert "built-in method" not in page and "Undefined" not in page and "Traceback" not in page


# ---------------------------------------------------------------------------------------------------- definition
def test_definition_json_libraries(client):
    m = client.get("/cases/libraries/api/viz/definition").json()
    jsonschema.validate(m, DEFINITION)
    prd = m["prd"]
    assert [d["n"] for d in prd["drafts"]] == [1, 2]
    assert prd["drafts"][0]["state"] == "block" and prd["drafts"][0]["decision"]["reason"]
    assert prd["drafts"][1]["current"] and prd["drafts"][1]["state"] == "need"  # PRD checkpoint pending
    diff = prd["diffs"][0]
    assert (diff["from_n"], diff["to_n"], diff["n_add"], diff["n_del"]) == (1, 2, 3, 1)
    assert diff["reason"] == prd["drafts"][0]["decision"]["reason"]
    assert {r["basis"] for r in m["dod"]["rows"]} == {"brief", "human", "proposed"}
    proposed = next(r for r in m["dod"]["rows"] if r["basis"] == "proposed")
    assert proposed["rationale"] and proposed["feasibility"]
    assert m["authority"]["tiers"][0]["tier"] == "primary" and m["authority"]["unknown_source_action"] == "review"
    assert m["factors"]["state"] == "pending" and len(m["factors"]["rows"]) == 2
    assert m["taxonomies"]["rows"][0]["n_nodes"] == 3
    onto = m["ontology"]
    assert onto["primary_class"] == "library" and onto["classes"][0]["primary"]
    assert (len(onto["graph"]["nodes"]), len(onto["graph"]["edges"])) == (2, 2)
    assert onto["n_dod"] == 4
    assert m["queries"]["n_met"] == 3 and m["queries"]["measured"]
    assert m["critic"]["n_objections"] == 1 and m["critic"]["threads"][0]["objections"][0]["resolution"]
    assert {p["href"] for p in m["pending"]} >= {"/cases/libraries/approvals/01-scope"}
    assert m["backend"] == "recorded"


def test_definition_page_shows_the_numbers(client):
    page = client.get("/cases/libraries/definition").text
    clean(page)
    m = client.get("/cases/libraries/api/viz/definition").json()
    d = m["prd"]["diffs"][0]
    assert f"+{d['n_add']} −{d['n_del']} lines" in page
    assert page.count('class="add"') == d["n_add"] and page.count('class="del"') == d["n_del"]
    for r in m["dod"]["rows"]:
        assert html.escape(r["metric"]) in page
    for q in m["queries"]["rows"]:
        assert html.escape(q["text"], quote=False) in page and f">{q['actual']}<" in page
    for c in m["ontology"]["classes"]:
        assert c["label"] in page
    assert m["critic"]["threads"][0]["objections"][0]["text"] in page
    assert f"<b>{len(m['prd']['drafts'])}</b> drafts" in page
    assert page.count('class="strip st-need"') >= len(m["pending"])
    for rel in ("brief.md", "01-scope/prd.json", "02-ontology/ontology.json", "02-ontology/dod-queries.json"):
        assert f'href="/cases/libraries/files/{rel}"' in page
    assert "Simulated inference" in page
    assert '<svg class="graph"' in page and 'marker-end="url(#arrow)"' in page


def test_definition_parks_is_honest(client):
    r = client.get("/cases/parks/definition")
    assert r.status_code == 200
    clean(r.text)
    m = client.get("/cases/parks/api/viz/definition").json()
    jsonschema.validate(m, DEFINITION)
    assert m["run_id"] is None and m["prd"]["present"]
    assert m["ontology"]["empty"] and m["ontology"]["graph"] is None
    assert m["queries"]["empty"] and m["critic"]["empty"] and m["factors"]["empty"]
    assert m["taxonomies"]["empty"]["row"] == "R10"
    assert "No ontology yet" in r.text and "Produced by gap R10" in r.text


def test_definition_unknown_case_and_run(client):
    assert client.get("/cases/nope/definition").status_code == 404
    assert client.get("/cases/nope/api/viz/definition").status_code == 404
    assert client.get("/cases/libraries/definition?run=run-nope").status_code == 404
    assert client.get("/cases/parks/api/viz/definition?run=anything").status_code == 404
    r = client.get("/cases/libraries/definition?run=run-libraries-0001")
    assert r.status_code == 200 and 'aria-current="true">run-libraries-0001' in r.text
    assert client.get("/cases/libraries/api/viz/definition?run=run-libraries-0001").json()["live"] is False


def test_definition_reads_decisions_and_factor_answers(client, cases_dir):
    case = cases_dir / "libraries" / "case"
    (case / "02-ontology/factors/APPROVED").write_text(json.dumps(
        {"approver": "ana@example.org", "date": "2026-09-26", "checkpoint": "factors",
         "decisions": {"operator_kind": "accept", "service_level": "reject"}}))
    (case / "01-scope/revisions/1/APPROVED").write_text(json.dumps(
        {"approver": "bo@example.org", "date": "2026-09-25", "checkpoint": "prd", "decision": "deny",
         "reason": "targets need a basis"}))
    (case / "decisions.jsonl").write_text(json.dumps(
        {"ts": "2026-09-25T10:00:00+00:00", "checkpoint": "prd", "phase_dir": "01-scope", "decision": "deny"}) + "\n")
    m = client.get("/cases/libraries/api/viz/definition").json()
    f = m["factors"]
    assert (f["state"], f["n_accepted"], f["n_rejected"]) == ("approved", 1, 1)
    first = m["prd"]["drafts"][0]["decision"]
    assert first["reason"] == "targets need a basis" and first["approver"] == "bo@example.org"
    assert first["logged_at"] == "2026-09-25T10:00:00+00:00"
    assert m["prd"]["diffs"][0]["reason"] == "targets need a basis"
    page = client.get("/cases/libraries/definition").text
    assert "targets need a basis" in page and ">accept<" in page and ">reject<" in page


def test_missing_archived_draft_names_the_file(client, cases_dir):
    (cases_dir / "libraries/case/01-scope/revisions/1/prd.json").unlink()
    m = client.get("/cases/libraries/api/viz/definition").json()
    assert m["prd"]["diffs"][0]["missing"].startswith("01-scope/revisions/1/prd.json")
    assert "archived draft missing" in client.get("/cases/libraries/definition").text


def test_prd_diff_is_line_based():
    old = {"personas": [{"id": "a", "description": "x"}], "constraints": ["c1"],
           "definition_of_done": [{"id": "d", "metric": "m", "operator": ">=", "target": 5, "basis": "proposed"}]}
    new = {**old, "constraints": ["c1", "c2"]}
    d = dv.line_diff(dv.prd_text(old), dv.prd_text(new))
    assert (d["n_add"], d["n_del"]) == (1, 0)
    assert any(line["op"] == "add" and "constraint: c2" in line["text"] for line in d["lines"])


@pytest.mark.parametrize("n", range(1, 10))
def test_graph_layout_stays_in_the_viewbox(n):
    classes = [{"id": f"c{i}", "label": f"Class number {i}", "primary": i == 0, "n_props": 2, "n_dod": 1}
               for i in range(n)]
    rels = [{"id": f"r{i}", "label": "r", "domain": "c0", "range": f"c{i}"} for i in range(n)]
    g = dv.graph_layout(classes, rels)
    for node in g["nodes"]:
        assert node["x"] >= 0 and node["y"] >= 0
        assert node["x"] + node["w"] <= g["width"] and node["y"] + node["h"] <= g["height"]
    assert len(g["edges"]) == n
    assert g == dv.graph_layout(classes, rels)  # deterministic


# ----------------------------------------------------------------------------------------------------- discovery
def write_ledger(case):
    """A small, engine-shaped P3 ledger (field names as the discovery loop writes them) plus one P4 plan."""
    surface = case / "03-fanout/surface-map"
    surface.mkdir(parents=True, exist_ok=True)
    gb = {"backend": "vultr", "model": "m", "at": "2026-09-26T18:00:00+00:00"}
    lead = lambda n, providers: {"url": f"https://{n}.example/list", "title": n, "snippet": "", "query": "q",  # noqa: E731
                                 "discovered_by": providers[0], "providers": providers, "property_ids": ["name"],
                                 "score": 0.5, "publisher": None, "lead_only": True, "iteration": 1}
    leads = [lead("one", ["search"]), lead("two", ["search", "registry"]), lead("three", ["registry"]),
             lead("four", ["search"]), lead("five", ["registry"])]

    def cand(n, providers, **kw):
        return {"url": f"https://{n}.example/list", "title": n, "snippet": "", "discovered_by": providers[0],
                "providers": providers, "query": "q", "property_ids": ["name"], "iteration": 1, "covers": [], **kw}

    candidates = [
        cand("one", ["search"], status="confirmed", source_id="src-a", authority="auto", authority_tier="primary",
             authority_reason="approved publisher kind: city office", source_type="directory", covers=["name"]),
        cand("two", ["search", "registry"], status="confirmed", source_id="src-b", authority="review",
             authority_tier="unknown", authority_reason="publisher authority needs human review", source_type="directory"),
        cand("three", ["registry"], status="rejected", source_id="src-c", authority="review", authority_tier="unknown",
             authority_reason="publisher authority needs human review", critic={"name": "no verbatim quote on the page"}),
        cand("four", ["search"], status="capture_failed", capture_reason="http_404"),
    ]
    (surface / "leads.json").write_text(json.dumps({"note": "Leads are never evidence", "leads": leads,
                                                    "candidates": candidates, "generated_by": gb}))
    rnd = {"mode": "loop", "gaps": ["name"], "required": {"name": 2},
           "coverage": {"name": {"required": 2, "hosts": ["one.example"]}}, "iterations": 2,
           "stop_reason": "max_iterations", "usd": 0.012, "objections": ["name: 1/2 confirmed sources"],
           "queries": ["q"], "provider_yield": {"search": {"leads": 3, "captured": 2, "confirmed": 2, "sources": 1,
                                                           "calls": 4, "credits": 6},
                                                "registry": {"leads": 3, "captured": 2, "confirmed": 1, "sources": 0,
                                                             "calls": 2, "cache_hits": 1}},
           "attempts": [], "selected_source_ids": ["src-a", "src-b"], "candidate_count": 4, "lead_count": 5,
           "generated_by": gb}
    (surface / "discovery.json").write_text(json.dumps({"request_fingerprint": "x", "rounds": [rnd], "generated_by": gb}))
    (case / "03-fanout/objectives.yaml").write_text(yaml.safe_dump({"ontology_version": "v1", "prd_path": "01-scope/prd.json",
        "generated_by": gb, "objectives": [{"id": "obj-a", "source_id": "src-a", "source_url": "https://one.example/list",
                                            "discovery_provider": "search", "target_fields": ["name", "address"],
                                            "priority": 1, "expected_contribution": 0.5, "authority_tier": "primary"}]}))
    src_b = case / "03-fanout/sources/src-b"
    src_b.mkdir(parents=True)
    (src_b / "candidate.json").write_text(json.dumps({"source_id": "src-b", "url": "https://two.example/list"}))
    (src_b / "APPROVAL_PENDING.md").write_text("---\nphase: 3\ncheckpoint: source\nreason: unknown publisher\n---\n# Source\n")
    plan = case / "04-local/src-a__obj-a"
    plan.mkdir(parents=True)
    (plan / "tdd.json").write_text(json.dumps({
        "source_id": "src-a", "objective_id": "obj-a", "local_prd_path": "04-local/src-a__obj-a/local-prd.json",
        "ontology_version": "v1", "source_url": "https://one.example/list", "allowed_domains": ["one.example"],
        "target_fields": ["name", "address"], "extraction_method": "dom", "validation_rules": [],
        "rate_limit_per_minute": 6, "budget_usd": 0.25, "target_volume": 20,
        "steps": [{"id": "open_list", "description": "Open the list", "starting_mode": "D1", "allowed_modes": ["D1"],
                   "observation_channel": "text_structure", "risk_tier": "SAFE", "termination_predicate": "list shown"},
                  {"id": "read_rows", "description": "Read rows", "starting_mode": "D1", "allowed_modes": ["D1", "S1"],
                   "observation_channel": "hybrid", "risk_tier": "SAFE", "termination_predicate": "no more rows"}],
        "generated_by": gb}))
    (plan / "local-prd.json").write_text(json.dumps({"local_definition_of_done": [
        {"metric": "rows_read", "operator": ">=", "target": 20}], "global_requirement_ids": ["r_hours"]}))


def test_discovery_without_ledger_is_honest(client):
    m = client.get("/cases/libraries/api/viz/discovery").json()
    jsonschema.validate(m, DISCOVERY)
    assert m["funnel"]["providers"] == [] and "leads.json" in m["funnel"]["empty"]["what"]
    assert m["funnel"]["empty"]["row"] is None
    assert m["site_graphs"]["empty"]["row"] == "R15"
    assert {s["source_id"] for s in m["trace_sources"]} >= {"registry-example", "municipal-example"}
    assert len(m["plans"]["rows"]) == 3 and m["plans"]["n_parsed"] == 0
    page = client.get("/cases/libraries/discovery").text
    clean(page)
    assert "No discovery ledger yet" in page and "Produced by gap R15" in page
    for s in m["trace_sources"]:
        assert s["source_id"] in page
        assert f'/cases/libraries/pages?source={s["source_id"]}' in page
    for p in m["plans"]["rows"]:
        assert p["raw_href"] in page


def test_discovery_parks_and_unknowns(client):
    r = client.get("/cases/parks/discovery")
    assert r.status_code == 200
    clean(r.text)
    m = client.get("/cases/parks/api/viz/discovery").json()
    jsonschema.validate(m, DISCOVERY)
    assert m["plans"]["empty"] and m["candidates"]["empty"] and m["trace_sources"] == []
    assert client.get("/cases/nope/discovery").status_code == 404
    assert client.get("/cases/nope/api/viz/discovery").status_code == 404
    assert client.get("/cases/libraries/discovery?run=run-nope").status_code == 404
    assert client.get("/cases/libraries/discovery?run=run-libraries-0001").status_code == 200


def test_discovery_funnel_and_review_from_the_ledger(client, cases_dir):
    write_ledger(cases_dir / "libraries" / "case")
    m = client.get("/cases/libraries/api/viz/discovery").json()
    jsonschema.validate(m, DISCOVERY)
    by = {p["provider"]: {s["key"]: s["n"] for s in p["stages"]} for p in m["funnel"]["providers"]}
    assert by["search"] == {"leads": 3, "captured": 2, "accepted": 2, "passed": 1, "sources": 1}
    assert by["registry"] == {"leads": 3, "captured": 2, "accepted": 1, "passed": 0, "sources": 0}
    search = next(p for p in m["funnel"]["providers"] if p["provider"] == "search")
    assert (search["calls"], search["credits"]) == (4, 6)
    assert m["funnel"]["totals"]["leads"] == 6 and m["funnel"]["n_uncaptured"] == 1
    rows = {c["host"]: c for c in m["candidates"]["rows"]}
    assert rows["one.example"]["decision"] == "trusted" and rows["one.example"]["objectives"][0]["id"] == "obj-a"
    assert rows["two.example"]["decision"] == "review" and rows["two.example"]["checkpoint"]["state"] == "need"
    assert rows["three.example"]["decision"] == "rejected" and rows["three.example"]["critic"][0]["property"] == "name"
    assert rows["four.example"]["decision"] == "rejected" and "http_404" in rows["four.example"]["reason"]
    assert m["candidates"]["by_decision"] == {"trusted": 1, "review": 1, "rejected": 2, "not captured": 0}
    assert m["pending"] and m["pending"][0]["href"] == "/cases/libraries/files/03-fanout/sources/src-b/APPROVAL_PENDING.md"
    assert m["trace_sources"] == []  # the ledger replaces the trace fallback
    plan = next(p for p in m["plans"]["rows"] if p["source_id"] == "src-a")
    assert plan["parsed"] and plan["mode_range"] == "D1–S1" and plan["budget_usd"] == 0.25
    assert plan["allowed_domains"] == ["one.example"] and plan["local_dod"][0]["metric"] == "rows_read"
    assert m["rounds"][0]["stop_label"] == "iteration cap" and m["rounds"][0]["coverage"][0]["met"] is False

    page = client.get("/cases/libraries/discovery").text
    clean(page)
    for p in m["funnel"]["providers"]:
        assert p["provider"] in page
        for s in p["stages"]:
            assert f'<span class="num">{s["n"]}</span>' in page
    assert "6 credits" in page and "1 cache hits" in page
    for c in m["candidates"]["rows"]:
        assert c["host"] in page and html.escape(c["reason"]) in page
    assert "/cases/libraries/pages?source=src-a" in page and "no site graph (R15)" in page
    assert "/cases/libraries/files/03-fanout/sources/src-b/APPROVAL_PENDING.md" in page
    assert page.count('class="strip st-need"') == len(m["pending"]) == 1
    assert "D1–S1" in page and "open_list → read_rows" in page and "0.25 USD" in page


def test_source_checkpoint_answers_change_the_decision(client, cases_dir):
    case = cases_dir / "libraries" / "case"
    write_ledger(case)
    (case / "03-fanout/sources/src-b/APPROVED").write_text(json.dumps(
        {"approver": "ana@example.org", "date": "2026-09-26", "checkpoint": "source", "decision": "deny",
         "reason": "a blog, not the publisher", "source_fingerprint": "0" * 64}))
    rows = {c["host"]: c for c in client.get("/cases/libraries/api/viz/discovery").json()["candidates"]["rows"]}
    assert rows["two.example"]["decision"] == "rejected" and "a blog, not the publisher" in rows["two.example"]["reason"]
    (case / "03-fanout/sources/src-b/APPROVED").write_text(json.dumps(
        {"approver": "ana@example.org", "date": "2026-09-26", "checkpoint": "source", "source_fingerprint": "0" * 64}))
    rows = {c["host"]: c for c in client.get("/cases/libraries/api/viz/discovery").json()["candidates"]["rows"]}
    assert rows["two.example"]["decision"] == "trusted" and "ana@example.org" in rows["two.example"]["reason"]


def test_discovery_falls_back_to_the_round_ledger(client, cases_dir):
    case = cases_dir / "libraries" / "case"
    write_ledger(case)
    (case / "03-fanout/surface-map/leads.json").unlink()
    m = client.get("/cases/libraries/api/viz/discovery").json()
    by = {p["provider"]: p for p in m["funnel"]["providers"]}
    assert by["search"]["basis"] == "discovery.json provider_yield"
    assert {s["key"]: s["n"] for s in by["search"]["stages"]}["sources"] is None
    assert "n/a" in client.get("/cases/libraries/discovery").text


def test_decide_is_total():
    assert xv.decide({}, None)[0] == "not captured"
    assert xv.decide({"status": "captured"}, None)[0] == "review"


# --------------------------------------------------------------------------------------------------------- UI
@pytest.mark.ui
def test_views_fit_1280_and_390(cases_dir):
    playwright = pytest.importorskip("playwright.sync_api")
    import uvicorn
    from conftest import spec_for

    from ontofill_console.web import create_app, settings_from_env

    write_ledger(cases_dir / "libraries" / "case")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = create_app(settings_from_env({"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir), "ONTOFILL_CONSOLE_IDENTITY": "local"}))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            for width in (1280, 390):
                page.set_viewport_size({"width": width, "height": 900})
                for url in ("/cases/libraries/definition", "/cases/parks/definition",
                            "/cases/libraries/discovery", "/cases/parks/discovery"):
                    page.goto(f"http://127.0.0.1:{port}{url}")
                    sw = page.evaluate("document.documentElement.scrollWidth")
                    assert sw <= width, (url, width, sw)
                    wide = page.evaluate("""(w) => [...document.querySelectorAll('main *')].filter(e =>
                        e.getBoundingClientRect().right > w + 1 && !e.closest('.table-wrap,.graph-wrap,.diff')).length""", width)
                    assert wide == 0, (url, width, wide)
            assert not errors, errors
            browser.close()
    finally:
        srv.should_exit = True
        thread.join(timeout=5)
