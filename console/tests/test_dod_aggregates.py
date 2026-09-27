"""Every DoD aggregate the engine can compile is evaluated here, and one this console does not know never fails a page:
live, the real case's dod1 used count_entities_with_relation and /watch, /output and the summary returned 500."""

import json

from ontofill_console import dod
from ontofill_console.domain import Domain


def test_count_entities_with_relation_matches_the_engine():
    entities = [
        {"class": "library", "properties": {}, "links": [{"property": "same_operator", "target": "x"}]},
        {"class": "library", "properties": {}, "links": [{"property": "operated_by", "target": "y"}]},
        {"class": "library", "properties": {}},
        {"class": "operator", "properties": {}, "links": [{"property": "same_operator", "target": "z"}]},
    ]
    q = {
        "criterion_id": "linked",
        "aggregate": "count_entities_with_relation",
        "relation_id": "same_operator",
        "class_id": "library",
        "target": 1,
        "operator": ">=",
    }
    assert dod.evaluate_query(q, entities, Domain("library")) == 1


def test_pages_survive_new_and_unknown_aggregates(client, cases_dir):
    path = cases_dir / "libraries" / "case" / "02-ontology" / "dod-queries.json"
    doc = json.loads(path.read_text())
    doc["queries"] += [
        {
            "criterion_id": "linked",
            "aggregate": "count_entities_with_relation",
            "relation_id": "same_operator",
            "class_id": "library",
            "target": 1,
            "operator": ">=",
        },
        {"criterion_id": "future", "aggregate": "count_something_new", "target": 1, "operator": ">="},
    ]
    path.write_text(json.dumps(doc))
    for url in (
        "/cases/libraries/output",
        "/api/watch",
        "/watch",
        "/cases/libraries/summary",
        "/cases/libraries/api/viz/output",
        "/cases/libraries/api/viz/summary",
    ):
        r = client.get(url)
        assert r.status_code == 200, url
        assert "Traceback" not in r.text
    rows = dod.criteria({}, {}, Domain("library"), queries=doc["queries"], entities=[])
    future = next(r for r in rows if r["criterion_id"] == "future")
    assert future["actual"] is None and future["note"].startswith("not computed here")
    assert next(r for r in rows if r["criterion_id"] == "linked")["actual"] == 0
