"""The public engine interface accepts synthetic examples and rejects bad provenance."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
SCHEMAS = {
    path.stem.removesuffix(".schema"): json.loads(path.read_text())
    for path in SCHEMA_DIR.glob("*.schema.json")
}
REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS.values()
)


def validate(name: str, document: object) -> None:
    Draft202012Validator(SCHEMAS[name], registry=REGISTRY, format_checker=FormatChecker()).validate(
        document
    )


def evidence() -> dict:
    return {
        "url": "https://example.invalid/public/supplier/1",
        "bronze_key": "sha256:" + "a" * 64,
        "selector": "table#supplier td.name",
        "screenshot_key": "sha256:" + "b" * 64,
        "captured_at": "2026-01-01T00:00:00Z",
        "source_id": "source-example",
        "source_type": "public_registry",
    }


def field(name: str, value: str) -> dict:
    return {
        "value_id": f"val:{name}-1",
        "value": value,
        "confidence": 0.9,
        "status": "gold",
        "evidence": [evidence()],
    }


def supplier() -> dict:
    fields = {
        "legal_name": field("name", "Proveedor Ejemplo 01"),
        "tax_id": field("tax", "FAKE010101AAA"),
        "address": field("address", "Calle Ejemplo 1"),
        "founding_date": field("founded", "2001-01-01"),
        "tax_list_status": field("tax-status", "unknown"),
        "sanction_status": {
            "value": None,
            "confidence": 0,
            "status": "missing",
            "evidence": [],
        },
    }
    return {
        "id": "sup:example-1",
        "classified_as": ["taxonomy:example"],
        "fields": fields,
        "flags": [
            {
                "rule_id": "sample-rule",
                "label": "Example flag",
                "explanation": "Synthetic example only",
                "evidence_value_ids": ["val:name-1"],
            }
        ],
        "links": [
            {
                "type": "shared_address",
                "target": "sup:example-2",
                "via_value_id": "val:address-1",
            }
        ],
        "contract_ids": ["con:example-1"],
    }


EXAMPLES = {
    "bronze-sidecar": {
        "content_type": "text/html",
        "url": "https://example.invalid/public/supplier/1",
        "captured_at": "2026-01-01T00:00:00Z",
        "source_id": "source-example",
        "step_id": "step-1",
    },
    "approved": {"approver": "Example Reviewer", "date": "2026-01-01"},
    "approval-pending": {
        "phase": 1,
        "checkpoint": "prd",
        "requested_at": "2026-01-01T00:00:00Z",
        "reason": "Review the generated requirements",
        "artifact_paths": ["01-scope/prd.json"],
    },
    "contract": {
        "id": "con:example-1",
        "title": "Synthetic contract",
        "amount": 100,
        "currency": "MXN",
        "date": "2026-01-01",
        "buyer": "Example Buyer",
        "procedure_type": "example procedure",
        "supplier_ids": ["sup:example-1"],
        "evidence": [evidence()],
    },
    "global-prd": {
        "version": "v1",
        "brief_path": "brief.md",
        "personas": [{"id": "persona-1", "description": "Example researcher"}],
        "jobs_to_be_done": [
            {
                "id": "job-1",
                "persona_id": "persona-1",
                "description": "Review a supplier",
            }
        ],
        "requirements": [{"id": "req-1", "job_id": "job-1", "description": "Show source evidence"}],
        "constraints": ["Public sources only"],
        "non_goals": [],
        "definition_of_done": [
            {"id": "dod-1", "metric": "suppliers_total", "operator": ">=", "target": 1}
        ],
    },
    "lake-pointer": {
        "case_id": "example-case",
        "bronze": {
            "kind": "s3",
            "bucket": "synthetic-test-bucket",
            "endpoint": "http://localhost:9000",
        },
        "silver": {
            "postgres_url_env": "SILVER_DATABASE_URL",
            "rdf_store_url_env": "OXIGRAPH_URL",
        },
        "gold": {"rdf_store_url_env": "OXIGRAPH_URL", "export": ["jsonl", "rdf"]},
    },
    "latest": {"run_id": "example-run"},
    "local-prd": {
        "source_id": "source-example",
        "objective_id": "objective-example",
        "global_prd_path": "01-scope/prd.json",
        "global_requirement_ids": ["req-1"],
        "target_fields": ["legal_name"],
        "local_definition_of_done": [
            {"metric": "legal_name_completeness", "operator": ">=", "target": 0.8}
        ],
        "ontology_recommendations": [],
    },
    "metrics": {
        "run_id": "example-run",
        "suppliers_total": 1,
        "suppliers_at_80pct_core": 1,
        "per_field_completeness": {
            "legal_name": 1,
            "tax_id": 1,
            "address": 1,
            "founding_date": 1,
            "tax_list_status": 1,
            "sanction_status": 0,
        },
        "distinct_source_types": 1,
        "gold_values_without_evidence": 0,
        "level_ratio_coverage": {"supplier_profile": [1, 0.5]},
        "mode_counts": {"D0": 0, "D1": 0, "S1": 1, "S2": 0},
        "jobs": {"ok": 1, "failed_by_reason": {}},
    },
    "objectives": {
        "ontology_version": "v1",
        "prd_path": "01-scope/prd.json",
        "objectives": [
            {
                "id": "objective-example",
                "source_id": "source-example",
                "source_url": "https://example.invalid/public",
                "target_fields": ["legal_name"],
                "priority": 1,
                "expected_contribution": 0.5,
            }
        ],
    },
    "run-request": {
        "command": "run",
        "case_dir": "../example-case",
        "from_phase": 1,
        "to_phase": 5,
        "run_id": "example-run",
        "budget_usd": 1.5,
    },
    "supplier": supplier(),
    "tdd": {
        "source_id": "source-example",
        "objective_id": "objective-example",
        "local_prd_path": "04-local/example/prd.json",
        "ontology_version": "v1",
        "source_url": "https://example.invalid/public",
        "allowed_domains": ["example.invalid"],
        "target_fields": ["legal_name"],
        "extraction_method": "dom",
        "validation_rules": ["Nonempty legal name"],
        "rate_limit_per_minute": 10,
        "budget_usd": 1.0,
        "steps": [
            {
                "id": "step-1",
                "description": "Read the public supplier page",
                "starting_mode": "S1",
                "allowed_modes": ["S1"],
                "observation_channel": "text_structure",
                "risk_tier": "SAFE",
                "termination_predicate": "A supplier name is observed",
            }
        ],
    },
    "trace-step": {
        "step_id": "step-1",
        "run_id": "example-run",
        "phase": 5,
        "source_id": "source-example",
        "objective_id": "objective-example",
        "tdd_path": "04-local/example/tdd.json",
        "mode": "S1",
        "observed": {"url": "https://example.invalid/public"},
        "requested": {"tool": "page.snapshot"},
        "executed": {"tool": "page.snapshot", "status": "ok"},
        "evaluated": {"status": "evidence_captured"},
        "parent_step_id": None,
        "value_ids": ["val:name-1"],
        "ts": "2026-01-01T00:00:00Z",
    },
}


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schema_is_valid(name: str) -> None:
    Draft202012Validator.check_schema(SCHEMAS[name])


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_synthetic_example_validates(name: str) -> None:
    validate(name, EXAMPLES[name])


def test_gold_value_without_bronze_evidence_is_rejected() -> None:
    record = supplier()
    record["fields"]["legal_name"]["evidence"] = []
    with pytest.raises(ValidationError):
        validate("supplier", record)


def test_bad_bronze_key_is_rejected() -> None:
    record = supplier()
    record["fields"]["legal_name"]["evidence"][0]["bronze_key"] = "sha256:not-a-hash"
    with pytest.raises(ValidationError):
        validate("supplier", record)


def test_missing_core_field_is_rejected() -> None:
    record = supplier()
    del record["fields"]["tax_id"]
    with pytest.raises(ValidationError):
        validate("supplier", record)


def test_unapproved_execution_mode_is_rejected() -> None:
    record = copy.deepcopy(EXAMPLES["trace-step"])
    record["mode"] = "S3"
    with pytest.raises(ValidationError):
        validate("trace-step", record)


def test_invalid_metric_ratio_is_rejected() -> None:
    record = copy.deepcopy(EXAMPLES["metrics"])
    record["per_field_completeness"]["legal_name"] = 1.5
    with pytest.raises(ValidationError):
        validate("metrics", record)


def test_approval_requires_approver_and_date() -> None:
    with pytest.raises(ValidationError):
        validate("approved", {"approver": "Example Reviewer"})


def test_local_file_lake_pointer_validates() -> None:
    pointer = copy.deepcopy(EXAMPLES["lake-pointer"])
    pointer["bronze"] = {"kind": "file", "root": "/tmp/example-bronze"}
    validate("lake-pointer", pointer)


def test_lake_pointer_requires_case_id() -> None:
    pointer = copy.deepcopy(EXAMPLES["lake-pointer"])
    del pointer["case_id"]
    with pytest.raises(ValidationError):
        validate("lake-pointer", pointer)


def test_bronze_sidecar_requires_step_id() -> None:
    sidecar = copy.deepcopy(EXAMPLES["bronze-sidecar"])
    del sidecar["step_id"]
    with pytest.raises(ValidationError):
        validate("bronze-sidecar", sidecar)
