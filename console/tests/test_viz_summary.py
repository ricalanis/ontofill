"""Summary · the morning page of a run: the question and the run, what it produced, completeness vs targets, failures
and containment, cost, key receipts and what comes next, with a plain-text copy and a JSON twin. Rendered for the rich
fixture (libraries), the empty one (parks), a recorded run built in tmp (live inference, runner events, a conflict kept,
a membership boolean, a multi-source value) and a run still in motion ("so far")."""

import html
import json
import re
import shutil
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit

import jsonschema
from conftest import spec_for
from fastapi.testclient import TestClient

from ontofill_console.web import create_app, settings_from_env

RUN = "run-libraries-0001"
LAKE = ("libraries", "lake")
LAKE_CASE = "fixture-libraries"

NULLABLE_STR = {"type": ["string", "null"]}
NUM = {"type": ["number", "null"]}
STRIP = {
    "type": "object",
    "required": ["state", "title", "detail", "href"],
    "properties": {
        "state": {"enum": ["need", "run", "pause", "block", "quar", "done", "none"]},
        "title": {"type": "string"},
        "href": NULLABLE_STR,
    },
}
RUN_SCHEMA = {
    "type": "object",
    "required": [
        "run_id",
        "state",
        "phase",
        "checkpoint",
        "reason",
        "started_at",
        "ended_at",
        "duration_s",
        "duration",
        "n_steps",
        "has_feed",
        "stop",
        "pauses",
        "resumes",
        "pause_basis",
        "n_decisions",
        "runner_events",
        "href",
    ],
    "properties": {
        "run_id": {"type": "string"},
        "stop": {"type": "array", "items": {"type": "string"}},
        "pauses": {"type": "integer", "minimum": 0},
        "resumes": {"type": "integer", "minimum": 0},
        "n_steps": {"type": "integer"},
        "duration_s": NUM,
    },
}
CRITERION = {
    "type": "object",
    "required": ["criterion_id", "label", "query", "actual", "target", "met", "mock", "v", "t", "state"],
    "properties": {
        "met": {"type": ["boolean", "null"]},
        "mock": {"type": "boolean"},
        "v": {"type": "number", "minimum": 0, "maximum": 100},
        "state": {"enum": ["done", "run", "pause"]},
    },
}
PROP = {
    "type": "object",
    "required": ["id", "label", "ratio", "pct", "missing", "v"],
    "properties": {"ratio": {"type": "number", "minimum": 0, "maximum": 1}, "pct": {"type": "string"}},
}
RECEIPT = {
    "type": "object",
    "required": [
        "value_id",
        "entity_id",
        "entity_title",
        "entity_href",
        "prop",
        "prop_label",
        "value",
        "status",
        "state",
        "why",
        "n_sources",
        "sources",
        "evidence",
        "lineage_href",
    ],
    "properties": {
        "lineage_href": {"type": "string", "pattern": "^/cases/[^/]+/lineage/"},
        "state": {"enum": ["gold", "weak", "conflict", "missing"]},
        "evidence": {
            "oneOf": [
                {"type": "null"},
                {"type": "object", "required": ["host", "url", "captured_at", "source_id", "screenshot_href"]},
            ]
        },
    },
}
SUMMARY = {
    "type": "object",
    "required": [
        "case_id",
        "case_title",
        "question",
        "run_id",
        "runs",
        "gold_run_ids",
        "live",
        "so_far",
        "backend",
        "mock",
        "primary_class",
        "run",
        "produced",
        "completeness",
        "failures",
        "cost",
        "receipts",
        "next",
        "plain",
        "sources",
        "json_href",
        "empty",
        "receipts_empty",
        "next_empty",
    ],
    "properties": {
        "run_id": NULLABLE_STR,
        "so_far": {"type": "boolean"},
        "mock": {"type": "boolean"},
        "live": {"type": "object", "required": ["on", "updated_at", "poll_ms"]},
        "run": {"oneOf": [{"type": "null"}, RUN_SCHEMA]},
        "produced": {
            "type": "object",
            "required": [
                "basis",
                "entities_by_class",
                "n_entities",
                "n_values",
                "sources",
                "n_sources",
                "source_classes",
                "n_source_classes",
                "n_sources_touched",
                "n_pages",
                "n_screenshots",
                "n_cells",
                "n_steps",
                "pages_href",
                "entities_href",
            ],
            "properties": {
                k: {"type": "integer", "minimum": 0}
                for k in ("n_entities", "n_values", "n_sources", "n_source_classes", "n_pages", "n_cells", "n_steps")
            },
        },
        "completeness": {
            "type": "object",
            "required": [
                "basis",
                "mock",
                "criteria",
                "n_met",
                "props",
                "primary_label",
                "n_primary",
                "meeting",
                "threshold",
                "href",
                "empty",
            ],
            "properties": {
                "criteria": {"type": "array", "items": CRITERION},
                "props": {"type": "array", "items": PROP},
            },
        },
        "failures": {
            "type": "object",
            "required": [
                "n",
                "reasons",
                "n_reasons",
                "dropped",
                "contained",
                "n_contained",
                "jobs",
                "jobs_all_pass",
                "href",
                "empty",
            ],
            "properties": {
                "reasons": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {"type": "object", "required": ["kind", "label", "state", "n", "example", "contained"]},
                },
                "contained": {"type": "object", "required": ["quarantines", "limit_kills", "blocked_domains", "gates"]},
            },
        },
        "cost": {
            "type": "object",
            "required": [
                "usd_total",
                "usd_text",
                "basis",
                "has_cost",
                "by_phase",
                "by_model",
                "n_values",
                "per_value",
                "per_value_text",
                "per_entity",
                "per_entity_text",
                "budget_usd",
                "burn",
                "inference",
                "href",
                "empty",
            ],
            "properties": {
                "inference": {
                    "type": "object",
                    "required": ["mounted", "n_calls", "pct_vultr", "unattributed", "n_model_steps", "text", "href"],
                },
                "by_phase": {
                    "type": "array",
                    "items": {"type": "object", "required": ["name", "usd", "usd_text", "steps"]},
                },
            },
        },
        "receipts": {"type": "array", "maxItems": 6, "items": RECEIPT},
        "next": {"type": "array", "items": STRIP},
        "plain": {"type": "string"},
        "empty": {"oneOf": [{"type": "null"}, {"type": "object", "required": ["what", "source", "row"]}]},
    },
}


