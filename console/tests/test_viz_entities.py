"""Q5 · Entities and lineage: a generic browser over gold entities, an entity page with every value's evidence one
click away, and the lineage journal value → evidence → step → plan → objective → ontology → PRD → brief."""

import json
from urllib.parse import quote

import jsonschema

RUN = "run-libraries-0001"

LIST = {
    "type": "object",
    "required": ["case_id", "run_id", "classes", "rows", "n_rows", "n_total", "capped", "filters", "empty"],
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["entity_id", "class", "class_label", "title", "identifier", "ratio", "href"],
                "properties": {
                    "ratio": {"type": ["number", "null"]},
                    "href": {"type": "string", "pattern": "^/cases/"},
                },
            },
        },
        "classes": {"type": "array", "items": {"type": "object", "required": ["id", "label", "n", "primary"]}},
    },
}
EVIDENCE = {
    "type": "object",
    "required": [
        "url",
        "host",
        "path",
        "selector",
        "captured_at",
        "source_id",
        "source_type",
        "screenshot_href",
        "raw_href",
    ],
}
ENTITY = {
    "type": "object",
    "required": ["entity_id", "class", "class_label", "title", "props", "links", "flags", "ratio", "run_id"],
    "properties": {
        "props": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "id",
                    "label",
                    "state",
                    "status",
                    "value",
                    "confidence",
                    "value_id",
                    "evidence",
                    "step_ids",
                    "lineage_href",
                ],
                "properties": {"evidence": {"type": "array", "items": EVIDENCE}},
            },
        }
    },
}
STEP = {"type": "object", "required": ["step_id", "phase", "mode", "observed", "requested", "executed", "evaluated"]}
LINEAGE = {
    "type": "object",
    "required": [
        "value",
        "evidence",
        "chains",
        "step_ids",
        "plans",
        "objectives",
        "ontology",
        "dod_queries",
        "prd",
        "brief",
    ],
    "properties": {
        "chains": {"type": "array", "items": {"type": "array", "items": STEP}},
        "value": {"type": "object", "required": ["value_id", "entity_id", "prop", "label", "value"]},
        "ontology": {"type": "object", "required": ["class", "property", "label", "dod"]},
        "prd": {"type": "object", "required": ["exists", "linked", "others"]},
    },
}


def test_list_json_shape_and_html(client):
    m = client.get("/cases/libraries/api/viz/entities").json()
    jsonschema.validate(m, LIST)
    assert m["n_total"] == 28 and m["n_rows"] == 28
    assert m["rows"][0]["class"] == "library"  # primary class first
    assert {c["id"] for c in m["classes"]} == {"library", "operator"}
    page = client.get("/cases/libraries/entities").text
    assert "built-in method" not in page and "Undefined" not in page
    assert f"{m['n_rows']} of {m['n_total']} entities" in page
    for r in m["rows"][:5]:
        assert r["title"] in page and r["href"].replace("&", "&amp;") in page


def test_list_filters(client):
    m = client.get("/cases/libraries/api/viz/entities?q=zz-lib-0024").json()
    assert [r["entity_id"] for r in m["rows"]] == ["lib:fixture-024"]
    m = client.get("/cases/libraries/api/viz/entities?cls=operator").json()
    assert m["n_rows"] == 4 and all(r["class"] == "operator" and r["ratio"] is None for r in m["rows"])
    m = client.get("/cases/libraries/api/viz/entities?completeness=partial").json()
    assert m["n_rows"] == 4 and all(r["ratio"] == 0.75 for r in m["rows"])
    m = client.get("/cases/libraries/api/viz/entities?completeness=complete&cls=library").json()
    assert m["n_rows"] == 20
    assert client.get("/cases/libraries/api/viz/entities?completeness=bogus").json()["n_rows"] == 28


def test_entity_page_every_value_with_evidence(client):
    eid = "lib:fixture-001"
    m = client.get(f"/cases/libraries/api/viz/entities/{quote(eid, safe='')}").json()
    jsonschema.validate(m, ENTITY)
    assert m["title"] == "Biblioteca Ejemplo 01" and m["identifier"]
    assert [p["id"] for p in m["props"]][:4] == ["name", "registry_code", "address", "opening_hours"]
    name = m["props"][0]
    assert name["state"] == "gold" and name["evidence"] and name["step_ids"]
    ev = name["evidence"][0]
    assert ev["host"] and ev["selector"] and ev["captured_at"] and ev["source_id"]
    assert client.get(ev["screenshot_href"]).status_code == 200 and client.get(ev["raw_href"]).status_code == 200
    page = client.get(f"/cases/libraries/entities/{quote(eid, safe='')}").text
    assert "built-in method" not in page and "Undefined" not in page
    assert ev["screenshot_href"] in page and ev["raw_href"] in page and ev["selector"] in page
    assert name["lineage_href"].replace("&", "&amp;") in page
    assert m["links"] and all(ln["label"] for ln in m["links"])
    for ln in m["links"]:
        assert ln["target_title"] in page
    assert f'id="p-{name["id"]}"' in page


