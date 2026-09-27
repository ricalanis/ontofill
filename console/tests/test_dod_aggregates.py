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


def test_relation_criterion_reads_as_words():
    d = Domain(
        "supplier",
        classes={"supplier": {"label": "Supplier", "label_plural": "Suppliers"}},
        relations={"supplier_awarded_contract": {"label": "awarded a contract"}},
    )
    engine_q = 'count_entities_with_relation(class_id="supplier", relation_id="supplier_awarded_contract")'
    assert dod.criterion_label(engine_q, "dod1", d) == "Suppliers with a “awarded a contract” link"
    ours = dod.query_text(
        {
            "aggregate": "count_entities_with_relation",
            "class_id": "supplier",
            "relation_id": "supplier_awarded_contract",
            "target": 50,
            "operator": ">=",
        }
    )
    assert dod.criterion_label(ours, "dod1", d) == "Suppliers with a “awarded a contract” link"


ONTO = {
    "primary_class": "branch",
    "classes": [{"id": "branch", "label": "Branch"}],
    "properties": [
        {"id": "address", "domain": "branch", "dod": True},
        {"id": "hours", "domain": "branch", "dod": True},
    ],
    "relations": [{"id": "held_by", "domain": "branch", "range": "branch"}],
}
GOLD = {"status": "gold", "value": "x", "evidence": [{"url": "https://a.example/"}]}


def _branch(complete: bool, linked: bool) -> dict:
    return {
        "class": "branch",
        "properties": {"address": GOLD, "hours": GOLD if complete else {"status": "missing"}},
        "links": [{"property": "held_by", "target": "b"}] if linked else [],
    }


BRANCHES = [_branch(True, True), _branch(False, True), _branch(True, False), _branch(True, False)]
RELATION_COUNT = {
    "criterion_id": "linked",
    "aggregate": "count_entities_with_relation",
    "class_id": "branch",
    "relation_id": "held_by",
    "target": 1,
    "operator": ">=",
}


def test_completeness_shares_match_the_engine_export():
    """As the engine's export: a fractional completeness target is a share of the linked entities (a legacy query
    resolves the case's single relation count), an explicit share with no relation measures every entity, and a
    relation count with measure=share is linked / all. Before, the console returned a count for all of these."""
    d = Domain.from_ontology(ONTO)
    legacy = {
        "criterion_id": "c",
        "aggregate": "entities_meeting_completeness",
        "class": "branch",
        "properties": "dod",
        "min_ratio": 1.0,
        "target": 0.8,
        "operator": ">=",
    }
    assert dod.evaluate_query(legacy, BRANCHES, d, [RELATION_COUNT, legacy]) == 0.5  # 1 of the 2 linked
    explicit = {**legacy, "measure": "share", "target": 1}
    assert dod.evaluate_query(explicit, BRANCHES, d, [RELATION_COUNT, explicit]) == 0.75  # 3 of all 4
    assert dod.evaluate_query({**explicit, "relation_id": "held_by"}, BRANCHES, d) == 0.5
    assert dod.evaluate_query({**explicit, "measure": "count", "target": 2}, BRANCHES, d) == 3
    assert dod.evaluate_query({**RELATION_COUNT, "measure": "share"}, BRANCHES, d) == 0.5
    assert dod.evaluate_query(RELATION_COUNT, BRANCHES, d) == 2
