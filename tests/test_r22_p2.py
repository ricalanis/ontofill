"""R22 P2: validation failures repair their own model step before draft pause."""

from __future__ import annotations

import json

import pytest

from ontofill.inference import ModelValidationExhausted, RecordedDecisionClient
from ontofill.phases.p2_ontology.phase import (
    OntologyDraftUnavailable,
    draft_factors,
    draft_ontology,
)
from tests.approval_support import bind_approval

_RECORDED = {"backend": "recorded", "model": "synthetic", "at": "2026-09-26T00:00:00Z"}
_CROSS_CLASS_ERROR = "all rule predicate properties must belong to one class"


def _prd() -> dict:
    return {
        "definition_of_done": [
            {
                "id": "dod-records",
                "metric": "records",
                "target": 1,
                "operator": ">=",
                "basis": "proposed",
                "rationale": "A single record proves the workflow.",
                "feasibility": "One synthetic record fits the run budget.",
            }
        ],
        "jobs_to_be_done": [{"description": "Find useful public records."}],
        "requirements": [{"description": "Record public evidence."}],
    }


def _factors() -> dict:
    return {
        "factors": [
            {
                "id": "region",
                "label": "Region",
                "description": "How records vary by region",
                "kind": "conceptual",
                "evidence": [],
            }
        ]
    }


def _schema() -> dict:
    return {
        "primary_class": "party",
        "classes": [
            {
                "id": "party",
                "label": "Party",
                "label_plural": "Parties",
                "description": "A listed party",
                "title_property": "party_name",
                "identifier_property": "party_name",
                "aligned_to": None,
            },
            {
                "id": "address",
                "label": "Address",
                "label_plural": "Addresses",
                "description": "A party address",
                "title_property": "address_name",
                "identifier_property": "address_name",
                "aligned_to": None,
            },
        ],
        "properties": [
            {
                "id": "party_name",
                "label": "Party name",
                "domain": "party",
                "datatype": "string",
                "dod": True,
                "order": 0,
                "description": "The party's public name",
                "aligned_to": None,
            },
            {
                "id": "address_name",
                "label": "Address name",
                "domain": "address",
                "datatype": "string",
                "dod": False,
                "order": 1,
                "description": "The address label",
                "aligned_to": None,
            },
        ],
        "relations": [],
        "rules": [],
        "source_classes": [{"id": "directory", "label": "Public directory"}],
    }


def _cross_class_schema() -> dict:
    schema = _schema()
    schema["rules"] = [
        {
            "id": "same_label",
            "label": "Same label",
            "checks": ["Compare the names"],
            "verify": ["Inspect both names"],
            "predicate": {
                "all": [
                    {
                        "op": "same_value",
                        "left_property": "party_name",
                        "right_property": "address_name",
                    }
                ]
            },
        }
    ]
    return schema


def _taxonomy() -> dict:
    return {
        "taxonomies": [
            {
                "factor_id": "region",
                "root_label": "Region",
                "children": [{"id": "north", "label": "North", "level": 1}],
            }
        ]
    }


def _queries(criterion_id: str = "dod-records") -> dict:
    return {
        "queries": [
            {
                "criterion_id": criterion_id,
                "aggregate": "count_entities",
                "class_id": "party",
                "target": 1,
                "operator": ">=",
            }
        ]
    }


def _base_decisions(
    *,
    schemas: list[dict] | None = None,
    queries: list[dict] | None = None,
    factors: list[dict] | None = None,
) -> RecordedDecisionClient:
    return RecordedDecisionClient(
        {
            "phase2.taxonomies": [_taxonomy()],
            "critic.phase2.taxonomy_nodes": [
                {
                    "labels": [
                        {
                            "factor_id": "region",
                            "node_id": "north",
                            "critic_label": "Good-Exclusive",
                        }
                    ]
                }
            ],
            "phase2.schema": schemas or [_schema()],
            "phase2.dod_queries": queries or [_queries()],
            **({"phase2.factors": factors} if factors is not None else {}),
        }
    )


def _case(case_dir) -> None:
    (case_dir / "brief.md").write_text("Describe public parties and addresses.", encoding="utf-8")


def _validation_callback(calls: list[tuple[str, int, str]]):
    return lambda purpose, attempt, reason: calls.append((purpose, attempt, reason))


