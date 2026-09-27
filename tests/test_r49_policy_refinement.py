"""R49: broad, tiered authority coverage and value-level refinement."""

from __future__ import annotations

from copy import deepcopy

from ontofill.contracts import validate_document
from ontofill.phases.p1_scope.phase import (
    _authority_policy_check,
    _objection_subject_changed,
)
from ontofill.refiner import Observation, refine_observations

RECORDED = {"backend": "recorded", "model": "synthetic", "at": "2026-01-01T00:00:00Z"}
BRONZE = "sha256:" + "0" * 64

LEVELS = ["national_federal", "state_provincial", "municipal", "autonomous_bodies"]
CHANNELS = [
    "open_data",
    "transparency_obligations",
    "registries",
    "lists",
    "datasets",
    "apis",
    "procurement_portals",
    "gazettes",
]


def _policy() -> dict:
    return {
        "schema_version": "1",
        "jurisdiction": "Example Republic",
        "jurisdiction_hierarchy": {
            "root_level": "national_federal",
            "include_descendants": True,
        },
        "trusted_publishers": [],
        "authority_matrix": [
            {
                "source_class": "executive_agency",
                "government_levels": LEVELS.copy(),
                "channel_types": CHANNELS.copy(),
                "default_tier": "primary",
            },
            {
                "source_class": "independent_regulator",
                "government_levels": LEVELS.copy(),
                "channel_types": CHANNELS.copy(),
                "default_tier": "primary",
            },
        ],
        "recall_coverage": {
            "government_levels": LEVELS.copy(),
            "channel_types": CHANNELS.copy(),
            "minimum_independent_publishers_per_property": 2,
        },
        "unknown_source_action": "review",
        "unknown_official_action": "admit_low_tier_flagged",
    }


def _prd(policy: dict) -> dict:
    return {
        "version": "1",
        "brief_path": "brief.md",
        "personas": [{"id": "reader", "description": "Uses public entity records."}],
        "jobs_to_be_done": [
            {"id": "find_records", "persona_id": "reader", "description": "Find records."}
        ],
        "requirements": [
            {"id": "evidence", "job_id": "find_records", "description": "Show evidence."}
        ],
        "constraints": ["Use public information."],
        "non_goals": [],
        "definition_of_done": [
            {
                "id": "entity_rows",
                "metric": "entity rows with evidence",
                "operator": ">=",
                "target": 1,
                "basis": "proposed",
                "rationale": "One row exercises the synthetic criterion.",
                "feasibility": "One row fits the synthetic run budget.",
            }
        ],
        "authority_policy": policy,
        "generated_by": RECORDED,
    }


def _observation(
    value: str,
    *,
    source_id: str,
    tier: str = "unknown",
    publisher_id: str | None = None,
    entity_id: str = "Record:one",
) -> Observation:
    return Observation(
        run_id="mock-r49",
        entity_id=entity_id,
        entity_class="Record",
        property_id="status",
        value=value,
        evidence={
            "url": f"https://{source_id}.example.test/record",
            "bronze_key": BRONZE,
            "selector": "table row 1",
            "screenshot_key": BRONZE,
            "captured_at": "2026-01-01T00:00:00Z",
            "source_id": source_id,
            "source_type": "registry",
        },
        step_id=f"step:{source_id}",
        generated_by=RECORDED,
        authority_tier=tier,
        publisher_id=publisher_id,
    )


def _ontology() -> dict:
    return {
        "primary_class": "Record",
        "classes": [
            {
                "id": "Record",
                "title_property": "status",
                "identifier_property": "status",
            }
        ],
        "properties": [{"id": "status", "domain": "Record", "datatype": "xsd:string"}],
        "relations": [],
        "rules": [],
    }


def test_global_prd_accepts_versioned_authority_matrix_and_legacy_policy() -> None:
    legacy = _prd(
        {
            "jurisdiction": "Example Republic",
            "trusted_publishers": [],
            "unknown_source_action": "review",
        }
    )
    versioned = _prd(_policy())

    validate_document("global-prd", legacy)
    validate_document("global-prd", versioned)


def test_p1_reports_recall_gaps_in_authority_matrix() -> None:
    policy = _policy()
    policy["authority_matrix"][0]["government_levels"].remove("municipal")
    policy["authority_matrix"][0]["channel_types"].remove("open_data")
    policy["authority_matrix"].pop()
    policy["recall_coverage"]["minimum_independent_publishers_per_property"] = 1

    result = _authority_policy_check(_prd(policy), [])

    assert not result.passed
    assert any(
        "municipal" in objection and "coverage" in objection for objection in result.objections
    )
    assert any(
        "open_data" in objection and "coverage" in objection for objection in result.objections
    )
    assert any("independent publisher" in objection for objection in result.objections)
    before = _prd(_policy())
    after = deepcopy(before)
    after["authority_policy"]["authority_matrix"][0]["channel_types"].remove("open_data")
    assert _objection_subject_changed(
        before,
        after,
        "Authority policy recall coverage at government level municipal is missing channel type open_data",
    )


def test_refiner_prefers_tier_then_independent_corroboration_and_keeps_conflict_receipts() -> None:
    observations = [
        _observation("primary-value", source_id="primary", tier="primary"),
        _observation("secondary-value", source_id="secondary-a", tier="secondary"),
        _observation("secondary-value", source_id="secondary-b", tier="secondary"),
        _observation(
            "uncorroborated",
            source_id="secondary-c",
            tier="secondary",
            publisher_id="publisher-one",
            entity_id="Record:two",
        ),
        _observation(
            "uncorroborated",
            source_id="secondary-c-copy",
            tier="secondary",
            publisher_id="publisher-one",
            entity_id="Record:two",
        ),
        _observation(
            "corroborated",
            source_id="secondary-d",
            tier="secondary",
            publisher_id="publisher-two",
            entity_id="Record:two",
        ),
        _observation(
            "corroborated",
            source_id="secondary-e",
            tier="secondary",
            publisher_id="publisher-three",
            entity_id="Record:two",
        ),
    ]

    result = refine_observations(observations, ontology=_ontology(), generated_by=RECORDED)

    first, second = result.entities
    assert first["properties"]["status"]["value"] == "primary-value"
    assert first["properties"]["status"]["status"] == "conflict"
    assert {item["source_id"] for item in first["properties"]["status"]["evidence"]} == {
        "primary",
        "secondary-a",
        "secondary-b",
    }
    assert second["properties"]["status"]["value"] == "corroborated"
    assert second["properties"]["status"]["status"] == "conflict"


def test_p1_legacy_authority_policy_remains_accepted_without_recall_fields() -> None:
    prd = _prd(
        {
            "jurisdiction": "Example Republic",
            "trusted_publishers": [
                {
                    "kind": "Example Republic records office",
                    "tier": "primary",
                    "jurisdiction": "Example Republic",
                    "domains": ["records.example.test"],
                    "rationale": "The office publishes official records.",
                }
            ],
            "unknown_source_action": "review",
        }
    )

    assert _authority_policy_check(prd, []).passed