# helpers ----------------------------------------------------------------------------------------------------------
def page_text(body: str) -> str:
    main = body.split('<main id="main">', 1)[1]
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", main)))


def _jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def build_recorded_run(
    cases_dir,
    rid: str = "run-libraries-0002",
    backend: str = "vultr",
    state: str = "done",
    updated_at: str | None = None,
) -> str:
    """Copy the fixture run to a new run id (live feed + gold export) with live inference, a reason it stopped, a
    conflict kept, a membership boolean and a value backed by two sources."""
    lake = cases_dir.joinpath(*LAKE)
    for layer in ("runs", "gold"):
        src, dst = lake / layer / LAKE_CASE / RUN, lake / layer / LAKE_CASE / rid
        shutil.copytree(src, dst)
        for f in dst.iterdir():
            f.write_text(f.read_text().replace(RUN, rid).replace('"recorded"', f'"{backend}"'))
    status_path = lake / "runs" / LAKE_CASE / rid / "status.json"
    status = json.loads(status_path.read_text())
    status.pop("checkpoint_pending", None)
    status.update(state=state, reason="definition of done met; gap loop stopped" if state == "done" else None)
    if updated_at:
        status["updated_at"] = updated_at
    status_path.write_text(json.dumps(status))
    ents_path = lake / "gold" / LAKE_CASE / rid / "entities.jsonl"
    ents = _jsonl(ents_path)
    libs = [e for e in ents if e["class"] == "library"]
    hours = libs[0]["properties"]["opening_hours"]
    ev = hours["evidence"][0]
    hours.update(
        status="conflict",
        evidence=[ev, {**ev, "source_id": "registry-example", "url": "https://bibliotecas-registro.example/x"}],
    )
    name = libs[2]["properties"]["name"]
    name["evidence"] = [
        *name["evidence"],
        {
            **name["evidence"][0],
            "source_id": "website-example",
            "source_type": "library_website",
            "url": "https://web.example/lib-3",
        },
    ]
    libs[1]["properties"]["in_network"] = {
        "value_id": "val:lib-002-in_network",
        "value": True,
        "confidence": 0.9,
        "status": "gold",
        "evidence": [dict(libs[1]["properties"]["name"]["evidence"][0])],
    }
    _write_jsonl(ents_path, ents)
    return rid


