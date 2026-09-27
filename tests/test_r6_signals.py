"""Synthetic coverage for typed, evidence-backed ontology signals."""

from __future__ import annotations

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator, ValidationError

from ontofill.contracts import validate_document
from ontofill.lake import FileLake
from ontofill.phases.p2_ontology.phase import (
    _schema_proposal,
    _validate_ontology,
    _validate_queries,
)
from ontofill.refiner import Observation, refine_observations
from ontofill.refiner.export import _query_actual, _validate_entities

RECORDED = {"backend": "recorded", "model": "fixture", "at": "2026-09-26T00:00:00Z"}


def _property(property_id: str, domain: str, datatype: str) -> dict:
    return {
        "id": property_id,
        "label": property_id.replace("_", " ").title(),
        "domain": domain,
        "datatype": datatype,
        "dod": False,
        "order": 0,
        "description": f"Synthetic {property_id}",
        "aligned_to": None,
    }


def _class(class_id: str, title_property: str, identifier_property: str) -> dict:
    return {
        "id": class_id,
        "label": class_id,
        "label_plural": f"{class_id}s",
        "description": f"Synthetic {class_id}",
        "title_property": title_property,
        "identifier_property": identifier_property,
        "aligned_to": None,
    }


def _ontology() -> dict:
    properties = [
        _property("record_id", "Record", "string"),
        _property("title", "Record", "string"),
        _property("reference_primary", "Record", "string"),
        _property("reference_copy", "Record", "string"),
        _property("active", "Record", "boolean"),
        _property("start_date", "Record", "date"),
        _property("end_date", "Record", "date"),
        _property("repository_code", "Record", "string"),
        _property("repository_id", "Repository", "string"),
        _property("name", "Repository", "string"),
    ]
    return {
        "version": "v1",
        "prd_path": "01-scope/prd.json",
        "factors": [],
        "taxonomies": [],
        "primary_class": "Record",
        "classes": [
            _class("Record", "title", "record_id"),
            _class("Repository", "name", "repository_id"),
        ],
        "properties": properties,
        "relations": [
            {
                "id": "stored_at",
                "label": "Stored at",
                "domain": "Record",
                "range": "Repository",
                "symmetric": False,
                "match": {
                    "op": "same_value",
                    "domain_property": "repository_code",
                    "range_property": "repository_id",
                },
            }
        ],
        "rules": [
            {
                "id": "active_record",
                "label": "Active record",
                "checks": ["Human-readable description only"],
                "verify": ["Review source evidence"],
                "predicate": {
                    "all": [
                        {
                            "op": "same_value",
                            "left_property": "reference_primary",
                            "right_property": "reference_copy",
                        },
                        {"op": "equals", "property": "active", "value": True},
                        {
                            "op": "date_before",
                            "earlier_property": "start_date",
                            "later_property": "end_date",
                        },
                    ]
                },
            },
            {
                "id": "legacy_description",
                "label": "Legacy description",
                "checks": ["This prose is not executable"],
                "verify": ["Manual review"],
            },
        ],
        "source_classes": [{"id": "public_catalog", "label": "Public catalog"}],
        "dod_queries_path": "02-ontology/dod-queries.json",
        "shacl_path": "02-ontology/shapes.ttl",
        "generated_by": RECORDED,
    }


def _evidence(lake: FileLake) -> dict:
    return {
        "url": "https://example.test/record",
        "bronze_key": lake.put_bytes(b"synthetic captured page"),
        "selector": "main",
        "screenshot_key": lake.put_bytes(b"synthetic screenshot"),
        "captured_at": "2026-09-26T00:00:00Z",
        "source_id": "fixture-source",
        "source_type": "public_catalog",
    }


def _observation(
    lake: FileLake,
    class_id: str,
    entity_suffix: str,
    property_id: str,
    value: str | bool,
) -> Observation:
    return Observation(
        run_id="mock-r6-signals",
        entity_id=f"{class_id}:{entity_suffix}",
        entity_class=class_id,
        property_id=property_id,
        value=value,
        evidence=_evidence(lake),
        step_id="synthetic-step",
        generated_by=RECORDED,
    )


