from __future__ import annotations

import json

import pytest
from ontofill_scrape.models import ParsedFile

from ontofill.inference import RecordedDecisionClient, generated_by
from ontofill.lake import FileLake
from ontofill.phases.p5_execute import execute_objective
from ontofill.phases.p5_execute.phase import _execute_nested_records
from ontofill.phases.p5_execute.record_mapping import (
    extract_record_instances,
    record_mapping_model_schema,
    record_shape,
    validate_record_mapping,
    validate_record_mapping_against_records,
)
from ontofill.refiner import MemorySilverStore, refine_observations


def _ontology() -> dict:
    return {
        "primary_class": "contract",
        "classes": [
            {
                "id": "contract",
                "identifier_property": "contract_id",
                "title_property": "contract_id",
            },
            {
                "id": "vendor",
                "identifier_property": "vendor_id",
                "title_property": "vendor_name",
            },
        ],
        "properties": [
            {"id": "contract_id", "domain": "contract", "datatype": "string"},
            {"id": "amount", "domain": "contract", "datatype": "number"},
            {"id": "contract_vendor_id", "domain": "contract", "datatype": "string"},
            {"id": "vendor_id", "domain": "vendor", "datatype": "string"},
            {"id": "vendor_name", "domain": "vendor", "datatype": "string"},
        ],
        "relations": [
            {
                "id": "contract_vendor",
                "label": "has vendor",
                "domain": "contract",
                "range": "vendor",
                "symmetric": False,
                "match": {
                    "op": "same_value",
                    "domain_property": "contract_vendor_id",
                    "range_property": "vendor_id",
                },
            }
        ],
    }


def _mapping() -> dict:
    return {
        "classes": [
            {
                "class_id": "contract",
                "collection_path": "/contracts/*",
                "identifier_property": "contract_id",
                "properties": [
                    {"property_id": "contract_id", "path": "/id"},
                    {"property_id": "amount", "path": "/amount"},
                    {"property_id": "contract_vendor_id", "path": "/vendors/*/id"},
                ],
            },
            {
                "class_id": "vendor",
                "collection_path": "/organizations/*",
                "identifier_property": "vendor_id",
                "properties": [
                    {"property_id": "vendor_id", "path": "/id"},
                    {"property_id": "vendor_name", "path": "/name"},
                ],
            },
        ]
    }


def _records() -> tuple[dict, ...]:
    return (
        {
            "row_number": 1,
            "path": "/release/0",
            "value": {
                "contracts": [{"id": "C-1", "amount": 42, "vendors": [{"id": "V-1"}]}],
                "organizations": [{"id": "V-1", "name": "North Star LLC"}],
            },
        },
    )


def _tdd() -> dict:
    return {
        "target_fields": ["contract_id", "amount", "vendor_name"],
        "target_entities": ["contract", "vendor"],
    }


def _mapping_decision() -> RecordedDecisionClient:
    return RecordedDecisionClient({"phase5.map_records": [_mapping()]})


def test_nested_mapping_preserves_instance_paths_and_relation_match_values() -> None:
    ontology = _ontology()
    mapping = _mapping()
    records = _records()

    validate_record_mapping(mapping, ontology, _tdd())
    validate_record_mapping_against_records(mapping, records, ontology, _tdd())
    instances = extract_record_instances(records, mapping)

    assert [(item.class_id, item.identifier) for item in instances] == [
        ("contract", "C-1"),
        ("vendor", "V-1"),
    ]
    values = {
        item.class_id: {field.property_id: field for field in item.fields} for item in instances
    }
    assert values["contract"]["contract_vendor_id"].value == "V-1"
    assert values["vendor"]["vendor_id"].value == "V-1"
    assert values["contract"]["amount"].selector == "/release/0/contracts/0/amount"
    assert values["vendor"]["vendor_name"].selector == "/release/0/organizations/0/name"


def test_model_schema_and_mapping_reject_unknown_paths_or_properties() -> None:
    ontology = _ontology()
    schema = record_mapping_model_schema(ontology, _tdd())
    allowed_classes = schema["properties"]["classes"]["items"]["properties"]["class_id"]["enum"]
    assert allowed_classes == ["contract", "vendor"]

    invalid = _mapping()
    invalid["classes"][0]["properties"][0]["path"] = "/id/~2bad"
    with pytest.raises(ValueError, match="invalid JSON Pointer escaping"):
        validate_record_mapping(invalid, ontology, _tdd())

    invalid = _mapping()
    invalid["classes"][1]["properties"][0]["property_id"] = "amount"
    with pytest.raises(ValueError, match="outside its class"):
        validate_record_mapping(invalid, ontology, _tdd())