def runner_env(tmp_path, rid: str) -> dict:
    root = tmp_path / "runner"
    root.mkdir(exist_ok=True)
    events = [
        {"ts": "2026-09-26T18:05:00+00:00", "case_id": "libraries", "run_id": rid, "kind": "started"},
        {
            "ts": "2026-09-26T18:06:00+00:00",
            "case_id": "libraries",
            "run_id": rid,
            "kind": "paused_at_checkpoint",
            "detail": "prd",
        },
        {"ts": "2026-09-26T18:20:00+00:00", "case_id": "libraries", "run_id": rid, "kind": "resumed"},
        {
            "ts": "2026-09-26T18:21:00+00:00",
            "case_id": "libraries",
            "run_id": rid,
            "kind": "paused_at_checkpoint",
            "detail": "ontology",
        },
        {"ts": "2026-09-26T18:25:00+00:00", "case_id": "libraries", "run_id": rid, "kind": "resumed"},
        {
            "ts": "2026-09-26T18:40:00+00:00",
            "case_id": "libraries",
            "run_id": rid,
            "kind": "done",
            "detail": "all criteria met",
        },
        {"ts": "2026-09-26T18:41:00+00:00", "case_id": "parks", "run_id": rid, "kind": "failed"},
    ]
    _write_jsonl(root / "events.jsonl", events)
    return {"ONTOFILL_RUNNER_STATE": str(root)}