def test_refinement_builds_typed_flag_relation_and_relation_count(tmp_path) -> None:
    lake = FileLake(tmp_path / "lake")
    ontology = _ontology()
    observations = []
    good = {
        "record_id": "R-1",
        "title": "First",
        "reference_primary": "K-1",
        "reference_copy": "K-1",
        "active": True,
        "start_date": "2021-01-01",
        "end_date": "2021-12-31",
        "repository_code": "A-1",
    }
    negative = {
        "record_id": "R-2",
        "title": "Second",
        "reference_primary": "K-2",
        "reference_copy": "K-2",
        "active": False,
        "start_date": "2022-01-01",
        "end_date": "2022-12-31",
        "repository_code": "A-missing",
    }
    for suffix, values in (("one", good), ("two", negative)):
        observations.extend(
            _observation(lake, "Record", suffix, property_id, value)
            for property_id, value in values.items()
        )
    observations.extend(
        [
            _observation(lake, "Repository", "one", "repository_id", "A-1"),
            _observation(lake, "Repository", "one", "name", "Main"),
        ]
    )

    result = refine_observations(observations, ontology=ontology, generated_by=RECORDED)
    by_id = {entity["id"]: entity for entity in result.entities}
    first = by_id["Record:one"]
    second = by_id["Record:two"]
    repository = by_id["Repository:one"]

    assert result.rejected == []
    assert [flag["rule_id"] for flag in first["flags"]] == ["active_record"]
    assert set(first["flags"][0]["evidence_value_ids"]) == {
        first["properties"][property_id]["value_id"]
        for property_id in (
            "reference_primary",
            "reference_copy",
            "active",
            "start_date",
            "end_date",
        )
    }
    assert first["links"] == [
        {
            "property": "stored_at",
            "target": "Repository:one",
            "via_value_id": first["properties"]["repository_code"]["value_id"],
        }
    ]
    assert second["flags"] == []
    assert second["links"] == []
    assert repository["links"] == []

    query = {
        "aggregate": "count_entities_with_relation",
        "relation_id": "stored_at",
        "class_id": "Record",
    }
    assert _query_actual(result.entities, query) == 1
    _validate_entities(result.entities, ontology, lake)

    tampered = deepcopy(result.entities)
    tampered_by_id = {entity["id"]: entity for entity in tampered}
    tampered_by_id["Record:one"]["links"].clear()
    with pytest.raises(ValueError, match="typed relation predicate"):
        _validate_entities(tampered, ontology, lake)

    tampered_flags = deepcopy(result.entities)
    tampered_flag_entity = next(item for item in tampered_flags if item["id"] == "Record:one")
    tampered_flag_entity["flags"][0]["evidence_value_ids"].pop()
    with pytest.raises(ValueError, match="typed rule predicates"):
        _validate_entities(tampered_flags, ontology, lake)


def test_p2_requires_typed_predicates_and_validates_references() -> None:
    ontology = _ontology()
    proposal = {
        key: ontology[key]
        for key in (
            "primary_class",
            "classes",
            "properties",
            "relations",
            "rules",
            "source_classes",
        )
    }
    proposal["rules"] = [rule for rule in proposal["rules"] if "predicate" in rule]
    proposal_validator = Draft202012Validator(_schema_proposal())
    proposal_validator.validate(proposal)
    _validate_ontology(ontology)

    missing_match = deepcopy(proposal)
    del missing_match["relations"][0]["match"]
    with pytest.raises(ValidationError):
        proposal_validator.validate(missing_match)

    missing_predicate = deepcopy(proposal)
    del missing_predicate["rules"][0]["predicate"]
    with pytest.raises(ValidationError):
        proposal_validator.validate(missing_predicate)

    bad_reference = deepcopy(ontology)
    bad_reference["rules"][0]["predicate"]["all"][0]["left_property"] = "unknown_field"
    with pytest.raises(ValueError, match="unknown property"):
        _validate_ontology(bad_reference)

    bad_literal = deepcopy(ontology)
    bad_literal["rules"][0]["predicate"]["all"][1]["value"] = "true"
    with pytest.raises(ValueError, match="does not match datatype"):
        _validate_ontology(bad_literal)


def test_relation_count_schema_and_p2_validation() -> None:
    ontology = _ontology()
    query = {
        "prd_path": "01-scope/prd.json",
        "ontology_version": "v1",
        "queries": [
            {
                "criterion_id": "linked_records",
                "aggregate": "count_entities_with_relation",
                "class_id": "Record",
                "relation_id": "stored_at",
                "target": 1,
                "operator": ">=",
            }
        ],
        "generated_by": RECORDED,
    }
    validate_document("dod-queries", query)
    prd = {"definition_of_done": [{"id": "linked_records", "target": 1, "operator": ">="}]}
    _validate_queries(prd, ontology, query)

    missing_relation = deepcopy(query)
    del missing_relation["queries"][0]["relation_id"]
    with pytest.raises(ValidationError):
        validate_document("dod-queries", missing_relation)

    bad_class = deepcopy(query)
    bad_class["queries"][0]["class_id"] = "Repository"
    with pytest.raises(ValueError, match="relation domain"):
        _validate_queries(prd, ontology, bad_class)