def test_multi_valued_relation_path_fails_closed() -> None:
    records = _records()
    records[0]["value"]["contracts"][0]["vendors"].append({"id": "V-2"})

    with pytest.raises(ValueError, match="relation property .* is multi-valued"):
        validate_record_mapping_against_records(_mapping(), records, _ontology(), _tdd())


def test_shape_signature_has_no_source_values() -> None:
    encoded = json.dumps(record_shape(_records()), sort_keys=True)

    assert "North Star LLC" not in encoded
    assert "V-1" not in encoded
    assert "organizations" in encoded


def test_nested_p5_mapping_reuses_versioned_config_with_new_run_receipts(tmp_path) -> None:
    case_dir = tmp_path / "case"
    (case_dir / "01-scope").mkdir(parents=True)
    (case_dir / "04-local/source-test__objective-test").mkdir(parents=True)
    (case_dir / "01-scope/prd.json").write_text('{"goal":"records"}', encoding="utf-8")
    (case_dir / "04-local/source-test__objective-test/local-prd.json").write_text(
        '{"goal":"contract records"}', encoding="utf-8"
    )
    tdd = {**_tdd(), "local_prd_path": "04-local/source-test__objective-test/local-prd.json"}
    objective = {
        "source_id": "source-test",
        "id": "objective-test",
        "source_type": "public_dataset",
    }
    ontology = _ontology()
    records = _records()
    page = {"screenshot_key": "sha256:" + "c" * 64}
    store = MemorySilverStore()
    first_decision = _mapping_decision()
    first_provenance = generated_by(first_decision)

    first = _execute_nested_records(
        case_dir=case_dir,
        objective=objective,
        ontology=ontology,
        tdd=tdd,
        records=records,
        parsed_format="jsonl",
        bronze_key="sha256:" + "a" * 64,
        evidence_url="https://data.example.test/release.jsonl.gz",
        page=page,
        run_id="mock-first",
        decision=first_decision,
        store=store,
        provenance=first_provenance,
        parent_step_id="step:download-first",
        feed=None,
    )
    artifact_path = next((case_dir / "05-execute/macros").glob("*-records.json"))
    artifact_text = artifact_path.read_text(encoding="utf-8")
    assert "North Star LLC" not in artifact_text
    assert len(first.observations) == 5
    assert all(item.evidence["selector"].startswith("/release/0/") for item in first.observations)
    assert all(item.run_id == "mock-first" for item in first.observations)
    refinement = refine_observations(
        first.observations, ontology=ontology, generated_by=first_provenance
    )
    contract = next(item for item in refinement.entities if item["class"] == "contract")
    vendor = next(item for item in refinement.entities if item["class"] == "vendor")
    assert contract["links"] == [
        {
            "property": "contract_vendor",
            "target": vendor["id"],
            "via_value_id": contract["properties"]["contract_vendor_id"]["value_id"],
        }
    ]

    restarted_decision = RecordedDecisionClient({})
    restarted = _execute_nested_records(
        case_dir=case_dir,
        objective=objective,
        ontology=ontology,
        tdd=tdd,
        records=records,
        parsed_format="jsonl",
        bronze_key="sha256:" + "a" * 64,
        evidence_url="https://data.example.test/release.jsonl.gz",
        page=page,
        run_id="mock-restarted",
        decision=restarted_decision,
        store=store,
        provenance=generated_by(restarted_decision),
        parent_step_id="step:download-restarted",
        feed=None,
    )

    assert restarted_decision.call_log == []
    assert len(restarted.observations) == 5
    assert all(item.run_id == "mock-restarted" for item in restarted.observations)
    assert all(step["run_id"] == "mock-restarted" for step in restarted.trace)
    mapping_steps = [
        step for step in restarted.trace if step["requested"].get("tool") == "record.map"
    ]
    assert len(mapping_steps) == 1
    assert mapping_steps[0]["executed"]["replay"] is True


