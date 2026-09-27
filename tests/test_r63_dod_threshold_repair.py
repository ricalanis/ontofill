"""Regression coverage for digest-bound P2 DoD threshold repair and unresolved queries."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from ontofill.case.checkpoints import write_json
from ontofill.contracts import validate_document
from ontofill.inference import RecordedDecisionClient, generated_by
from ontofill.phases.p2_ontology.phase import (
    OntologyDraftUnavailable,
    _draft_dod_queries,
    _validate_queries,
)
from tests.approval_support import bind_approval


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
                "id": "dod2",
                "metric": "share of listed values cited",
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


def _ontology() -> dict:
    return {
        "version": "1",
        "primary_class": "branch",
        "classes": [{"id": "branch"}],
        "properties": [{"id": "address", "domain": "branch", "dod": True}],
        "relations": [],
    }


def _query(criterion_id: str, *, aggregate: str = "entities_meeting_completeness") -> dict:
    query = {
        "criterion_id": criterion_id,
        "aggregate": aggregate,
        "target": 0.25,
        "operator": "<",
    }
    if aggregate == "entities_meeting_completeness":
        query.update(class_id="branch", **{"class": "branch"}, properties="dod", min_ratio=0.4)
    else:
        query["class_id"] = "branch"
    return query


def _client(responses: list[dict]) -> RecordedDecisionClient:
    return RecordedDecisionClient({"phase2.dod_queries": responses})


def _approve_prd(tmp_path, prd: dict, *, decision: str = "approve") -> None:
    prd_path = tmp_path / "01-scope/prd.json"
    write_json(prd_path, prd)
    marker = {
        "approver": "Synthetic reviewer",
        "date": "2026-09-27",
        "checkpoint": "prd",
        "decision": decision,
    }
    if decision == "deny":
        marker["reason"] = "The synthetic reviewer denied this PRD."
    write_json(
        prd_path.parent / "APPROVED",
        bind_approval(tmp_path, ["01-scope/prd.json"], marker),
    )


def _draft(criterion_ids: list[str], *, unsupported: set[str] | None = None) -> dict:
    unsupported = unsupported or set()
    return {
        "queries": [
            _query(
                criterion_id,
                aggregate=(
                    "count_entities"
                    if criterion_id in unsupported
                    else "entities_meeting_completeness"
                ),
            )
            for criterion_id in criterion_ids
        ]
    }


def test_approved_sf_shaped_thresholds_are_repaired_and_receipted(tmp_path) -> None:
    # one criterion: three identical completeness queries for three criteria are copies (R64b), set aside
    prd = {"definition_of_done": _prd()["definition_of_done"][:1]}
    _approve_prd(tmp_path, prd)
    decision = _client([_draft(["dod1"])])
    repairs: list[dict] = []

    queries = _draft_dod_queries(
        prd, _ontology(), decision, case_dir=tmp_path, query_repairs=repairs
    )

    assert len(decision.calls) == 1
    assert [
        (item["criterion_id"], item["target"], item["operator"], item["min_ratio"])
        for item in queries["queries"]
    ] == [("dod1", 1, ">=", 1.0)]
    assert [item["criterion_id"] for item in repairs] == ["dod1"]
    assert all(item["fields"] == ["target", "operator", "min_ratio"] for item in repairs)
    assert all(item["approved_values"]["min_ratio"] == 1.0 for item in repairs)


def test_unsupported_sf_criteria_are_set_aside_and_cannot_pass_export(tmp_path) -> None:
    prd = _prd()
    _approve_prd(tmp_path, prd)
    decision = _client(
        [_draft(["dod1", "dod2", "dod3"], unsupported={"dod2", "dod3"}) for _ in range(3)]
    )
    repairs: list[dict] = []
    unresolved: list[dict] = []

    compiled = _draft_dod_queries(
        prd,
        _ontology(),
        decision,
        case_dir=tmp_path,
        query_repairs=repairs,
        unresolved_criteria=unresolved,
    )

    assert len(decision.calls) == 3
    assert [item["criterion_id"] for item in compiled["queries"]] == ["dod1"]
    assert {item["criterion_id"] for item in unresolved} == {"dod2", "dod3"}
    assert all("cannot be evaluated" in item["reason"] for item in unresolved)
    rejected = json.loads(
        (tmp_path / "02-ontology/recommendations/rejected-dod-queries.json").read_text()
    )
    assert rejected["status"] == "rejected"
    assert {item["criterion_id"] for item in rejected["queries"]} == {"dod1", "dod2", "dod3"}
    assert not (tmp_path / "02-ontology/dod-queries.json").exists()
    with pytest.raises(ValueError, match="cover each approved criterion exactly once"):
        _validate_queries(prd, _ontology(), compiled)

    recommendations = {
        "schema_version": "1",
        "ontology_path": "02-ontology/ontology.json",
        "generated_by": generated_by(decision),
        "unresolved": [],
        "repairs": [],
        "query_repairs": repairs,
        "unresolved_criteria": unresolved,
    }
    validate_document("ontology-recommendations", recommendations)


def test_denied_prd_thresholds_are_not_used_for_repair_and_rejection_is_saved(tmp_path) -> None:
    prd = _prd()
    _approve_prd(tmp_path, prd, decision="deny")
    invalid = _draft(["dod1", "dod2", "dod3"])
    decision = _client([deepcopy(invalid) for _ in range(3)])
    repairs: list[dict] = []

    with pytest.raises(OntologyDraftUnavailable, match="dod1.*0.25.*<.*1.*>="):
        _draft_dod_queries(prd, _ontology(), decision, case_dir=tmp_path, query_repairs=repairs)

    assert repairs == []
    rejected = json.loads(
        (tmp_path / "02-ontology/recommendations/rejected-dod-queries.json").read_text()
    )
    assert rejected["status"] == "rejected"
    assert "DoD criterion `dod1`" in rejected["reason"]
    assert "(0.25, '<')" in rejected["reason"]
    assert "(1, '>=')" in rejected["reason"]