def client_for(cases_dir, extra: dict | None = None) -> TestClient:
    env = {"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir), "ONTOFILL_CONSOLE_IDENTITY": "sso", **(extra or {})}
    return TestClient(create_app(settings_from_env(env)))


# tests ------------------------------------------------------------------------------------------------------------
def test_summary_json_schema_both_cases(client):
    for cid in ("libraries", "parks"):
        r = client.get(f"/cases/{cid}/api/viz/summary")
        assert r.status_code == 200, cid
        jsonschema.validate(r.json(), SUMMARY)
    m = client.get("/cases/libraries/api/viz/summary").json()
    assert m["run_id"] == RUN and m["mock"] is True and m["backend"] == "recorded"
    assert m["run"]["n_steps"] > 0 and m["run"]["started_at"] <= m["run"]["ended_at"]
    assert m["run"]["checkpoint"] == "action" and any("Action checkpoint" in s for s in m["run"]["stop"])
    assert m["produced"]["n_entities"] == sum(c["n"] for c in m["produced"]["entities_by_class"]) > 0
    assert m["produced"]["n_values"] > 0 and m["produced"]["n_pages"] > 0 and m["produced"]["n_cells"] >= 1
    assert all(c["mock"] and c["met"] is False for c in m["completeness"]["criteria"])  # recorded never counts
    assert m["failures"]["n_contained"] >= 2 and m["failures"]["contained"]["quarantines"] >= 1
    assert all(
        x["example"] and x["example"]["href"].startswith("/cases/libraries/runs/") for x in m["failures"]["reasons"]
    )
    assert m["cost"]["has_cost"] and m["cost"]["usd_total"] > 0
    assert m["cost"]["inference"]["mounted"] is False and "gateway log not mounted" in m["cost"]["inference"]["text"]
    assert 3 <= len(m["receipts"]) <= 6
    assert any(x["title"].startswith("Recorded inference") for x in m["next"])
    assert any("Uncovered" in x["title"] for x in m["next"])


def test_numbers_in_html_match_json(client):
    m = client.get("/cases/libraries/api/viz/summary").json()
    body = client.get("/cases/libraries/summary").text
    assert "built-in method" not in body and "Undefined" not in body
    text = page_text(body)
    p, c, f, k, r = m["produced"], m["completeness"], m["failures"], m["cost"], m["run"]
    for needle in (
        m["question"].split("\n")[0],
        r["run_id"],
        r["duration"],
        f"{r['n_steps']} steps",
        r["started"],
        f"entities {p['n_entities']}",
        f"gold values {p['n_values']}",
        f"sources used {p['n_sources']}",
        f"pages captured {p['n_pages']}",
        f"cells run {p['n_cells']}",
        f"{c['n_met']} of {len(c['criteria'])} met",
        f"quarantined {f['contained']['quarantines']}",
        f"limit kills {f['contained']['limit_kills']}",
        f"total {k['usd_text']}",
        f"per gold value {k['per_value_text']}",
        f"per entity meeting the DoD {k['per_entity_text']}",
        "gateway log not mounted",
        "does not count",
    ):
        assert needle in text, needle
    for x in c["criteria"]:
        assert f"{x['actual']} / {x['target']}" in text, x
    for x in c["props"]:
        assert f"{x['label']} {x['pct']}" in text, x
    for x in f["reasons"]:
        assert f"{x['label']} · {x['n']}" in text, x
    for x in k["by_phase"]:
        assert f"{x['name']} {x['usd_text']} {x['steps']}" in text, x
    for x in m["receipts"]:
        assert x["value"] in text and x["lineage_href"] in html.unescape(body)
    for x in m["next"]:
        assert x["title"] in text
    # the plain-text copy is on the page, inside a <details>, and carries the same numbers
    pre = re.search(r'<details class="sum-copy".*?<pre[^>]*>(.*?)</pre>', body, re.S)
    assert pre and html.unescape(pre.group(1)) == m["plain"]
    for needle in (f"{p['n_values']} gold values", k["usd_text"], r["run_id"]):
        assert needle in m["plain"]


def test_summary_is_first_case_view_after_runs(client):
    body = client.get("/cases/libraries/summary").text
    nav = re.search(r'<nav class="subnav".*?</nav>', body, re.S).group(0)
    links = re.findall(r'href="([^"]+)"', nav)
    assert links[:3] == ["/cases/libraries", "/cases/libraries/runs", "/cases/libraries/summary"]
    assert 'href="/cases/libraries/summary" aria-current="page"' in nav


def test_receipts_link_to_real_values(client, cases_dir):
    m = client.get("/cases/libraries/api/viz/summary").json()
    gold = cases_dir.joinpath(*LAKE, "gold", LAKE_CASE, RUN, "entities.jsonl")
    value_ids = {f["value_id"] for e in _jsonl(gold) for f in e["properties"].values() if isinstance(f, dict)}
    for x in m["receipts"]:
        assert x["value_id"] in value_ids
        path = urlsplit(x["lineage_href"])
        lin = client.get(path.path.replace("/lineage/", "/api/viz/lineage/", 1) + "?" + path.query).json()
        assert unquote(path.path.rsplit("/lineage/", 1)[1]) == x["value_id"]
        assert json.dumps(lin).count(x["value_id"]) >= 1
        assert client.get(x["lineage_href"]).status_code == 200
        assert client.get(x["entity_href"].split("#")[0]).status_code == 200
        shot = x["evidence"]["screenshot_href"]
        if shot:
            img = client.get(shot)
            assert img.status_code == 200 and img.headers["content-type"].startswith("image/")
    # deterministic: the same page twice picks the same values
    again = client.get("/cases/libraries/api/viz/summary").json()
    assert [x["value_id"] for x in again["receipts"]] == [x["value_id"] for x in m["receipts"]]
    # DoD properties first, one per property
    props = [x["prop"] for x in m["receipts"] if x["why"].startswith("DoD")]
    assert len(props) == len(set(props)) >= 3


def test_parks_honest_empty_states(client):
    r = client.get("/cases/parks/summary")
    assert r.status_code == 200
    body = r.text
    assert "built-in method" not in body and "Undefined" not in body
    m = client.get("/cases/parks/api/viz/summary").json()
    assert m["run_id"] is None and m["run"] is None and m["empty"] is not None and m["receipts"] == []
    assert m["question"] and m["question"] in page_text(body)
    text = page_text(body)
    assert "No run yet" in text and "The engine has not published a run" in text
    assert any("PRD" in x["title"] for x in m["next"])  # the pending PRD is the next thing
    assert "Run: none yet" in m["plain"]


def test_unknown_case_and_run(client):
    assert client.get("/cases/nope/summary").status_code == 404
    assert client.get("/cases/nope/api/viz/summary").status_code == 404
    assert client.get("/cases/libraries/summary?run=run-does-not-exist").status_code == 404
    assert client.get("/cases/libraries/api/viz/summary?run=run-does-not-exist").status_code == 404
    assert client.get("/cases/parks/summary?run=anything").status_code == 404


def test_recorded_run_built_in_tmp(cases_dir, tmp_path):
    rid = build_recorded_run(cases_dir)
    c = client_for(cases_dir, runner_env(tmp_path, rid))
    r = c.get(f"/cases/libraries/api/viz/summary?run={rid}")
    assert r.status_code == 200
    m = r.json()
    jsonschema.validate(m, SUMMARY)
    assert m["run_id"] == rid and m["backend"] == "vultr" and m["mock"] is False and m["so_far"] is False
    run = m["run"]
    assert run["state"] == "done" and run["checkpoint"] is None
    assert run["pauses"] == 2 and run["resumes"] == 2 and run["pause_basis"] == "runner events.jsonl"
    assert any("definition of done met" in s for s in run["stop"])
    assert any(s.startswith("runner: done") and "all criteria met" in s for s in run["stop"])
    assert all(e["kind"] != "failed" for e in run["runner_events"])  # another case's event stays out
    crit = m["completeness"]["criteria"]
    assert (
        crit
        and all(x["mock"] is False for x in crit)
        and m["completeness"]["n_met"] == sum(1 for x in crit if x["met"])
    )
    # receipts: the two-source value first, one conflict kept, one membership boolean
    rec = m["receipts"]
    assert 3 <= len(rec) <= 6
    assert rec[0]["n_sources"] == 2 and "2 sources" in rec[0]["why"]
    kept = [x for x in rec if x["why"].startswith("conflict kept")]
    assert len(kept) == 1 and kept[0]["state"] == "conflict" and kept[0]["prop"] == "opening_hours"
    member = [x for x in rec if x["why"].startswith("membership")]
    assert len(member) == 1 and member[0]["value"] == "yes" and member[0]["value_id"] == "val:lib-002-in_network"
    body = c.get(f"/cases/libraries/summary?run={rid}").text
    text = page_text(body)
    assert "does not count" not in text and "banner--sim" not in body
    assert "Waited for a person 2× , resumed 2×" in text and "Runner events · " in text
    assert "conflict kept" in text and "membership" in text
    # the run picker lists both runs; the default (latest) page is still the fixture run
    assert f'href="?run={rid}"' in body
    assert c.get("/cases/libraries/api/viz/summary").json()["run_id"] == RUN
    for x in rec:
        assert c.get(x["lineage_href"]).status_code == 200


def test_live_run_numbers_are_labelled_so_far(cases_dir):
    rid = build_recorded_run(
        cases_dir, "run-libraries-0003", state="running", updated_at=datetime.now(UTC).isoformat(timespec="seconds")
    )
    c = client_for(cases_dir)
    m = c.get(f"/cases/libraries/api/viz/summary?run={rid}").json()
    assert m["so_far"] is True and m["live"]["on"] is True and m["run"]["state"] == "running"
    assert any(x["title"].startswith("Run in motion") for x in m["next"])
    assert "(so far)" in m["plain"]
    body = c.get(f"/cases/libraries/summary?run={rid}").text
    assert 'data-live="1"' in body and "data-live-region=" in body
    text = page_text(body)
    assert "What it produced so far" in text and "Cost so far" in text


def test_partial_metrics_without_gold(cases_dir):
    """A live run whose gold export is not written yet: completeness comes from status.json metrics, labelled."""
    rid = build_recorded_run(cases_dir, "run-libraries-0004", state="paused")
    shutil.rmtree(cases_dir.joinpath(*LAKE, "gold", LAKE_CASE, rid))
    c = client_for(cases_dir)
    m = c.get(f"/cases/libraries/api/viz/summary?run={rid}").json()
    jsonschema.validate(m, SUMMARY)
    assert m["completeness"]["basis"].startswith("status.json metrics")
    assert m["completeness"]["criteria"] and m["completeness"]["props"]
    assert m["receipts"] == [] and m["receipts_empty"]["row"] == "R2"
    assert m["produced"]["n_values"] > 0  # value_ids emitted in the trace
    assert c.get(f"/cases/libraries/summary?run={rid}").status_code == 200


def test_inference_line_for_a_run_before_gateway_logging():
    from ontofill_console.viz.summary import _inference_text

    im = {"n_calls": 0, "n_model_steps": 5, "pct_vultr": None, "unattributed": {"n": 12}}
    text = _inference_text(im)
    assert "no reasoning calls" not in text and "5 model steps" in text and "12 unattributed" in text
    im = {"n_calls": 8, "n_model_steps": 6, "pct_vultr": 100.0, "unattributed": {"n": 0}}
    assert _inference_text(im) == "8 gateway calls · 100% of reasoning on Vultr · 0 unattributed"
