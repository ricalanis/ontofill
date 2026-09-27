"""Synthetic regressions for P2 schema and DoD semantic guards."""

from __future__ import annotations

import json

import pytest

from ontofill.inference import RecordedDecisionClient
from ontofill.phases.p2_ontology.phase import (
    OntologyDraftUnavailable,
    _validate_queries,
    draft_ontology,
)

_RECORDED = {"backend": "recorded", "model": "synthetic", "at": "2026-09-27T00:00:00Z"}


def _prd() -> dict:
    return {
        "version": "1",
        "brief_path": "brief.md",
        "personas": [{"id": "reader", "description": "A reader of public records"}],
        "jobs_to_be_done": [
            {"id": "inspect", "persona_id": "reader", "description": "Inspect records"}
        ],
        "requirements": [
            {
                "id": "record-name",
                "job_id": "inspect",
                "description": "Every record includes its readable name.",
            },
            {
                "id": "record-key",
                "job_id": "inspect",
                "description": "Every record includes its stable key.",
            },
        ],
        "constraints": [],
        "non_goals": [],
        "definition_of_done": [
            {
                "id": "linked-records",
                "metric": "Count linked records",
                "operator": ">=",
                "target": 1,
                "basis": "proposed",
                "rationale": "One record with a typed link proves the workflow.",
                "feasibility": "The synthetic fixture has one linked record.",
            },
            {
                "id": "record-completeness",
                "metric": "Complete records",
                "operator": ">=",
                "target": 1,
                "min_ratio": 0.8,
                "basis": "proposed",
                "rationale": "One sufficiently complete record proves the workflow.",
                "feasibility": "The synthetic fixture has one complete record.",
            },
        ],
        "open_issues": [],
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
                "description": "How records vary by format",
                "kind": "conceptual",
                "evidence": [],
            }
        ]
    }


def _taxonomy() -> dict:
    return {
        "taxonomies": [
            {
                "factor_id": "format",
                "root_label": "Record format",
                "children": [{"id": "text", "label": "Text", "level": 1}],
            }
        ]
    }


def _schema(
    *,
    record_name_domain: str = "record",
    relation_domain: str = "record",
    rule_label: str = "Record is active",
) -> dict:
    relation_range = "event" if relation_domain == "record" else "record"
    domain_property = "record_key" if relation_domain == "record" else "event_key"
    range_property = "event_key" if relation_domain == "record" else "record_key"
    return {
        "primary_class": "record",
        "classes": [
            {
                "id": "record",
                "label": "Record",
                "label_plural": "Records",
                "description": "A public record",
                "title_property": "record_key",
                "identifier_property": "record_key",
                "aligned_to": None,
            },
            {
                "id": "event",
                "label": "Event",
                "label_plural": "Events",
                "description": "A related event",
                "title_property": "event_name",
                "identifier_property": "event_key",
                "aligned_to": None,
            },
        ],
        "properties": [
            {
                "id": "record_key",
                "label": "Record key",
                "domain": "record",
                "datatype": "string",
                "dod": True,
                "order": 0,
                "description": "Stable record key",
                "aligned_to": None,
            },
            {
                "id": "record_name",
                "label": "Record name",
                "domain": record_name_domain,
                "datatype": "string",
                "dod": True,
                "order": 1,
                "description": "Readable record name",
                "aligned_to": None,
            },
            {
                "id": "record_active",
                "label": "Record active state",
                "domain": "record",
                "datatype": "boolean",
                "dod": False,
                "order": 2,
                "description": "Whether the record is active",
                "aligned_to": None,
            },
            {
                "id": "event_key",
                "label": "Event key",
                "domain": "event",
                "datatype": "string",
                "dod": False,
                "order": 3,
                "description": "Stable event key",
                "aligned_to": None,
            },
            {
                "id": "event_name",
                "label": "Event name",
                "domain": "event",
                "datatype": "string",
                "dod": False,
                "order": 4,
                "description": "Readable event name",
                "aligned_to": None,
            },
        ],
        "relations": [
            {
                "id": "has_event",
                "label": "Record event link",
                "domain": relation_domain,
                "range": relation_range,
                "symmetric": False,
                "match": {
                    "op": "same_value",
                    "domain_property": domain_property,
                    "range_property": range_property,
                },
            }
        ],
        "rules": [
            {
                "id": "record_active_check",
                "label": rule_label,
                "checks": "The record is active when its active state is true.",
                "verify": ["Compare the active state with the stated condition."],
                "predicate": {
                    "all": [{"op": "equals", "property": "record_active", "value": True}]
                },
            }
        ],
        "source_classes": [{"id": "catalog", "label": "Public catalog"}],
    }