def test_entity_missing_value_and_404s(client):
    m = client.get("/cases/libraries/api/viz/entities/lib:fixture-024").json()
    hours = next(p for p in m["props"] if p["id"] == "opening_hours")
    assert hours["state"] == "missing" and hours["evidence"] == []
    assert client.get("/cases/libraries/entities/lib:nope").status_code == 404
    assert client.get("/cases/libraries/api/viz/entities/lib:nope").status_code == 404
    assert client.get("/cases/libraries/entities/lib:fixture-001?run=no-such").status_code == 404
    assert client.get("/cases/nope/entities").status_code == 404
    assert client.get("/cases/parks/entities/lib:fixture-001").status_code == 404


def test_parks_honest_empty_state(client):
    r = client.get("/cases/parks/entities")
    assert r.status_code == 200 and "No entities yet" in r.text and "Undefined" not in r.text
    m = client.get("/cases/parks/api/viz/entities").json()
    jsonschema.validate(m, LIST)
    assert m["empty"] and m["rows"] == []
    assert client.get("/cases/parks/lineage/val:x").status_code == 404


def test_lineage_value_with_steps_shows_step_ids_and_the_brief(client, cases_dir):
    vid = "val:lib-001-name"
    m = client.get(f"/cases/libraries/api/viz/lineage/{quote(vid, safe='')}").json()
    jsonschema.validate(m, LINEAGE)
    assert m["step_ids"] == ["step:run-libraries-0001:00009"]
    chain = m["chains"][0]
    assert [s["phase"] for s in chain] == [5, 4, 3, 2, 1]  # the producing step, then its ancestors to Phase 1
    assert m["plans"][0]["path"] == "04-local/registry-example__profile/tdd.md" and m["plans"][0]["exists"]
    assert m["ontology"]["label"] == "Branch name" and m["ontology"]["dod"] is True
    assert m["objectives"] and m["objectives"][0]["id"] == "registry-example-profile"
    brief = (cases_dir / "libraries" / "case" / "brief.md").read_text().splitlines()[-1].strip()
    assert brief and brief in m["brief"]
    page = client.get(f"/cases/libraries/lineage/{quote(vid, safe='')}").text
    assert "built-in method" not in page and "Undefined" not in page
    for s in chain:
        assert s["step_id"] in page
    assert brief in page and m["plans"][0]["path"] in page and "Branch name" in page
    for word in ("observed", "requested", "executed", "evaluated"):
        assert word in page


def test_lineage_links_the_prd_criterion_that_uses_the_property(client):
    m = client.get("/cases/libraries/api/viz/lineage/val:lib-001-opening_hours").json()
    assert any(q["criterion_id"] == "with_hours" for q in m["dod_queries"])
    assert m["prd"]["exists"]
    m = client.get("/cases/libraries/api/viz/lineage/val:lib-001-website").json()
    assert m["ontology"]["dod"] is False and not m["dod_queries"]


def test_lineage_falls_back_to_the_live_trace(client, cases_dir):
    """A gold trace that does not name the value: the run's live trace does."""
    gold = cases_dir / "libraries" / "lake" / "gold" / "fixture-libraries" / RUN / "trace.jsonl"
    rows = [json.loads(line) for line in gold.read_text().splitlines() if line.strip()]
    for r in rows:
        r["value_ids"] = []
    gold.write_text("".join(json.dumps(r) + "\n" for r in rows))
    m = client.get("/cases/libraries/api/viz/lineage/val:lib-001-name").json()
    assert m["chains"] and m["steps_from"].startswith("live") and m["step_ids"]
    live = cases_dir / "libraries" / "lake" / "runs" / "fixture-libraries" / RUN / "trace.live.jsonl"
    steps = [json.loads(line) for line in live.read_text().splitlines() if line.strip()]
    for s in steps:
        s["value_ids"] = [v for v in s.get("value_ids") or [] if v != "val:lib-002-founded"]
    live.write_text("".join(json.dumps(s) + "\n" for s in steps))
    m = client.get("/cases/libraries/api/viz/lineage/val:lib-002-founded").json()
    assert m["chains"] == [] and m["empty_steps"]["row"] == "R2"
    assert client.get("/cases/libraries/lineage/val:lib-001-name").status_code == 200


def test_inferred_ontology_for_an_unknown_class(client, cases_dir):
    import shutil

    gd = cases_dir / "libraries" / "lake" / "gold" / "fixture-libraries"
    shutil.copytree(gd / RUN, gd / "run-libraries-0009")
    path = gd / "run-libraries-0009" / "entities.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    rows[0]["class"] = "mystery"
    rows[0]["properties"]["odd_prop"] = rows[0]["properties"]["kind"]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    eid = rows[0]["id"]
    m = client.get(f"/cases/libraries/api/viz/entities/{quote(eid, safe='')}?run=run-libraries-0009").json()
    assert m["class"] == "mystery" and m["inferred_ontology"] is True
    assert {"odd_prop", "kind"} <= {p["id"] for p in m["props"]}
    r = client.get(f"/cases/libraries/entities/{quote(eid, safe='')}?run=run-libraries-0009")
    assert r.status_code == 200 and "inferred ontology" in r.text
