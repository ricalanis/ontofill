"""R64b: each DoD criterion compiles to a query that measures its own metric. Live (sf-library-branches
run-09ed86537750) dod2 "share of listed values with a cited source" and dod3 "share of branch addresses
cross-checked against the secondary portal" compiled to copies of dod1's completeness query."""

from __future__ import annotations

from ontofill.contracts import validate_document
from ontofill.inference import RecordedDecisionClient, generated_by
from ontofill.phases.p2_ontology.phase import _draft_dod_queries, _validate_queries
from ontofill.refiner.export import _query_actual
from tests.test_r63_dod_threshold_repair import _approve_prd
from tests.test_r64_zero_count_guard import CROSS_CHECK_ONTOLOGY

PRD = {
    "definition_of_done": [
        {
            "id": "dod1",
            "metric": "share of branches complete",
            "operator": ">=",
            "target": 1,
            "min_ratio": 1.0,
        },
        {
            "id": "dod2",
            "metric": "share of listed values with a cited source",
            "operator": ">=",
            "target": 1,
            "min_ratio": 1.0,
        },
        {
            "id": "dod3",
            "metric": "share of addresses cross-checked",
            "operator": ">=",
            "target": 1,
            "min_ratio": 1.0,
        },
    ]
}
DOD1 = {
    "criterion_id": "dod1",
    "aggregate": "entities_meeting_completeness",
    "class_id": "branch",
    "class": "branch",
    "properties": "dod",
    "min_ratio": 1.0,
    "measure": "share",
    "target": 1,
    "operator": ">=",
}


def test_each_criterion_keeps_its_own_meaning(tmp_path) -> None:
    _approve_prd(tmp_path, PRD)
    draft = {
        "queries": [
            DOD1,
            {
                "criterion_id": "dod2",
                "aggregate": "count_values_without_evidence",
                "target": 1,
                "operator": ">=",
            },
            {
                "criterion_id": "dod3",
                "aggregate": "count_entities_with_relation",
                "class_id": "branch",
                "relation_id": "address_cross_check",
                "min_ratio": 1.0,
                "target": 1,
                "operator": ">=",
            },
        ]
    }
    repairs: list[dict] = []
    decision = RecordedDecisionClient({"phase2.dod_queries": [draft]})
    compiled = _draft_dod_queries(
        PRD,
        CROSS_CHECK_ONTOLOGY,
        decision,
        case_dir=tmp_path,
        query_repairs=repairs,
    )
    q = {item["criterion_id"]: item for item in compiled["queries"]}
    assert (q["dod2"]["target"], q["dod2"]["operator"]) == (
        0,
        "<=",
    )  # all values cited == none without evidence
    assert q["dod3"]["measure"] == "share" and "min_ratio" not in q["dod3"]
    assert q["dod3"]["relation_id"] == "address_cross_check"
    by_id = {r["criterion_id"]: r for r in repairs}
    assert by_id["dod2"]["approved_values"]["compiled_as"] == "count_values_without_evidence <= 0"
    validate_document(
        "ontology-recommendations",
        {
            "schema_version": "1",
            "ontology_path": "02-ontology/ontology.json",
            "generated_by": generated_by(decision),
            "unresolved": [],
            "query_repairs": repairs,
        },
    )
    _validate_queries(PRD, CROSS_CHECK_ONTOLOGY, compiled)  # the equivalent zero count is accepted


def test_copies_of_another_criterion_are_set_aside(tmp_path) -> None:
    _approve_prd(tmp_path, PRD)
    copies = {"queries": [DOD1, {**DOD1, "criterion_id": "dod2"}, {**DOD1, "criterion_id": "dod3"}]}
    unresolved: list[dict] = []
    compiled = _draft_dod_queries(
        PRD,
        CROSS_CHECK_ONTOLOGY,
        RecordedDecisionClient({"phase2.dod_queries": [copies] * 3}),
        case_dir=tmp_path,
        unresolved_criteria=unresolved,
    )
    assert [item["criterion_id"] for item in compiled["queries"]] == ["dod1"]
    reasons = {item["criterion_id"]: item["reason"] for item in unresolved}
    assert set(reasons) == {"dod2", "dod3"}
    assert all("compiled to the same query as `dod1`" in r for r in reasons.values())


def test_export_measures_a_relation_share() -> None:
    entities = [
        {
            "id": "b1",
            "class": "branch",
            "properties": {},
            "links": [{"property": "address_cross_check", "target": "a1"}],
        },
        {"id": "b2", "class": "branch", "properties": {}, "links": []},
    ]
    query = {
        "criterion_id": "dod3",
        "aggregate": "count_entities_with_relation",
        "class_id": "branch",
        "relation_id": "address_cross_check",
        "measure": "share",
        "target": 1,
        "operator": ">=",
    }
    assert _query_actual(entities, query) == 0.5
    assert _query_actual(entities, {k: v for k, v in query.items() if k != "measure"}) == 1


def test_an_explicit_share_without_relation_is_not_redirected_to_the_cases_relation_count() -> None:
    """The export used to resolve a completeness share with no relation_id to the case's single relation-count query
    (here `held_by`), even when the share was explicit: SF's dod1 would again be measured only over cross-checked
    branches. Only a legacy query (no `measure`) falls back; an explicit share measures every primary entity."""
    import pytest

    from ontofill.refiner.export import _metrics
    from tests.test_refiner import RECORDED, RUN_ID, ontology

    model = ontology()
    queries = {
        "queries": [
            {
                "criterion_id": "linked_books",
                "aggregate": "count_entities_with_relation",
                "class_id": "Book",
                "relation_id": "held_by",
                "target": 1,
                "operator": ">=",
            },
            {
                "criterion_id": "complete_books",
                "aggregate": "entities_meeting_completeness",
                "class": "Book",
                "properties": "dod",
                "min_ratio": 0.8,
                "measure": "share",
                "target": 1,
                "operator": ">=",
            },
        ]
    }

    def book(index: int, *, complete: bool, linked: bool) -> dict:
        def field(present: bool) -> dict:
            return {
                "value": f"v{index}" if present else None,
                "status": "gold" if present else "missing",
                "evidence": [{"source_type": "catalog"}] if present else [],
            }

        values = {p["id"]: field(complete) for p in model["properties"] if p["domain"] == "Book"}
        values["book_id"] = field(True)
        links = [{"property": "held_by", "target": f"library-{index}"}] if linked else []
        return {"class": "Book", "properties": values, "classified_as": [], "links": links}

    books = [
        book(0, complete=True, linked=True),
        book(1, complete=False, linked=True),
        book(2, complete=True, linked=False),
        book(3, complete=True, linked=False),
    ]
    result = _metrics(
        run_id=RUN_ID,
        entities=books,
        ontology=model,
        dod_queries=queries,
        trace=[],
        taxonomy_levels={},
        jobs=None,
        generated_by=RECORDED,
        preview=False,
        decisions_by_backend=None,
    )
    assert result["dod"][1]["actual"] == pytest.approx(
        0.75
    )  # 3 of all 4 books, not 1 of the 2 linked ones