def _core_field_review(*, record_name_property: str = "record_name") -> dict:
    return {
        "requirements": [
            {
                "requirement_id": "record-name",
                "core_fields": [{"field": "readable name", "property_id": record_name_property}],
                "non_core_reason": None,
            },
            {
                "requirement_id": "record-key",
                "core_fields": [{"field": "stable key", "property_id": "record_key"}],
                "non_core_reason": None,
            },
        ]
    }


def _relation_count_review() -> dict:
    return {
        "assessments": [
            {
                "criterion_id": "linked-records",
                "counts_related_entities": True,
                "class_id": "record",
                "relation_id": "has_event",
                "reason": "The criterion counts records with a typed event link.",
            },
            {
                "criterion_id": "record-completeness",
                "counts_related_entities": False,
                "class_id": None,
                "relation_id": None,
                "reason": "The criterion measures per-record property completeness.",
            },
        ]
    }


def _rule_review(*, matches: bool) -> dict:
    return {
        "assessments": [
            {
                "rule_id": "record_active_check",
                "matches": matches,
                "reason": (
                    "The predicate checks record_active equals true."
                    if matches
                    else "The label names a future completion date, but the predicate only "
                    "checks whether record_active equals true."
                ),
            }
        ]
    }


def _query(*, count_class: str = "record") -> dict:
    return {
        "queries": [
            {
                "criterion_id": "linked-records",
                "aggregate": "count_entities_with_relation",
                "class_id": count_class,
                "relation_id": "has_event",
                "target": 1,
                "operator": ">=",
            },
            {
                "criterion_id": "record-completeness",
                "aggregate": "entities_meeting_completeness",
                "class": "record",
                "properties": "dod",
                "min_ratio": 0.8,
                "target": 1,
                "operator": ">=",
            },
        ]
    }


def _decision(schemas: list[dict]) -> RecordedDecisionClient:
    return RecordedDecisionClient(
        {
            "phase2.taxonomies": [_taxonomy()],
            "critic.phase2.taxonomy_nodes": [
                {
                    "labels": [
                        {
                            "factor_id": "format",
                            "node_id": "text",
                            "critic_label": "Good-Exclusive",
                        }
                    ]
                }
            ],
            "phase2.schema": schemas,
            "critic.phase2.core_field_bindings": [
                _core_field_review(),
                _core_field_review(),
            ],
            "critic.phase2.relation_count_semantics": [
                _relation_count_review(),
                _relation_count_review(),
            ],
            "critic.phase2.rule_semantics": [
                _rule_review(matches=False),
                _rule_review(matches=True),
            ],
            "phase2.dod_queries": [_query(count_class="event"), _query()],
        }
    )


