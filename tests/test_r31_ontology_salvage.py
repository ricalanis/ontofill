"""Recorded R31 regressions for actionable P2 schema feedback and salvage."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from ontofill.case.checkpoints import write_json
from ontofill.contracts import validate_document
from ontofill.inference import ModelValidationExhausted, RecordedDecisionClient, generated_by
from ontofill.lake import FileLake
from ontofill.phases.p2_ontology.phase import (
    OntologyDraftUnavailable,
    _review_core_field_bindings,
    draft_ontology,
)


def _prd() -> dict:
    return {
        "version": "1",
        "brief_path": "brief.md",
        "personas": [{"id": "reader", "description": "A generic reader"}],
        "jobs_to_be_done": [
            {"id": "inspect", "persona_id": "reader", "description": "Inspect public records"}
        ],
        "requirements": [{"id": "trace", "job_id": "inspect", "description": "Trace records"}],
        "constraints": [],
        "non_goals": [],
        "definition_of_done": [
            {
                "id": "record_count",
                "metric": "records",
                "operator": ">=",
                "target": 1,
                "basis": "proposed",
                "rationale": "One record is enough for this recorded fixture.",
                "feasibility": "The fixture contains one synthetic record.",
            }
        ],
        "open_issues": ["No unresolved issues remain in this synthetic PRD."],
        "authority_policy": {
            "jurisdiction": "Example Region",
            "trusted_publishers": [],
            "unknown_source_action": "review",
        },
    }


def _factors() -> dict:
    return {
        "factors": [
            {
                "id": "format",
                "label": "Record format",
                "description": "How record formats vary",
                "kind": "conceptual",
                "evidence": [],
            }
        ]
    }


def _candidate() -> dict:
    classes = [
        {
            "id": "record",
            "label": "Record",
            "label_plural": "Records",
            "description": "A generic record",
            "title_property": "record_name",
            "identifier_property": "record_key",
            "aligned_to": None,
        },
        {
            "id": "event",
            "label": "Event",
            "label_plural": "Events",
            "description": "A generic event",
            "title_property": "event_name",
            "identifier_property": "event_key",
            "aligned_to": None,
        },
    ]
    properties = [
        ("record_key", "Record key", "record"),
        ("record_name", "Record name", "record"),
        ("record_code", "Record code", "record"),
        ("event_key", "Event key", "event"),
        ("event_name", "Event name", "event"),
        ("event_code", "Event code", "event"),
    ]
    return {
        "primary_class": "record",
        "classes": classes,
        "properties": [
            {
                "id": property_id,
                "label": label,
                "domain": domain,
                "datatype": "string",
                "dod": property_id in {"record_key", "record_name"},
                "order": index,
                "description": f"Synthetic {label.lower()} value",
                "aligned_to": None,
            }
            for index, (property_id, label, domain) in enumerate(properties)
        ],
        "relations": [
            {
                "id": "record_event_link",
                "label": "Record event link",
                "domain": "record",
                "range": "event",
                "symmetric": False,
                "match": {
                    "op": "same_value",
                    "domain_property": "record_code",
                    "range_property": "event_code",
                },
            }
        ],
        "rules": [
            {
                "id": "record_event_match",
                "label": "Record and event match",
                "checks": "Record and event values match",
                "verify": ["Compare the synthetic values"],
                "predicate": {
                    "all": [
                        {
                            "op": "same_value",
                            "left_property": "record_code",
                            "right_property": "event_code",
                        }
                    ]
                },
            },
            {
                "id": "record_event_name_match",
                "label": "Record and event names match",
                "checks": "Record and event names match",
                "verify": ["Compare the synthetic names"],
                "predicate": {
                    "all": [
                        {
                            "op": "same_value",
                            "left_property": "record_name",
                            "right_property": "event_name",
                        }
                    ]
                },
            },
            {
                "id": "record_key_example",
                "label": "Record key example",
                "checks": "A record key can equal a literal",
                "verify": ["Check the key value"],
                "predicate": {
                    "all": [{"op": "equals", "property": "record_key", "value": "sample"}]
                },
            },
        ],
        "source_classes": [{"id": "catalog", "label": "Catalog"}],
    }


def _decision(
    candidates: list[dict],
    *,
    rule_semantics: list[dict] | None = None,
    core_field_bindings: list[dict] | None = None,
) -> RecordedDecisionClient:
    return RecordedDecisionClient(
        {
            "phase2.factors": [_factors()],
            "phase2.taxonomies": [
                {
                    "taxonomies": [
                        {
                            "factor_id": "format",
                            "root_label": "Record format",
                            "children": [{"id": "text_record", "label": "Text record", "level": 1}],
                        }
                    ]
                }
            ],
            "critic.phase2.taxonomy_nodes": [
                {
                    "labels": [
                        {
                            "factor_id": "format",
                            "node_id": "text_record",
                            "critic_label": "Good-Exclusive",
                        }
                    ]
                }
            ],
            "critic.phase2.relation_count_semantics": [
                {
                    "assessments": [
                        {
                            "criterion_id": "record_count",
                            "counts_related_entities": False,
                            "class_id": None,
                            "relation_id": None,
                            "reason": "The criterion counts records without a relation condition.",
                        }
                    ]
                }
            ],
            "critic.phase2.rule_semantics": rule_semantics
            or [
                {
                    "assessments": [
                        {
                            "rule_id": "record_key_example",
                            "matches": True,
                            "reason": "The predicate checks the key against its stated example.",
                        }
                    ]
                }
            ],
            "critic.phase2.core_field_bindings": core_field_bindings or [],
            "phase2.schema": candidates,
            "phase2.dod_queries": [
                {
                    "queries": [
                        {
                            "criterion_id": "record_count",
                            "aggregate": "count_entities",
                            "class_id": "record",
                            "target": 1,
                            "operator": ">=",
                        }
                    ]
                }
            ],
        }
    )


def test_cross_class_rules_are_named_then_set_aside_after_three_attempts(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Inspect generic public records.", encoding="utf-8")
    candidates = [_candidate(), _candidate(), _candidate()]
    decision = _decision(candidates)

    ontology = draft_ontology(tmp_path, _prd(), _factors(), decision)

    assert [rule["id"] for rule in ontology["rules"]] == ["record_key_example"]
    assert [relation["id"] for relation in ontology["relations"]] == ["record_event_link"]
    assert len([call for call in decision.calls if call[0] == "phase2.schema"]) == 3
    prompts = [prompt for purpose, prompt in decision.calls if purpose == "phase2.schema"]
    for prompt in prompts[1:]:
        assert "record_event_match" in prompt
        assert "record_event_name_match" in prompt
        assert "Allowed rule predicates" in prompt
        assert "same_value" in prompt
        assert "equals" in prompt
        assert "date_before" in prompt

    recommendations_path = tmp_path / "02-ontology/recommendations/unresolved.json"
    recommendations = json.loads(recommendations_path.read_text(encoding="utf-8"))
    validate_document("ontology-recommendations", recommendations)
    assert recommendations["schema_version"] == "1"
    assert recommendations["ontology_path"] == "02-ontology/ontology.json"
    assert recommendations["generated_by"]["backend"] == "recorded"
    assert [item["id"] for item in recommendations["unresolved"]] == [
        "record_event_match",
        "record_event_name_match",
    ]
    for item, original in zip(
        recommendations["unresolved"], candidates[-1]["rules"][:2], strict=True
    ):
        assert item["kind"] == "rule"
        assert item["proposal"] == original
        assert "one class" in item["reason"]
        assert item["id"] == item["proposal"]["id"]

    validate_document("ontology", ontology)
    queries = json.loads((tmp_path / "02-ontology/dod-queries.json").read_text(encoding="utf-8"))
    validate_document("dod-queries", queries)


def test_invalid_relation_endpoint_feedback_and_salvage(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Inspect generic public records.", encoding="utf-8")
    candidate = _candidate()
    candidate["rules"] = []
    candidate["relations"] = [
        {
            "id": "record_event_link",
            "label": "Record event link",
            "domain": "record",
            "range": "event",
            "symmetric": False,
            "match": {
                "op": "same_value",
                "domain_property": "event_code",
                "range_property": "record_code",
            },
        }
    ]
    candidates = [deepcopy(candidate), deepcopy(candidate), deepcopy(candidate)]
    decision = _decision(candidates)

    ontology = draft_ontology(tmp_path, _prd(), _factors(), decision)

    assert ontology["relations"] == []
    prompts = [prompt for purpose, prompt in decision.calls if purpose == "phase2.schema"]
    assert "record_event_link" in prompts[1]
    assert "domain_property `event_code`" in prompts[1]
    assert "endpoint classes" in prompts[1]
    recommendations = json.loads(
        (tmp_path / "02-ontology/recommendations/unresolved.json").read_text(encoding="utf-8")
    )
    assert recommendations["unresolved"] == [
        {
            "kind": "relation",
            "id": "record_event_link",
            "reason": recommendations["unresolved"][0]["reason"],
            "proposal": candidate["relations"][0],
        }
    ]
    assert "record" in recommendations["unresolved"][0]["reason"]
    assert "event" in recommendations["unresolved"][0]["reason"]


def test_semantically_invalid_presence_rule_is_set_aside_after_three_attempts(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Inspect generic public records.", encoding="utf-8")
    candidate = _candidate()
    candidate["relations"] = []
    candidate["rules"] = [
        {
            "id": "address_presence",
            "label": "Address is present",
            "checks": "The record has an address value",
            "verify": ["Confirm the address is populated"],
            "predicate": {
                "all": [
                    {
                        "op": "same_value",
                        "left_property": "record_name",
                        "right_property": "record_name",
                    }
                ]
            },
        }
    ]
    bad_rule_critic = {
        "assessments": [
            {
                "rule_id": "address_presence",
                "matches": False,
                "reason": "same_value compares the property with itself and is tautological, not presence.",
            }
        ]
    }
    decision = _decision(
        [deepcopy(candidate) for _ in range(3)], rule_semantics=[bad_rule_critic] * 3
    )

    ontology = draft_ontology(tmp_path, _prd(), _factors(), decision)

    assert ontology["rules"] == []
    prompts = [prompt for purpose, prompt in decision.calls if purpose == "phase2.schema"]
    assert "same_value compares the property with itself" in prompts[1]
    recommendations = json.loads(
        (tmp_path / "02-ontology/recommendations/unresolved.json").read_text(encoding="utf-8")
    )
    assert len(recommendations["unresolved"]) == 1
    unresolved = recommendations["unresolved"][0]
    assert unresolved["kind"] == "rule"
    assert unresolved["id"] == "address_presence"
    assert unresolved["proposal"] == candidate["rules"][0]
    assert "tautological" in unresolved["reason"]
    validate_document("ontology", ontology)


def _sf_shaped_repair_inputs() -> tuple[dict, list[dict], list[dict], list[dict]]:
    prd = _prd()
    prd["requirements"][0]["description"] = (
        "List every office's address, weekly opening hours, and free Wi-Fi yes/no."
    )
    prd["definition_of_done"][0]["min_ratio"] = 0.95

    candidate = _candidate()
    candidate["relations"] = []
    extra_properties = [
        ("address", "Address", "string", True),
        ("has_free_wifi", "Free Wi-Fi", "boolean", True),
        ("address_source", "Address source", "string", False),
        ("wifi_source", "Wi-Fi source", "string", False),
        ("hours_source", "Hours source", "string", True),
    ]
    candidate["properties"].extend(
        {
            "id": property_id,
            "label": label,
            "domain": "record",
            "datatype": datatype,
            "dod": dod,
            "order": len(candidate["properties"]) + index,
            "description": f"Synthetic {label.lower()} value",
            "aligned_to": None,
        }
        for index, (property_id, label, datatype, dod) in enumerate(extra_properties)
    )

    cross_class = deepcopy(candidate)
    cross_class["rules"] = [
        {
            "id": "address_cited",
            "label": "Address cited",
            "checks": "The address is linked to a source record",
            "verify": ["Check its source"],
            "predicate": {
                "all": [
                    {
                        "op": "same_value",
                        "left_property": "address_source",
                        "right_property": "event_code",
                    }
                ]
            },
        }
    ]

    wrong_boolean_literal = deepcopy(candidate)
    wrong_boolean_literal["rules"] = [
        {
            "id": "wifi_present",
            "label": "Wi-Fi is available",
            "checks": "The branch offers free Wi-Fi",
            "verify": ["Check the availability value"],
            "predicate": {"all": [{"op": "equals", "property": "has_free_wifi", "value": "true"}]},
        }
    ]

    final_candidate = deepcopy(candidate)
    final_candidate["rules"] = [
        {
            "id": rule_id,
            "label": label,
            "checks": checks,
            "verify": [checks],
            "predicate": {
                "all": [
                    {
                        "op": "same_value",
                        "left_property": property_id,
                        "right_property": property_id,
                    }
                ]
            },
        }
        for rule_id, label, checks, property_id in (
            (
                "address_present",
                "Address present",
                "The address has a non-empty value",
                "address",
            ),
            (
                "wifi_present",
                "Wi-Fi present",
                "The Wi-Fi availability value is stated",
                "has_free_wifi",
            ),
            (
                "address_cited",
                "Address cited",
                "The address has a source citation",
                "address_source",
            ),
            (
                "wifi_cited",
                "Wi-Fi cited",
                "The Wi-Fi value has a source citation",
                "wifi_source",
            ),
        )
    ]
    core_review = {
        "requirements": [
            {
                "requirement_id": "trace",
                "core_fields": [{"field": "weekly opening hours", "property_id": None}],
                "non_core_reason": None,
            }
        ]
    }
    rule_review = {
        "assessments": [
            {
                "rule_id": rule["id"],
                "matches": False,
                "reason": "Self-comparison is tautological and does not establish presence or citation.",
            }
            for rule in final_candidate["rules"]
        ]
    }
    return (
        prd,
        [cross_class, wrong_boolean_literal, final_candidate],
        [core_review],
        [rule_review],
    )


def _completeness_query() -> dict:
    return {
        "queries": [
            {
                "criterion_id": "record_count",
                "aggregate": "entities_meeting_completeness",
                "class": "record",
                "properties": "dod",
                "min_ratio": 0.95,
                "target": 1,
                "operator": ">=",
            }
        ]
    }


def _assert_generic_core_repair(tmp_path, ontology: dict) -> None:
    repaired = next(item for item in ontology["properties"] if item["id"] == "weekly_opening_hours")
    assert repaired["label"] == "weekly opening hours"
    assert repaired["domain"] == "record"
    assert repaired["datatype"] == "string"
    assert repaired["dod"] is True
    validate_document("ontology", ontology)

    recommendations = json.loads(
        (tmp_path / "02-ontology/recommendations/unresolved.json").read_text(encoding="utf-8")
    )
    validate_document("ontology-recommendations", recommendations)
    assert {item["id"] for item in recommendations["unresolved"]} == {
        "address_present",
        "wifi_present",
        "address_cited",
        "wifi_cited",
    }
    assert all(item["kind"] == "rule" for item in recommendations["unresolved"])
    assert all(item["id"] == item["proposal"]["id"] for item in recommendations["unresolved"])
    assert recommendations["repairs"] == [
        {
            "kind": "primary_dod_property",
            "id": "weekly_opening_hours",
            "requirement_id": "trace",
            "field": "weekly opening hours",
            "reason": recommendations["repairs"][0]["reason"],
            "property": repaired,
        }
    ]
    assert "human review" in recommendations["repairs"][0]["reason"]


def test_core_field_and_invalid_rules_are_salvaged_after_three_drafts(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Inspect generic public offices.", encoding="utf-8")
    prd, candidates, core_reviews, rule_reviews = _sf_shaped_repair_inputs()
    decision = _decision(
        candidates,
        core_field_bindings=core_reviews,
        rule_semantics=rule_reviews,
    )
    decision.responses["phase2.dod_queries"].clear()
    decision.responses["phase2.dod_queries"].append(_completeness_query())

    ontology = draft_ontology(tmp_path, prd, _factors(), decision)

    _assert_generic_core_repair(tmp_path, ontology)
    assert len([item for item in decision.calls if item[0] == "phase2.schema"]) == 3
    assert not any(rule["id"] in {"address_present", "wifi_present"} for rule in ontology["rules"])


def test_final_malformed_draft_salvages_latest_structurally_valid_candidate(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Inspect generic public offices.", encoding="utf-8")
    prd, candidates, core_reviews, rule_reviews = _sf_shaped_repair_inputs()
    salvageable_candidate = candidates[-1]
    decision = _decision(
        [salvageable_candidate, salvageable_candidate, salvageable_candidate],
        core_field_bindings=core_reviews * 3,
        rule_semantics=rule_reviews * 3,
    )
    decision.responses["phase2.dod_queries"].clear()
    decision.responses["phase2.dod_queries"].append(_completeness_query())
    complete_json = decision.complete_json
    schema_attempts = 0

    def malformed_final_response(purpose, prompt, schema):
        nonlocal schema_attempts
        if purpose == "phase2.schema":
            schema_attempts += 1
            if schema_attempts == 2:
                decision.calls.append((purpose, prompt))
                raise ModelValidationExhausted(
                    purpose, "synthetic malformed final schema response", 3
                )
        return complete_json(purpose, prompt, schema)

    decision.complete_json = malformed_final_response

    ontology = draft_ontology(tmp_path, prd, _factors(), decision)

    _assert_generic_core_repair(tmp_path, ontology)
    assert schema_attempts == 2
    assert len([item for item in decision.calls if item[0] == "phase2.schema"]) == 2


def test_core_field_feedback_names_prd_phrase_and_primary_dod_candidates() -> None:
    prd = _prd()
    prd["requirements"][0]["description"] = "Collect weekly opening hours for every branch."
    prd["definition_of_done"][0]["min_ratio"] = 0.95
    ontology = _candidate()
    ontology["properties"].append(
        {
            "id": "weekly_hours_text",
            "label": "Weekly hours text",
            "domain": "record",
            "datatype": "string",
            "dod": True,
            "order": len(ontology["properties"]),
            "description": "Synthetic opening hours",
            "aligned_to": None,
        }
    )
    decision = RecordedDecisionClient(
        {
            "critic.phase2.core_field_bindings": [
                {
                    "requirements": [
                        {
                            "requirement_id": "trace",
                            "core_fields": [{"field": "weekly opening hours", "property_id": None}],
                            "non_core_reason": None,
                        }
                    ]
                }
            ]
        }
    )

    with pytest.raises(ValueError) as error:
        _review_core_field_bindings(prd, ontology, decision)

    message = str(error.value)
    assert "weekly opening hours" in message
    assert "Collect weekly opening hours" in message
    assert "Weekly hours text" in message
    assert "weekly_hours_text" in message


def test_clean_draft_clears_prior_unresolved_recommendations(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Inspect generic public records.", encoding="utf-8")
    recommendation_path = tmp_path / "02-ontology/recommendations/unresolved.json"
    recommendation_path.parent.mkdir(parents=True)
    recommendation_path.write_text('{"unresolved": [{"id": "stale"}]}', encoding="utf-8")
    candidate = _candidate()
    candidate["rules"] = [candidate["rules"][-1]]
    decision = _decision([candidate])

    draft_ontology(tmp_path, _prd(), _factors(), decision)

    recommendations = json.loads(recommendation_path.read_text(encoding="utf-8"))
    validate_document("ontology-recommendations", recommendations)
    assert recommendations["unresolved"] == []
    assert recommendations["generated_by"]["backend"] == "recorded"


def test_non_rule_schema_defect_still_fails_without_staging(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Inspect generic public records.", encoding="utf-8")
    candidates = [_candidate(), _candidate(), _candidate()]
    for candidate in candidates:
        candidate["classes"][0]["identifier_property"] = "missing_record_key"
    decision = _decision(candidates)

    with pytest.raises(OntologyDraftUnavailable, match="identifier_property must refer"):
        draft_ontology(tmp_path, _prd(), _factors(), decision)

    assert len([call for call in decision.calls if call[0] == "phase2.schema"]) == 3
    assert not (tmp_path / "02-ontology/ontology.json").exists()
    assert not (tmp_path / "02-ontology/dod-queries.json").exists()
    assert not (tmp_path / "02-ontology/recommendations/unresolved.json").exists()


def test_salvaged_schema_reaches_the_ontology_approval_checkpoint(tmp_path, monkeypatch) -> None:
    from ontofill import workflow

    original_case = tmp_path / "original-case"
    original_case.mkdir()
    (original_case / "brief.md").write_text("Inspect generic public records.", encoding="utf-8")
    scratch = tmp_path / "scratch-case"
    scratch.mkdir()
    (scratch / "brief.md").write_text("Inspect generic public records.", encoding="utf-8")
    lake = FileLake(tmp_path / "lake")
    monkeypatch.setattr(workflow, "_scratch_case", lambda *_args: (scratch, lake))

    decision = _decision([_candidate(), _candidate(), _candidate()])
    prd = _prd()
    prd["generated_by"] = generated_by(decision)
    validate_document("global-prd", prd)

    def draft_recorded_prd(case_dir, *_args, **_kwargs):
        write_json(case_dir / "01-scope/prd.json", prd)
        return prd

    monkeypatch.setattr(workflow, "draft_prd", draft_recorded_prd)
    original_require_approval = workflow.require_approval

    def require_approval(directory, *, checkpoint, **kwargs):
        if checkpoint in {"prd", "factors"}:
            return True
        return original_require_approval(directory, checkpoint=checkpoint, **kwargs)

    monkeypatch.setattr(workflow, "require_approval", require_approval)

    result = workflow.run_case(
        original_case,
        run_id="mock-r31-ontology-salvage",
        decision=decision,
        lake=lake,
        preview_past_checkpoints=True,
        to_phase=2,
    )

    assert result == 3
    assert (scratch / "02-ontology/ontology.json").is_file()
    assert (scratch / "02-ontology/recommendations/unresolved.json").is_file()
    pending = (scratch / "02-ontology/APPROVAL_PENDING.md").read_text(encoding="utf-8")
    assert "# Approval pending: ontology" in pending
    assert "02-ontology/ontology.json" in pending
    status = json.loads(lake.read_key("runs/original-case/mock-r31-ontology-salvage/status.json"))
    assert status["state"] == "paused"
    assert status["checkpoint_pending"] == "ontology"
