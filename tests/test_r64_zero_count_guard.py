"""R64: a DoD criterion compiled so that it must stay at zero, as an entity count of the primary class or as distinct
source classes, is met only when the run finds nothing. Live (sf-library-branches run-09ed86537750): dod5 "branches
missing from the output <= 0" became `count_entities(library_branch) <= 0` and dod4 "values from non-public sources
<= 0" became `count_distinct_source_classes <= 0`. P2 now sets such a query aside as unresolved (131599b's salvage
path) instead of compiling a criterion that contradicts the others."""

from __future__ import annotations

from ontofill.inference import RecordedDecisionClient
from ontofill.phases.p2_ontology.phase import _counts_primary_to_zero, _draft_dod_queries
from tests.test_r63_dod_threshold_repair import _approve_prd

ONTOLOGY = {
    "version": "1",
    "primary_class": "branch",
    "classes": [{"id": "branch"}, {"id": "source_record"}],
    "properties": [{"id": "address", "domain": "branch", "dod": True}],
    "relations": [],
}


def _prd() -> dict:
    return {
        "definition_of_done": [
            {
                "id": "dod1",
                "metric": "share of branches complete",
                "operator": ">=",
                "target": 1,
                "min_ratio": 1.0,
            },
            {
                "id": "dod3",
                "metric": "values without a cited source",
                "operator": "<=",
                "target": 0,
            },
            {
                "id": "dod4",
                "metric": "values from non-public sources",
                "operator": "<=",
                "target": 0,
            },
            {
                "id": "dod5",
                "metric": "branches missing from the output",
                "operator": "<=",
                "target": 0,
            },
        ]
    }


def _draft() -> dict:
    return {
        "queries": [
            {
                "criterion_id": "dod1",
                "aggregate": "entities_meeting_completeness",
                "class_id": "branch",
                "class": "branch",
                "properties": "dod",
                "min_ratio": 1.0,
                "target": 1,
                "operator": ">=",
            },
            {
                "criterion_id": "dod3",
                "aggregate": "count_values_without_evidence",
                "target": 0,
                "operator": "<=",
            },
            {
                "criterion_id": "dod4",
                "aggregate": "count_distinct_source_classes",
                "target": 0,
                "operator": "<=",
            },
            {
                "criterion_id": "dod5",
                "aggregate": "count_entities",
                "class_id": "branch",
                "target": 0,
                "operator": "<=",
            },
        ]
    }


def test_zero_counts_of_the_primary_class_are_set_aside(tmp_path) -> None:
    prd = _prd()
    _approve_prd(tmp_path, prd)
    decision = RecordedDecisionClient({"phase2.dod_queries": [_draft() for _ in range(3)]})
    unresolved: list[dict] = []

    compiled = _draft_dod_queries(
        prd, ONTOLOGY, decision, case_dir=tmp_path, unresolved_criteria=unresolved
    )

    assert [q["criterion_id"] for q in compiled["queries"]] == ["dod1", "dod3"]
    reasons = {item["criterion_id"]: item["reason"] for item in unresolved}
    assert set(reasons) == {"dod4", "dod5"}
    assert "met only when there are no `branch` entities" in reasons["dod5"]
    assert "met only when no value carries evidence" in reasons["dod4"]
    assert all("excluded after three bounded retries" in r for r in reasons.values())


def test_the_guard_leaves_value_counts_other_classes_and_nonzero_targets_alone() -> None:
    crit = {"operator": "<=", "target": 0}
    assert not _counts_primary_to_zero(
        {"aggregate": "count_values_without_evidence"}, crit, ONTOLOGY
    )
    assert not _counts_primary_to_zero(
        {"aggregate": "count_entities", "class_id": "source_record"}, crit, ONTOLOGY
    )
    assert not _counts_primary_to_zero(
        {"aggregate": "count_entities", "class_id": "branch"},
        {"operator": ">=", "target": 50},
        ONTOLOGY,
    )
    assert _counts_primary_to_zero(
        {"aggregate": "count_entities_with_relation", "class_id": "branch"}, crit, ONTOLOGY
    )


CROSS_CHECK_ONTOLOGY = {
    "version": "1",
    "primary_class": "branch",
    "classes": [
        {"id": "branch", "identifier_property": "branch_id"},
        {"id": "address_record", "identifier_property": "address_id"},
    ],
    "properties": [
        {"id": "branch_id", "domain": "branch", "dod": False},
        {"id": "address", "domain": "branch", "dod": True},
        {"id": "address_id", "domain": "address_record", "dod": False},
        {"id": "street_address", "domain": "address_record", "dod": False},
    ],
    "relations": [
        {
            "id": "address_cross_check",
            "domain": "branch",
            "range": "address_record",
            "match": {
                "op": "same_value",
                "domain_property": "address",
                "range_property": "street_address",
            },
        },
    ],
}


def test_a_cross_check_relation_does_not_narrow_a_completeness_share(tmp_path) -> None:
    """Live (run-09ed86537750): dod1 'every branch complete' was compiled as a share of branches linked by
    address_cross_check (branch.address = address_record.street_address), which counts only branches whose address
    already matched a record. A relation joined on a measured value, not the class identifier, is a cross-check:
    the share is measured over every branch."""
    from ontofill.phases.p2_ontology.phase import _is_cross_check

    prd = {
        "definition_of_done": [
            {
                "id": "dod1",
                "metric": "share of branches complete",
                "operator": ">=",
                "target": 1,
                "min_ratio": 1.0,
            }
        ]
    }
    _approve_prd(tmp_path, prd)
    draft = {
        "queries": [
            {
                "criterion_id": "dod1",
                "aggregate": "entities_meeting_completeness",
                "class_id": "branch",
                "class": "branch",
                "properties": "dod",
                "min_ratio": 1.0,
                "measure": "share",
                "relation_id": "address_cross_check",
                "target": 1,
                "operator": ">=",
            }
        ]
    }
    repairs: list[dict] = []
    decision = RecordedDecisionClient({"phase2.dod_queries": [draft]})

    compiled = _draft_dod_queries(
        prd, CROSS_CHECK_ONTOLOGY, decision, case_dir=tmp_path, query_repairs=repairs
    )

    assert (
        "relation_id" not in compiled["queries"][0] and compiled["queries"][0]["measure"] == "share"
    )
    assert "relation_id" in repairs[0]["removed_fields"]
    assert _is_cross_check("address_cross_check", CROSS_CHECK_ONTOLOGY)
    membership = {
        **CROSS_CHECK_ONTOLOGY,
        "relations": [
            {
                "id": "awarded",
                "domain": "branch",
                "range": "address_record",
                "match": {
                    "op": "same_value",
                    "domain_property": "branch_id",
                    "range_property": "address_id",
                },
            }
        ],
    }
    assert not _is_cross_check(
        "awarded", membership
    )  # joined on the identifier: membership, keeps the linked share


def test_export_measures_an_unlinked_share_over_every_primary_entity() -> None:
    from ontofill.refiner.export import _query_actual

    gold = {"status": "gold", "evidence": [{"url": "https://a.example/"}]}
    entities = [
        {"id": "b1", "class": "branch", "properties": {"address": gold}, "links": []},
        {
            "id": "b2",
            "class": "branch",
            "properties": {"address": {"status": "missing"}},
            "links": [],
        },
    ]
    query = {
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
    assert _query_actual(entities, {**query, "_dod_properties": ["address"]}) == 0.5
