"""Q6 · Output: DoD progress, completeness heatmap, gold growth, sources × properties, conflicts, gap-loop decisions,
exports. HTML renders for both fixture cases, the JSON view-model has its documented shape, and edge cases (conflict,
Jev-only, a second run, an ontology that does not describe the entities) render honestly."""

import html
import json
import shutil
from urllib.parse import unquote, urlsplit

import jsonschema

LAKE_RUNS = ("libraries", "lake", "gold", "fixture-libraries")
RUN = "run-libraries-0001"

DOD_ROW = {
    "type": "object",
    "required": ["criterion_id", "query", "target", "label", "actual", "met", "mock", "v", "t", "state", "shown"],
    "properties": {
        "met": {"type": ["boolean", "null"]},
        "mock": {"type": "boolean"},
        "v": {"type": "number", "minimum": 0, "maximum": 100},
        "state": {"enum": ["done", "run", "pause"]},
    },
}
CELL = {
    "type": "object",
    "required": ["prop", "state", "href"],
    "properties": {
        "state": {"enum": ["gold", "weak", "conflict", "missing"]},
        "href": {"type": "string", "pattern": "^/cases/"},
    },
}
HEAT = {
    "type": "object",
    "required": ["columns", "rows", "n_rows", "capped", "threshold", "meeting", "class_label"],
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["entity_id", "title", "ratio", "cells", "href"],
                "properties": {"cells": {"type": "array", "items": CELL}, "ratio": {"type": "number"}},
            },
        },
        "columns": {
            "type": "array",
            "items": {"type": "object", "required": ["id", "label", "ratio", "gold", "missing"]},
        },
        "n_rows": {"type": "integer"},
    },
}
OUTPUT = {
    "type": "object",
    "required": [
        "case_id",
        "run_id",
        "gold_run_ids",
        "brief",
        "backend",
        "mock",
        "dod",
        "dod_met",
        "heat",
        "growth",
        "coverage",
        "conflicts",
        "conflicts_kept",
        "gap_decisions",
        "reopened",
        "exports",
        "empty",
        "empty_gap",
    ],
    "properties": {
        "dod": {"type": "array", "items": DOD_ROW},
        "heat": {"oneOf": [{"type": "null"}, HEAT]},
        "growth": {
            "type": "object",
            "required": ["runs", "n_runs", "chart"],
            "properties": {
                "runs": {
                    "type": "array",
                    "items": {"type": "object", "required": ["run_id", "entities_total", "meeting_dod"]},
                }
            },
        },
        "gap_decisions": {
            "type": "array",
            "items": {"type": "object", "required": ["step_id", "iteration", "reopen", "reopen_label", "state"]},
        },
        "exports": {"type": "array", "items": {"type": "object", "required": ["name", "bytes", "href"]}},
        "conflicts_kept": {"type": "integer", "minimum": 0},
        "empty": {"oneOf": [{"type": "null"}, {"type": "object", "required": ["what", "source", "row"]}]},
    },
}


def gold_dir(cases_dir):
    return cases_dir.joinpath(*LAKE_RUNS)