def test_changed_prd_invalidates_nested_mapping(tmp_path) -> None:
    case_dir = tmp_path / "case"
    (case_dir / "01-scope").mkdir(parents=True)
    (case_dir / "04-local/source-test__objective-test").mkdir(parents=True)
    prd_path = case_dir / "01-scope/prd.json"
    prd_path.write_text('{"goal":"first"}', encoding="utf-8")
    local_prd_path = case_dir / "04-local/source-test__objective-test/local-prd.json"
    local_prd_path.write_text('{"goal":"same"}', encoding="utf-8")
    tdd = {**_tdd(), "local_prd_path": local_prd_path.relative_to(case_dir).as_posix()}
    kwargs = {
        "case_dir": case_dir,
        "objective": {
            "source_id": "source-test",
            "id": "objective-test",
            "source_type": "public_dataset",
        },
        "ontology": _ontology(),
        "tdd": tdd,
        "records": _records(),
        "parsed_format": "jsonl",
        "bronze_key": "sha256:" + "b" * 64,
        "evidence_url": "https://data.example.test/release.jsonl.gz",
        "page": {"screenshot_key": "sha256:" + "c" * 64},
        "store": MemorySilverStore(),
        "parent_step_id": "step:download",
        "feed": None,
    }
    first_decision = _mapping_decision()
    _execute_nested_records(
        **kwargs,
        run_id="mock-first",
        decision=first_decision,
        provenance=generated_by(first_decision),
    )
    prd_path.write_text('{"goal":"changed"}', encoding="utf-8")
    changed_decision = _mapping_decision()
    changed = _execute_nested_records(
        **kwargs,
        run_id="mock-changed",
        decision=changed_decision,
        provenance=generated_by(changed_decision),
    )

    assert len(changed_decision.call_log) == 1
    mapping_steps = [
        step for step in changed.trace if step["requested"].get("tool") == "record.map"
    ]
    assert len(mapping_steps) == 1
    assert mapping_steps[0]["executed"]["replay"] is False


def test_execute_objective_dispatches_nested_parse_records(monkeypatch, tmp_path) -> None:
    case_dir = tmp_path / "case"
    (case_dir / "01-scope").mkdir(parents=True)
    (case_dir / "04-local/source-test__objective-test").mkdir(parents=True)
    (case_dir / "01-scope/prd.json").write_text('{"goal":"records"}', encoding="utf-8")
    local_prd_path = case_dir / "04-local/source-test__objective-test/local-prd.json"
    local_prd_path.write_text('{"goal":"contract records"}', encoding="utf-8")
    lake = FileLake(tmp_path / "lake")
    document_key = lake.put_bytes(b"bounded fake JSONL bytes")
    screenshot_key = lake.put_bytes(b"synthetic screenshot")
    decision = _mapping_decision()
    provenance = generated_by(decision)

    class ParsedRecords:
        def __init__(self):
            self.records = _records()
            self.format = "jsonl"
            self.truncated = False
            self.trace = ()
            self.job_record = {"proof": {"kind": "fake-parse"}}

        def as_parsed_file(self):
            return ParsedFile("jsonl", ())

    def parse_bronze(_lake, key, *, format, **_kwargs):
        assert key == document_key
        assert format == "jsonl"
        return ParsedRecords()

    monkeypatch.setattr("ontofill.phases.p5_execute.phase.parse_bronze", parse_bronze)

    def capture(url, **_kwargs):
        assert url == "https://data.example.test/release.jsonl.gz"
        return {
            "url": url,
            "document_key": document_key,
            "document_content_type": "application/x-ndjson",
            "status": 200,
            "screenshot_key": screenshot_key,
            "trace": [
                {
                    "step_id": "step:page-capture",
                    "run_id": "mock-dispatch",
                    "phase": 5,
                    "source_id": "source-test",
                    "objective_id": "objective-test",
                    "tdd_path": "04-local/source-test__objective-test/tdd.json",
                    "mode": "D0",
                    "observed": {"url": url},
                    "requested": {"tool": "source.capture"},
                    "executed": {"captured": True},
                    "evaluated": {"status": "ok"},
                    "parent_step_id": None,
                    "value_ids": [],
                    "ts": "2026-09-27T12:00:00+00:00",
                    "generated_by": provenance,
                }
            ],
        }

    result = execute_objective(
        case_dir=case_dir,
        objective={
            "source_id": "source-test",
            "id": "objective-test",
            "source_url": "https://data.example.test/release.jsonl.gz",
            "source_type": "public_dataset",
        },
        ontology=_ontology(),
        tdd={
            **_tdd(),
            "local_prd_path": local_prd_path.relative_to(case_dir).as_posix(),
            "allowed_domains": ["data.example.test"],
        },
        lake=lake,
        run_id="mock-dispatch",
        decision=decision,
        store=MemorySilverStore(),
        provenance=provenance,
        capture=capture,
    )

    assert result.format == "jsonl"
    assert len(result.observations) == 5
    assert all(item.evidence["format"] == "jsonl" for item in result.observations)
    assert any(step["requested"].get("tool") == "record.map" for step in result.trace)