def test_p2_retries_named_ontology_defects_and_query_class_without_rewriting_prd(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Describe generic public records.", encoding="utf-8")
    prd = _prd()
    prd_dir = tmp_path / "01-scope"
    prd_dir.mkdir()
    prd_bytes = json.dumps(prd, ensure_ascii=False, indent=2).encode("utf-8")
    (prd_dir / "prd.json").write_bytes(prd_bytes)

    invalid_schema = _schema(
        record_name_domain="event",
        relation_domain="event",
        rule_label="Record has a future completion date",
    )
    corrected_schema = _schema()
    decision = _decision([invalid_schema, corrected_schema])
    validation_errors: list[tuple[str, int, str]] = []

    ontology = draft_ontology(
        tmp_path,
        prd,
        _factors(),
        decision,
        on_validation_error=lambda purpose, attempt, reason: validation_errors.append(
            (purpose, attempt, reason)
        ),
    )

    schema_prompts = [prompt for purpose, prompt in decision.calls if purpose == "phase2.schema"]
    query_prompts = [
        prompt for purpose, prompt in decision.calls if purpose == "phase2.dod_queries"
    ]
    assert len(schema_prompts) == 2
    assert len(query_prompts) == 2
    combined_schema_error = validation_errors[0][2]
    assert "core PRD field `readable name`" in combined_schema_error
    assert "primary class `record`" in combined_schema_error
    assert "criterion `linked-records` names counted class `record`" in combined_schema_error
    assert "relation `has_event` is oriented `event` → `record`" in combined_schema_error
    assert "rule `record_active_check` label/checks do not match" in combined_schema_error
    assert "future completion date" in combined_schema_error
    assert "DoD criterion `linked-records` must count the named class `record`" in validation_errors[1][2]
    assert validation_errors[1][2] in query_prompts[1]
    assert [item[:2] for item in validation_errors] == [
        ("phase2.schema", 1),
        ("phase2.dod_queries", 1),
    ]
    assert ontology["relations"][0]["domain"] == "record"
    saved_queries = json.loads((tmp_path / "02-ontology/dod-queries.json").read_text())
    assert saved_queries["queries"][0]["class_id"] == "record"
    assert saved_queries["queries"][1]["properties"] == "dod"
    assert (prd_dir / "prd.json").read_bytes() == prd_bytes


def test_zero_primary_dod_properties_fails_before_query_drafting(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Describe generic public records.", encoding="utf-8")
    prd = _prd()
    candidate = _schema()
    for prop in candidate["properties"]:
        if prop["domain"] == "record":
            prop["dod"] = False
    candidate["properties"][-1]["dod"] = True
    decision = _decision([candidate, candidate, candidate])

    with pytest.raises(OntologyDraftUnavailable) as raised:
        draft_ontology(tmp_path, prd, _factors(), decision)

    assert raised.value.purpose == "phase2.schema"
    assert "primary class `record` to own at least one `dod: true` property" in raised.value.reason
    assert not (tmp_path / "02-ontology/ontology.json").exists()
    assert not any(purpose == "phase2.dod_queries" for purpose, _ in decision.calls)


def test_invalid_cached_core_field_semantics_are_reviewed_before_cache_reuse(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Describe generic public records.", encoding="utf-8")
    prd = _prd()
    draft_ontology(tmp_path, prd, _factors(), _decision([_schema()] * 4))

    ontology_path = tmp_path / "02-ontology/ontology.json"
    ontology = json.loads(ontology_path.read_text(encoding="utf-8"))
    ontology["properties"][
        next(index for index, item in enumerate(ontology["properties"]) if item["id"] == "record_name")
    ]["dod"] = False
    ontology_path.write_text(json.dumps(ontology), encoding="utf-8")

    class RejectCacheDecision:
        backend = "recorded"
        model = "synthetic"

        def __init__(self) -> None:
            self.calls: list[str] = []

        def complete_json(self, purpose, _prompt, _schema):
            self.calls.append(purpose)
            if purpose == "critic.phase2.core_field_bindings":
                return _core_field_review()
            if purpose == "critic.phase2.relation_count_semantics":
                return _relation_count_review()
            if purpose == "critic.phase2.rule_semantics":
                return _rule_review(matches=True)
            raise AssertionError(f"cache validation should reject before `{purpose}`")

    decision = RejectCacheDecision()
    try:
        draft_ontology(tmp_path, prd, _factors(), decision)
    except AssertionError as exc:
        assert "phase2.taxonomies" in str(exc)
    else:
        raise AssertionError("an invalid cached core-field mapping was reused")

    assert decision.calls == [
        "critic.phase2.core_field_bindings",
        "critic.phase2.relation_count_semantics",
        "critic.phase2.rule_semantics",
        "phase2.taxonomies",
    ]


def test_query_validator_rejects_secondary_class_dod_placement() -> None:
    prd = _prd()
    ontology = {
        "primary_class": "record",
        "classes": _schema()["classes"],
        "properties": [
            {"id": "record_key", "domain": "record", "dod": False},
            {"id": "record_name", "domain": "event", "dod": True},
        ],
        "relations": [],
    }
    query = {
        "queries": [
            {
                "criterion_id": "record-completeness",
                "aggregate": "entities_meeting_completeness",
                "class": "record",
                "properties": "dod",
                "min_ratio": 0.8,
                "target": 1,
                "operator": ">=",
            },
            {
                "criterion_id": "linked-records",
                "aggregate": "count_entities",
                "class_id": "record",
                "target": 1,
                "operator": ">=",
            },
        ]
    }

    with pytest.raises(ValueError, match="no `dod: true` properties to measure"):
        _validate_queries(prd, ontology, query)
