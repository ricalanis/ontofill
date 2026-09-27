"""Q7–Q10 · failures, cost, learning and compare views: HTML renders for both fixture cases, JSON view-models match
their schemas, key numbers reach the page, unknown cases/runs are 404, and a second run exercises the diffs."""

import json
import shutil

import jsonschema
import pytest

GOLD = "libraries/lake/gold/fixture-libraries"
LIVE = "libraries/lake/runs/fixture-libraries"
R1, R2 = "run-libraries-0001", "run-libraries-0002"
CASE_PAGES = ("failures", "cost", "learning", "compare")

STRIP = {"type": "object", "required": ["kind", "state", "title", "detail", "step_id", "href"],
         "properties": {"state": {"enum": ["block", "quar", "pause"]}, "href": {"type": "string", "pattern": "^/cases/"}}}
CELL = {"type": "object", "required": ["job_id", "checkpoints", "passed", "failed", "missing", "teardown", "href"],
        "properties": {"checkpoints": {"type": "array", "minItems": 6, "maxItems": 6,
                                       "items": {"type": "object", "required": ["key", "state", "glyph"],
                                                 "properties": {"state": {"enum": ["pass", "fail", "pending"]}}}},
                       "teardown": {"type": "boolean"}}}
FAILURES = {"type": "object", "required": ["case_id", "run_id", "runs", "strips", "counts", "n_strips", "cells", "totals",
                                           "empty", "cells_empty"],
            "properties": {"run_id": {"type": ["string", "null"]}, "strips": {"type": "array", "items": STRIP},
                           "cells": {"type": "array", "items": CELL}, "n_strips": {"type": "integer"},
                           "totals": {"type": "object", "required": ["jobs", "all_pass", "teardown", "by_checkpoint"]}}}
BAR = {"type": "object", "required": ["key", "label", "metric", "rows", "table"],
       "properties": {"metric": {"enum": ["usd", "steps"]},
                      "rows": {"type": "array", "items": {"type": "object", "required": ["name", "share", "c"],
                                                          "properties": {"c": {"type": "string", "pattern": "^var\\(--"}}}}}}
COST = {"type": "object", "required": ["case_id", "run_id", "has_cost", "usd_total", "n_priced", "n_steps", "bars", "loops",
                                       "n_values", "per_value", "budget_usd", "burn", "decisions_by_backend", "empty"],
        "properties": {"has_cost": {"type": "boolean"}, "usd_total": {"type": ["number", "null"]},
                       "bars": {"type": "array", "minItems": 4, "maxItems": 4, "items": BAR},
                       "per_value": {"type": ["number", "null"]}}}
LEARNING = {"type": "object", "required": ["case_id", "runs", "n_runs", "chart", "repair_groups", "n_repairs", "macros",
                                           "n_macro_versions", "crystallizations", "empty", "repairs_empty", "macros_empty"],
            "properties": {"runs": {"type": "array", "items": {"type": "object", "required": ["run_id", "modes", "share"]}},
                           "chart": {"type": "object", "required": ["points", "n", "path", "yticks"]},
                           "repair_groups": {"type": "array", "items": {"type": "object", "required": ["source_id", "attempts"]}}}}
COL = {"type": "object", "required": ["id", "title", "question", "prd", "ontology", "discovery", "output", "runs"],
       "properties": {"prd": {"type": "object", "required": ["present", "personas", "dod"]},
                      "discovery": {"type": "object", "required": ["n_sources"]},
                      "runs": {"type": "object", "required": ["n", "modes", "code_only"]}}}
COMPARE = {"type": "object", "required": ["a", "b", "case_ids", "cols", "empty"],
           "properties": {"cols": {"type": "array", "items": COL}}}
DELTA = {"type": "object", "required": ["label", "a", "b", "delta", "dir"]}
COMPARE_RUNS = {"type": "object", "required": ["case_id", "runs", "run_a", "run_b", "ready", "metrics", "props", "added",
                                               "removed", "changed", "n_added", "n_removed", "n_changed", "modes", "empty"],
                "properties": {"metrics": {"type": "array", "items": DELTA}, "modes": {"type": "array", "items": DELTA},
                               "ready": {"type": "boolean"}}}