def test_cross_class_predicate_retries_schema_step_with_validator_error(tmp_path) -> None:
    _case(tmp_path)
    decision = _base_decisions(schemas=[_cross_class_schema(), _schema()])
    validation_calls: list[tuple[str, int, str]] = []

    ontology = draft_ontology(
        tmp_path,
        _prd(),
        _factors(),
        decision,
        on_validation_error=_validation_callback(validation_calls),
    )

    schema_calls = [
        (purpose, prompt) for purpose, prompt in decision.calls if purpose == "phase2.schema"
    ]
    assert len(schema_calls) == 2
    assert _CROSS_CLASS_ERROR in schema_calls[1][1]
    assert len(validation_calls) == 1
    assert validation_calls[0][:2] == ("phase2.schema", 1)
    assert "rule `same_label`" in validation_calls[0][2]
    assert _CROSS_CLASS_ERROR in validation_calls[0][2]
    assert "Allowed rule predicates" in validation_calls[0][2]
    assert ontology["rules"] == []


def test_three_invalid_core_schema_responses_pause_without_writing_artifacts(tmp_path) -> None:
    _case(tmp_path)
    invalid = _schema()
    invalid["classes"][0]["identifier_property"] = "missing_property"
    decision = _base_decisions(schemas=[invalid, invalid, invalid])
    validation_calls: list[tuple[str, int, str]] = []

    with pytest.raises(OntologyDraftUnavailable) as raised:
        draft_ontology(
            tmp_path,
            _prd(),
            _factors(),
            decision,
            on_validation_error=_validation_callback(validation_calls),
        )

    assert raised.value.purpose == "phase2.schema"
    assert raised.value.attempts == 3
    assert "identifier_property must refer to a property of its class" in raised.value.reason
    assert [attempt for _, attempt, _ in validation_calls] == [1, 2, 3]
    assert not (tmp_path / "02-ontology/ontology.json").exists()
    assert not (tmp_path / "02-ontology/dod-queries.json").exists()
    assert not (tmp_path / "02-ontology/recommendations/unresolved.json").exists()
    assert not (tmp_path / "02-ontology/APPROVED.stale").exists()


def test_inference_client_validation_exhaustion_becomes_p2_pause(tmp_path) -> None:
    class ExhaustedDecision:
        backend = "vultr"
        model = "synthetic"

        def complete_json(self, purpose, prompt, schema):
            raise ModelValidationExhausted(purpose, "'factors' is required", 3)

    validation_calls: list[tuple[str, int, str]] = []

    with pytest.raises(OntologyDraftUnavailable) as raised:
        draft_factors(
            tmp_path,
            _prd(),
            ExhaustedDecision(),
            on_validation_error=_validation_callback(validation_calls),
        )

    assert raised.value.purpose == "phase2.factors"
    assert raised.value.attempts == 3
    assert raised.value.reason == "'factors' is required"
    assert validation_calls == [("phase2.factors", 3, "'factors' is required")]
    assert not (tmp_path / "02-ontology/factors/factors.json").exists()


def test_taxonomy_factor_coverage_error_retries_taxonomy_model_step(tmp_path) -> None:
    _case(tmp_path)
    decision = _base_decisions(
        schemas=[_schema()],
    )
    taxonomy_responses = decision.responses["phase2.taxonomies"]
    taxonomy_responses.clear()
    taxonomy_responses.extend(
        [
            {
                "taxonomies": [
                    {
                        "factor_id": "wrong_factor",
                        "root_label": "Wrong",
                        "children": [{"id": "north", "label": "North", "level": 1}],
                    }
                ]
            },
            _taxonomy(),
        ]
    )
    validation_calls: list[tuple[str, int, str]] = []

    ontology = draft_ontology(
        tmp_path,
        _prd(),
        _factors(),
        decision,
        on_validation_error=_validation_callback(validation_calls),
    )

    prompts = [prompt for purpose, prompt in decision.calls if purpose == "phase2.taxonomies"]
    error = "taxonomies must cover exactly the approved factors"
    assert len(prompts) == 2
    assert error in prompts[1]
    assert validation_calls == [("phase2.taxonomies", 1, error)]
    assert ontology["taxonomies"][0]["factor_id"] == "region"


def test_taxonomy_critic_duplicate_labels_retry_with_feedback(tmp_path) -> None:
    _case(tmp_path)
    decision = _base_decisions()
    invalid_labels = {
        "labels": [
            {"factor_id": "region", "node_id": "north", "critic_label": "Bad"},
            {
                "factor_id": "region",
                "node_id": "north",
                "critic_label": "Good-Exclusive",
            },
        ]
    }
    critic_responses = decision.responses["critic.phase2.taxonomy_nodes"]
    valid_labels = critic_responses[0]
    critic_responses.clear()
    critic_responses.extend([invalid_labels, valid_labels])
    validation_calls: list[tuple[str, int, str]] = []

    draft_ontology(
        tmp_path,
        _prd(),
        _factors(),
        decision,
        on_validation_error=_validation_callback(validation_calls),
    )

    prompts = [
        prompt for purpose, prompt in decision.calls if purpose == "critic.phase2.taxonomy_nodes"
    ]
    error = "the taxonomy critic returned duplicate node labels"
    assert len(prompts) == 2
    assert error in prompts[1]
    assert validation_calls == [("critic.phase2.taxonomy_nodes", 1, error)]