def entities(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_entities(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_output_json_shape_and_numbers_in_html(client):
    m = client.get("/cases/libraries/api/viz/output").json()
    jsonschema.validate(m, OUTPUT)
    assert m["run_id"] == RUN and m["empty"] is None and m["mock"] is True
    assert m["heat"]["n_rows"] == 24 and m["heat"]["meeting"] == 20
    assert [c["id"] for c in m["heat"]["columns"]] == ["name", "registry_code", "address", "opening_hours"]
    ratios = [r["ratio"] for r in m["heat"]["rows"]]
    assert ratios == sorted(ratios, reverse=True)
    hours = next(c for c in m["heat"]["columns"] if c["id"] == "opening_hours")
    assert hours["missing"] == 4 and abs(hours["ratio"] - 20 / 24) < 1e-3
    page = client.get("/cases/libraries/output").text
    assert "built-in method" not in page and "Undefined" not in page
    for r in m["dod"]:
        assert f"{r['shown']} / {r['target']}" in page and html.escape(r["query"], quote=False) in page
    assert f"all {m['heat']['n_rows']}" in page and "83%" in page
    assert page.count('class="cell--') >= 24 * 4
    assert m["brief"] in page and RUN in page
    for x in m["exports"]:
        assert x["href"] in page
    for s in m["coverage"]["rows"]:
        assert s["source_id"] in page


def test_mock_run_never_counts(client):
    m = client.get("/cases/libraries/api/viz/output").json()
    assert m["dod"] and all(r["mock"] and r["met"] is False for r in m["dod"]) and m["dod_met"] == 0
    assert all(r["engine_met"] for r in m["dod"])  # the engine said met; recorded inference does not count
    page = client.get("/cases/libraries/output").text
    assert "does not count" in page and "Simulated inference" in page


def test_heat_cells_link_to_that_values_evidence(client):
    m = client.get("/cases/libraries/api/viz/output").json()
    row = m["heat"]["rows"][0]
    cell = row["cells"][0]
    parts = urlsplit(cell["href"])
    assert parts.fragment == f"p-{cell['prop']}"
    assert unquote(parts.path).endswith(row["entity_id"])
    page = client.get(cell["href"])
    assert page.status_code == 200 and f'id="p-{cell["prop"]}"' in page.text


def test_gap_loop_decisions_from_the_trace(client):
    m = client.get("/cases/libraries/api/viz/output").json()
    reopen = [d for d in m["gap_decisions"] if d["reopen"] is not None]
    assert reopen and reopen[0]["reopen"] == 3 and reopen[0]["state"] == "need"
    assert m["reopened"] == [[3, 1]]
    page = client.get("/cases/libraries/output").text
    assert reopen[0]["reopen_label"].replace("·", "&middot;") in page or "reopen phase 3" in page


def test_growth_single_run_says_so(client):
    m = client.get("/cases/libraries/api/viz/output").json()
    g = m["growth"]
    assert g["n_runs"] == 1 and g["runs"][0] == {
        "run_id": RUN,
        "entities_total": 24,
        "meeting_dod": 20,
        "backend": "recorded",
    }
    assert "single point" in client.get("/cases/libraries/output").text


def test_parks_honest_empty_state(client):
    r = client.get("/cases/parks/output")
    assert r.status_code == 200 and "No gold export yet" in r.text and "Undefined" not in r.text
    m = client.get("/cases/parks/api/viz/output").json()
    jsonschema.validate(m, OUTPUT)
    assert m["empty"] and m["heat"] is None and m["dod"] == [] and m["exports"] == []


def test_unknown_case_and_run(client):
    assert client.get("/cases/nope/output").status_code == 404
    assert client.get("/cases/nope/api/viz/output").status_code == 404
    assert client.get("/cases/libraries/output?run=no-such-run").status_code == 404
    assert client.get("/cases/libraries/api/viz/output?run=no-such-run").status_code == 404
    assert client.get("/cases/parks/output?run=no-such-run").status_code == 404


def test_export_allowlist_streams_raw_files(client, cases_dir):
    base = f"/cases/libraries/api/viz/output/export/{RUN}"
    r = client.get(f"{base}/metrics.json")
    assert r.status_code == 200 and r.content == (gold_dir(cases_dir) / RUN / "metrics.json").read_bytes()
    assert "attachment" in r.headers["content-disposition"]
    r = client.get(f"{base}/entities.jsonl")
    assert r.status_code == 200 and len(r.text.splitlines()) == 28
    for bad in ("status.json", "..%2Flatest.json", "latest.json", "entities.jsonl.bak"):
        assert client.get(f"{base}/{bad}").status_code == 404, bad
    assert client.get("/cases/libraries/api/viz/output/export/no-run/metrics.json").status_code == 404
    assert client.get(f"/cases/parks/api/viz/output/export/{RUN}/metrics.json").status_code == 404


def test_conflict_weak_and_a_second_run(client, cases_dir):
    """A conflict kept, a Jev-only gold value (weak), and a second gold run for the growth chart."""
    gd = gold_dir(cases_dir)
    shutil.copytree(gd / RUN, gd / "run-libraries-0002")
    path = gd / "run-libraries-0002" / "entities.jsonl"
    rows = entities(path)
    lib = next(e for e in rows if e["id"] == "lib:fixture-001")
    lib["properties"]["address"]["status"] = "conflict"
    lib["properties"]["name"]["generated_by"] = {"backend": "jev", "model": "x", "at": "2026-09-26T18:00:00+00:00"}
    write_entities(path, rows)
    (gd / "run-libraries-0002" / "metrics.json").unlink()  # growth recomputes from entities
    m = client.get("/cases/libraries/api/viz/output?run=run-libraries-0002").json()
    jsonschema.validate(m, OUTPUT)
    row = next(r for r in m["heat"]["rows"] if r["entity_id"] == "lib:fixture-001")
    states = {c["prop"]: c["state"] for c in row["cells"]}
    assert states["address"] == "conflict" and states["name"] == "weak"
    assert m["conflicts_kept"] == 1
    assert [p["run_id"] for p in m["growth"]["runs"]] == [RUN, "run-libraries-0002"]
    assert m["growth"]["runs"][1]["meeting_dod"] == 19
    assert len(m["growth"]["chart"]["series"]) == 2 and m["growth"]["chart"]["series"][0]["path"].startswith("M")
    page = client.get("/cases/libraries/output?run=run-libraries-0002").text
    assert "cell--conflict" in page and "cell--weak" in page and 'class="series s-total"' in page
    assert "single point" not in page


def test_inferred_ontology_when_the_ontology_does_not_describe_the_entities(client, cases_dir):
    gd = gold_dir(cases_dir)
    shutil.copytree(gd / RUN, gd / "run-libraries-0003")
    path = gd / "run-libraries-0003" / "entities.jsonl"
    rows = entities(path)
    for e in rows:
        if e["class"] == "library":
            e["class"] = "branch"
    write_entities(path, rows)
    m = client.get("/cases/libraries/api/viz/output?run=run-libraries-0003").json()
    assert m["heat"]["n_rows"] == 24 and m["heat"]["dod_columns"] is False
    assert {c["id"] for c in m["heat"]["columns"]} >= {"name", "opening_hours"}
    page = client.get("/cases/libraries/output?run=run-libraries-0003")
    assert page.status_code == 200 and "marks none as DoD" in page.text


def test_row_cap_and_show_all(client, cases_dir, monkeypatch):
    from ontofill_console.viz import output

    monkeypatch.setattr(output, "ROW_CAP", 5)
    m = client.get("/cases/libraries/api/viz/output").json()
    assert m["heat"]["capped"] and len(m["heat"]["rows"]) == 5 and m["heat"]["n_rows"] == 24
    assert "Show all" in client.get("/cases/libraries/output").text
    m = client.get("/cases/libraries/api/viz/output?all=1").json()
    assert not m["heat"]["capped"] and len(m["heat"]["rows"]) == 24