def _page_ok(page: str) -> None:
    assert "built-in method" not in page and "Undefined" not in page and "Traceback" not in page


def _jsonl(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.fixture
def two_runs(cases_dir):
    """A second run of the libraries case: one entity dropped, one value changed, priced steps, a budget, failures of
    every kind, a failed checkpoint, a dropped source, and two promoted macro versions."""
    src, dst = cases_dir / GOLD / R1, cases_dir / GOLD / R2
    shutil.copytree(src, dst)
    ents = _jsonl(dst / "entities.jsonl")
    dropped = ents.pop(0)
    changed = next(e for e in ents if e.get("class") == dropped.get("class"))
    prop = next(k for k, v in changed["properties"].items() if isinstance(v, dict) and v.get("value") is not None)
    changed["properties"][prop]["value"] = "CHANGED-IN-RUN-2"
    _write(dst / "entities.jsonl", ents)
    live_src, live_dst = cases_dir / LIVE / R1, cases_dir / LIVE / R2
    shutil.copytree(live_src, live_dst)
    steps = [dict(s, run_id=R2, ts=s["ts"].replace("2026-09-26T18", "2026-09-26T20")) for s in _jsonl(live_dst / "trace.live.jsonl")]
    base = {"run_id": R2, "phase": 5, "objective_id": None, "tdd_path": None, "parent_step_id": None, "value_ids": [],
            "ts": "2026-09-26T21:00:00+00:00", "generated_by": {"backend": "vultr", "model": "m-live", "at": "2026-09-26T21:00:00+00:00"}}
    steps += [
        {**base, "step_id": "y1", "source_id": "src-a", "mode": "S1", "observed": "open", "requested": "open the list",
         "executed": "stopped", "evaluated": {"status": "stopped", "reason": "captcha shown"}, "event": "hard_stop"},
        {**base, "step_id": "y2", "source_id": "src-b", "mode": "D1", "observed": "x", "requested": "parse table",
         "executed": "raised", "evaluated": {"status": "error", "error": "ValueError"}},
        {**base, "step_id": "y3", "source_id": "src-b", "mode": "S1", "observed": "x", "requested": "open cdn page",
         "executed": "refused", "evaluated": {"status": "domain_not_allowed", "url": "https://cdn.example/x"}},
        {**base, "step_id": "y4", "source_id": "src-b", "mode": "S1", "observed": "x", "requested": "parse table",
         "executed": "done", "evaluated": "ok", "event": "escalation", "parent_step_id": "y2"},
        {**base, "step_id": "y5", "source_id": "src-c", "mode": "S1", "observed": "x", "requested": "plan", "executed": "done",
         "evaluated": "ok", "usage": {"model": "m-live", "backend": "vultr", "input_tokens": 1200, "output_tokens": 300, "est_usd": 0.25}},
        {**base, "step_id": "y6", "source_id": "src-c", "mode": "S2", "observed": "x", "requested": "look", "executed": "done",
         "evaluated": "ok", "usage": {"model": "m-vision", "backend": "vultr", "input_tokens": 800, "output_tokens": 100, "est_usd": 0.75}},
        {**base, "step_id": "y7", "source_id": "src-c", "mode": "D1", "observed": "x", "requested": "promote extractor",
         "executed": {"tool": "code.promote", "version": "v2"}, "evaluated": "ok", "event": "crystallization"},
        {**base, "step_id": "y8", "source_id": "src-c", "mode": "S1", "observed": "x", "requested": {"tool": "browser.act", "action": "send form"},
         "executed": "held", "evaluated": {"status": "denied"}, "event": "action_gate",
         "gate": {"action": "send form", "risk_tier": "HIGH", "decided_by": "code", "outcome": "denied", "approval_path": None}},
    ]
    _write(live_dst / "trace.live.jsonl", steps)
    status = json.loads((live_dst / "status.json").read_text())
    status.update(run_id=R2, state="done", budget_usd=2.0,
                  sources=[{"source_id": "src-a", "source_type": "registry", "health": {"ok": 0, "failed": 3, "yield": 0}},
                           {"source_id": "src-b", "source_type": "website", "health": {"ok": 4, "failed": 1, "yield": 6}}])
    (live_dst / "status.json").write_text(json.dumps(status))
    jobs = _jsonl(live_dst / "jobs.jsonl")
    bad = json.loads(json.dumps(jobs[0]))
    bad.update(job_id="job:bad", run_id=R2, killed_by="memory")
    bad["checkpoints"]["isolation"]["probes"][0]["result"] = "ALLOWED"
    del bad["checkpoints"]["secrets"]
    _write(live_dst / "jobs.jsonl", [dict(jobs[0], run_id=R2), bad])
    for ver, day in (("v1", "2026-09-26T19:00:00+00:00"), ("v2", "2026-09-26T21:00:00+00:00")):
        d = cases_dir / "libraries/case/05-macros/src-c" / ver
        d.mkdir(parents=True)
        (d / "macro.json").write_text(json.dumps({"promoted_at": day, "test": {"precision": 1.0, "coverage": 0.9}}))
        (d / "extract.py").write_text("def extract(page):\n    return {}\n")
    return cases_dir


# --- HTML for both fixture cases ----------------------------------------------------------------------------------

@pytest.mark.parametrize("slug", CASE_PAGES)
@pytest.mark.parametrize("case", ["libraries", "parks"])
def test_case_pages_render(client, case, slug):
    r = client.get(f"/cases/{case}/{slug}")
    assert r.status_code == 200, r.text[:400]
    _page_ok(r.text)
    assert "The case's question" in r.text  # every case view shows the brief at the top
    assert client.get(f"/cases/{case}/api/viz/{slug}").status_code == 200


@pytest.mark.parametrize("slug", CASE_PAGES)
def test_unknown_case_and_run_are_404(client, slug):
    assert client.get(f"/cases/nope/{slug}").status_code == 404
    assert client.get(f"/cases/nope/api/viz/{slug}").status_code == 404
    param = "run_a" if slug == "compare" else "run"
    assert client.get(f"/cases/libraries/{slug}?{param}=no-such-run").status_code == 404
    assert client.get(f"/cases/libraries/api/viz/{slug}?{param}=no-such-run").status_code == 404


def test_nav_lists_the_views(client):
    page = client.get("/cases/libraries/failures").text
    for slug, label in (("failures", "Failures"), ("cost", "Cost"), ("learning", "Learning"), ("compare", "Compare runs")):
        assert f'href="/cases/libraries/{slug}"' in page and label in page
    assert 'href="/compare"' in page


# --- Q7 failures ----------------------------------------------------------------------------------------------------

def test_failures_fixture_run(client):
    m = client.get("/cases/libraries/api/viz/failures").json()
    jsonschema.validate(m, FAILURES)
    kinds = {s["kind"] for s in m["strips"]}
    assert {"quarantine", "limit_kill", "gate"} <= kinds
    assert m["totals"]["jobs"] == 1 and m["totals"]["all_pass"] == 1 and m["totals"]["teardown"] == 1
    page = client.get("/cases/libraries/failures").text
    for s in m["strips"]:
        assert s["href"] in page
    assert page.count('class="ck ck--pass" role="cell"') == 6
    assert f"{m['n_strips']} in {m['n_steps']} steps" in page


def test_failures_every_kind(make_client, two_runs):
    c = make_client()
    m = c.get(f"/cases/libraries/api/viz/failures?run={R2}").json()
    jsonschema.validate(m, FAILURES)
    kinds = {s["kind"] for s in m["strips"]}
    assert {"stop", "failure", "blocked_domain", "escalation", "gate", "limit_kill", "quarantine", "checkpoint_fail",
            "source_dropped", "source_degraded"} <= kinds, kinds
    bad = next(r for r in m["cells"] if r["job_id"] == "job:bad")
    states = {c["key"]: c["state"] for c in bad["checkpoints"]}
    assert states["isolation"] == "fail" and states["secrets"] == "pending" and bad["killed_by"] == "memory"
    page = c.get(f"/cases/libraries/failures?run={R2}").text
    _page_ok(page)
    assert "Stopped: captcha" in page and "Source dropped · src-a" in page and 'class="ck ck--fail"' in page


def test_failures_parks_is_honest(client):
    m = client.get("/cases/parks/api/viz/failures").json()
    jsonschema.validate(m, FAILURES)
    assert m["run_id"] is None and m["strips"] == [] and m["empty"]["row"] == "R8" and m["cells_empty"]["row"] == "R3"
    page = client.get("/cases/parks/failures").text
    assert "No run yet" in page and "Produced by gap R8" in page


# --- Q8 cost ----------------------------------------------------------------------------------------------------------

def test_cost_fixture_run(client):
    m = client.get("/cases/libraries/api/viz/cost").json()
    jsonschema.validate(m, COST)
    assert m["has_cost"] and m["usd_total"] == pytest.approx(0.016) and m["n_priced"] == 4
    assert m["n_values"] > 0 and m["per_value"] == pytest.approx(0.016 / m["n_values"], rel=1e-3)
    assert {r["name"] for r in m["decisions_by_backend"]} == {"recorded"}
    page = client.get("/cases/libraries/cost").text
    _page_ok(page)
    assert "$0.0160" in page and f"{m['n_priced']} priced of {m['n_steps']} steps" in page
    assert "Simulated inference" in page  # recorded backend banner


def test_cost_budget_burn_and_breakdown(make_client, two_runs):
    c = make_client()
    m = c.get(f"/cases/libraries/api/viz/cost?run={R2}").json()
    jsonschema.validate(m, COST)
    assert m["usd_total"] == pytest.approx(1.016) and m["budget_usd"] == 2.0 and m["burn"] == pytest.approx(0.508)
    by_model = {r["name"]: r["usd"] for r in next(b for b in m["bars"] if b["key"] == "model")["table"]}
    assert by_model["m-vision"] == pytest.approx(0.75)
    by_mode = next(b for b in m["bars"] if b["key"] == "mode")
    assert [r["name"] for r in by_mode["rows"]] == ["S1", "S2"]  # D0..S2 order, zero rows dropped
    page = c.get(f"/cases/libraries/cost?run={R2}").text
    assert "$1.0160" in page and "50.8% burned" in page and "m-vision" in page


def test_cost_without_prices_says_so(make_client, two_runs):
    live = two_runs / LIVE / R1 / "trace.live.jsonl"
    _write(live, [{k: v for k, v in s.items() if k != "usage"} for s in _jsonl(live)])
    m = make_client().get(f"/cases/libraries/api/viz/cost?run={R1}").json()
    assert not m["has_cost"] and m["usd_total"] is None and all(b["metric"] == "steps" for b in m["bars"])
    assert "Cost per call isn't recorded yet" in m["empty"]["what"] and "CONTRACT REQUEST" in m["empty"]["what"]


def test_cost_reads_spend_tracker(client, tmp_path, monkeypatch):
    hist = tmp_path / "history.jsonl"
    hist.write_text(json.dumps({"ts": "2026-09-26T20:00:00+00:00", "credit_used": 3.0,
                                "engine": {"reported": True, "runs": {R1: 0.0421}}}) + "\n")
    monkeypatch.setenv("ONTOFILL_CONSOLE_SPEND_HISTORY", str(hist))
    m = client.get("/cases/libraries/api/viz/cost").json()
    assert m["tracker_run_usd"] == 0.0421
    assert "$0.0421" in client.get("/cases/libraries/cost").text


# --- Q9 learning --------------------------------------------------------------------------------------------------------

def test_learning_fixture(client):
    m = client.get("/cases/libraries/api/viz/learning").json()
    jsonschema.validate(m, LEARNING)
    assert m["n_repairs"] == 1 and m["repair_groups"][0]["source_id"] == "website-example"
    a = m["repair_groups"][0]["attempts"][0]
    assert a["attempt"] == 1 and a["max_attempts"] == 3 and a["precision"] == 0.5 and "KeyError" in a["stderr"]
    assert m["macros_empty"]["row"] == "R4" and m["chart"]["n"] == 1
    page = client.get("/cases/libraries/learning").text
    _page_ok(page)
    assert "Attempt 1 of 3" in page and "KeyError" in page and "Produced by gap R4" in page
    assert f"{m['chart']['points'][0]['pct']}%" in page


def test_learning_over_two_runs(make_client, two_runs):
    c = make_client()
    m = c.get("/cases/libraries/api/viz/learning").json()
    jsonschema.validate(m, LEARNING)
    assert [r["run_id"] for r in m["runs"]] == [R1, R2] and m["chart"]["n"] == 2 and m["chart"]["path"].count("L") == 1
    assert m["n_macro_versions"] == 2 and [v["version"] for v in m["macros"][0]["versions"]] == ["v1", "v2"]
    assert m["macros"][0]["versions"][1]["date"] == "2026-09-26T21:00:00+00:00"
    assert len(m["crystallizations"]) == 1
    page = c.get("/cases/libraries/learning").text
    assert "<path class=\"line\"" in page and "src-c" in page and "Crystallized" in page


def test_learning_parks(client):
    m = client.get("/cases/parks/api/viz/learning").json()
    jsonschema.validate(m, LEARNING)
    assert m["empty"]["row"] == "R4" and m["runs"] == []


# --- Q10 compare -------------------------------------------------------------------------------------------------------

def test_compare_cases_default_and_swap(client):
    m = client.get("/api/viz/compare").json()
    jsonschema.validate(m, COMPARE)
    assert (m["a"], m["b"]) == ("libraries", "parks") and len(m["cols"]) == 2
    lib, parks = m["cols"]
    assert lib["ontology"]["primary"] and lib["output"]["entities"] == 24 and lib["runs"]["n"] == 1
    assert parks["output"] is None and parks["ontology"] is None and parks["prd"]["present"]
    page = client.get("/compare").text
    _page_ok(page)
    assert lib["question"] in page and parks["question"] in page and lib["ontology"]["primary"] in page
    swapped = client.get("/api/viz/compare?a=parks&b=libraries").json()
    assert [c["id"] for c in swapped["cols"]] == ["parks", "libraries"]
    assert client.get("/compare?a=nope").status_code == 404 and client.get("/api/viz/compare?b=nope").status_code == 404


def test_compare_runs_needs_two_runs(client):
    m = client.get("/cases/libraries/api/viz/compare").json()
    jsonschema.validate(m, COMPARE_RUNS)
    assert not m["ready"] and m["empty"]["row"] == "R11"
    assert "Only one run" in client.get("/cases/libraries/compare").text


def test_compare_runs_diff(make_client, two_runs):
    c = make_client()
    m = c.get("/cases/libraries/api/viz/compare").json()
    jsonschema.validate(m, COMPARE_RUNS)
    assert m["ready"] and (m["run_a"], m["run_b"]) == (R1, R2)
    assert m["n_removed"] == 1 and m["n_added"] == 0 and m["n_changed"] >= 1
    assert any(x["b"] == "CHANGED-IN-RUN-2" for x in m["changed"])
    steps = next(r for r in m["metrics"] if r["label"] == "Steps")
    assert steps["delta"] == 8 and steps["dir"] == "up"
    page = c.get("/cases/libraries/compare").text
    _page_ok(page)
    assert "CHANGED-IN-RUN-2" in page and "Removed in B · 1" in page and "+8" in page
    back = c.get(f"/cases/libraries/api/viz/compare?run_a={R2}&run_b={R1}").json()
    assert back["n_added"] == 1 and back["n_removed"] == 0