def test_invalid_factor_schema_retries_and_feeds_error_into_prompt(tmp_path) -> None:
    invalid = {"factors": []}
    decision = RecordedDecisionClient({"phase2.factors": [invalid, _factors()]})
    validation_calls: list[tuple[str, int, str]] = []

    factors = draft_factors(
        tmp_path,
        _prd(),
        decision,
        on_validation_error=_validation_callback(validation_calls),
    )

    prompts = [prompt for purpose, prompt in decision.calls if purpose == "phase2.factors"]
    assert len(prompts) == 2
    assert "minItems" in validation_calls[0][2]
    assert validation_calls[0][2] in prompts[1]
    assert factors["factors"] == _factors()["factors"]


def test_exhausted_factor_validation_preserves_denial_and_prior_artifact(tmp_path) -> None:
    factor_path = tmp_path / "02-ontology/factors/factors.json"
    factor_path.parent.mkdir(parents=True)
    prior_artifact = {**_factors(), "revisions": [], "generated_by": _RECORDED}
    prior_bytes = (json.dumps(prior_artifact, indent=2) + "\n").encode()
    factor_path.write_bytes(prior_bytes)
    marker = bind_approval(
        tmp_path,
        ["02-ontology/factors/factors.json"],
        {
            "approver": "Test Reviewer",
            "date": "2026-09-26",
            "checkpoint": "factors",
            "decision": "deny",
            "reason": "Revise the proposed factors.",
        },
    )
    (factor_path.parent / "APPROVED").write_text(json.dumps(marker), encoding="utf-8")
    decision = RecordedDecisionClient({"phase2.factors": [{"factors": []}] * 3})

    with pytest.raises(OntologyDraftUnavailable, match="minItems"):
        draft_factors(tmp_path, _prd(), decision)

    assert factor_path.read_bytes() == prior_bytes
    assert (factor_path.parent / "APPROVED").exists()
    assert not (factor_path.parent / "revisions/1").exists()


def test_invalid_dod_query_retries_and_feeds_semantic_error_into_prompt(tmp_path) -> None:
    _case(tmp_path)
    decision = _base_decisions(queries=[_queries("wrong-criterion"), _queries()])
    validation_calls: list[tuple[str, int, str]] = []

    draft_ontology(
        tmp_path,
        _prd(),
        _factors(),
        decision,
        on_validation_error=_validation_callback(validation_calls),
    )

    prompts = [prompt for purpose, prompt in decision.calls if purpose == "phase2.dod_queries"]
    assert len(prompts) == 2
    assert "DoD queries must cover each approved criterion exactly once" in prompts[1]
    assert validation_calls == [
        (
            "phase2.dod_queries",
            1,
            "DoD queries must cover each approved criterion exactly once",
        )
    ]


def test_completeness_query_without_approved_min_ratio_retries_then_corrects(tmp_path) -> None:
    _case(tmp_path)
    invalid = _queries()
    invalid["queries"][0].update(
        aggregate="entities_meeting_completeness",
        **{"class": "party", "properties": "dod", "min_ratio": 0.8},
    )
    decision = _base_decisions(queries=[invalid, _queries()])

    draft_ontology(tmp_path, _prd(), _factors(), decision)

    prompts = [prompt for purpose, prompt in decision.calls if purpose == "phase2.dod_queries"]
    assert len(prompts) == 2
    assert "completeness query requires an approved PRD min_ratio" in prompts[1]
    saved = json.loads((tmp_path / "02-ontology/dod-queries.json").read_text())
    assert saved["queries"][0]["aggregate"] == "count_entities"


def test_three_unapproved_completeness_queries_pause_ontology_without_write(tmp_path) -> None:
    _case(tmp_path)
    invalid = _queries()
    invalid["queries"][0].update(
        aggregate="entities_meeting_completeness",
        **{"class": "party", "properties": "dod", "min_ratio": 0.8},
    )
    decision = _base_decisions(queries=[invalid, invalid, invalid])

    with pytest.raises(
        OntologyDraftUnavailable, match="completeness query requires an approved PRD min_ratio"
    ) as raised:
        draft_ontology(tmp_path, _prd(), _factors(), decision)

    assert raised.value.purpose == "phase2.dod_queries"
    assert raised.value.attempts == 3
    assert not (tmp_path / "02-ontology/ontology.json").exists()
    assert not (tmp_path / "02-ontology/dod-queries.json").exists()
