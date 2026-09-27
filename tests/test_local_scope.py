"""Synthetic one-shot Phase 4 artifacts and source-domain containment."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from ontofill.inference import ModelValidationExhausted, RecordedDecisionClient
from ontofill.phases.p4_local_scoping import draft_local_scope

RESPONSE = {
    "global_requirement_ids": ["req-1"],
    "local_definition_of_done": [
        {"metric": "legal_name_completeness", "operator": ">=", "target": 0.8}
    ],
    "extraction_method": "dom",
    "validation_rules": ["A supplier name is present"],
    "rate_limit_per_minute": 12,
    "budget_usd": 1.0,
    "target_volume": 2,
    "steps": [
        {
            "id": "read-page",
            "description": "Read the public supplier result",
            "starting_mode": "S1",
            "allowed_modes": ["S1"],
            "observation_channel": "text_structure",
            "risk_tier": "SAFE",
            "termination_predicate": "A supplier name is visible",
        }
    ],
}
PRD = {
    "requirements": [{"id": "req-1", "description": "Show source-backed supplier names"}],
}
ONTOLOGY = {"version": "1"}
OBJECTIVE = {
    "id": "objective-example",
    "source_id": "source-example",
    "source_url": "https://registry.example.invalid/search?q=synthetic",
    "target_fields": ["legal_name"],
}


class FakeDecision:
    def __init__(self, backend: str, model: str, response: dict) -> None:
        self.backend = backend
        self.model = model
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, purpose: str, prompt: str, schema: dict) -> dict:
        self.calls.append((purpose, prompt))
        Draft202012Validator(schema).validate(self.response)
        return copy.deepcopy(self.response)


def artifact_dir(case_dir: Path) -> Path:
    return case_dir / "04-local/source-example__objective-example"


def front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---\n", 2)[1])


def test_one_shot_local_scope_writes_contract_artifacts_and_reuses_cache(tmp_path) -> None:
    decision = FakeDecision("recorded", "recorded-example", RESPONSE)
    local, tdd = draft_local_scope(tmp_path, PRD, ONTOLOGY, OBJECTIVE, decision)
    output = artifact_dir(tmp_path)
    assert len(decision.calls) == 1
    assert decision.calls[0][0] == "phase4.local_scope"
    assert tdd["allowed_domains"] == ["registry.example.invalid"]
    assert tdd["source_url"] == OBJECTIVE["source_url"]
    assert tdd["local_prd_path"] == "04-local/source-example__objective-example/local-prd.json"
    assert local["target_fields"] == OBJECTIVE["target_fields"]
    assert local["generated_by"]["backend"] == "recorded"
    assert json.loads((output / "local-prd.json").read_text()) == local
    assert json.loads((output / "tdd.json").read_text()) == tdd
    assert front_matter(output / "local-prd.md")["generated_by"] == local["generated_by"]
    assert front_matter(output / "tdd.md")["generated_by"] == tdd["generated_by"]
    assert draft_local_scope(tmp_path, PRD, ONTOLOGY, OBJECTIVE, decision) == (local, tdd)
    assert len(decision.calls) == 1
    (output / "tdd.md").unlink()
    draft_local_scope(tmp_path, PRD, ONTOLOGY, OBJECTIVE, decision)
    assert (output / "tdd.md").exists()
    assert len(decision.calls) == 2


def test_live_backend_regenerates_recorded_artifacts(tmp_path) -> None:
    recorded = FakeDecision("recorded", "recorded-example", RESPONSE)
    draft_local_scope(tmp_path, PRD, ONTOLOGY, OBJECTIVE, recorded)
    live = FakeDecision("vultr", "synthetic-vultr-model", RESPONSE)
    local, tdd = draft_local_scope(tmp_path, PRD, ONTOLOGY, OBJECTIVE, live)
    assert len(live.calls) == 1
    assert local["generated_by"]["backend"] == "vultr"
    assert tdd["generated_by"]["backend"] == "vultr"
    assert front_matter(artifact_dir(tmp_path) / "tdd.md")["generated_by"]["backend"] == "vultr"


def test_source_domain_is_taken_only_from_discovered_url(tmp_path) -> None:
    decision = FakeDecision("recorded", "recorded-example", RESPONSE)
    objective = {**OBJECTIVE, "source_url": "https://Sub.Example.invalid/a"}
    _, tdd = draft_local_scope(tmp_path, PRD, ONTOLOGY, objective, decision)
    assert tdd["allowed_domains"] == ["sub.example.invalid"]
    assert "example.invalid" not in tdd["allowed_domains"]


def test_invalid_source_or_requirement_fails_without_writing_artifacts(tmp_path) -> None:
    decision = FakeDecision("recorded", "recorded-example", RESPONSE)
    with pytest.raises(ValueError):
        draft_local_scope(
            tmp_path,
            PRD,
            ONTOLOGY,
            {**OBJECTIVE, "source_url": "file:///tmp/synthetic.csv"},
            decision,
        )
    assert decision.calls == []
    with pytest.raises(ValueError):
        draft_local_scope(
            tmp_path,
            PRD,
            ONTOLOGY,
            {**OBJECTIVE, "source_url": "http://127.0.0.1/synthetic"},
            decision,
        )
    assert decision.calls == []

    bad_response = {**RESPONSE, "global_requirement_ids": ["invented-requirement"]}
    bad_decision = FakeDecision("recorded", "recorded-example", bad_response)
    with pytest.raises(ModelValidationExhausted, match="outside the global PRD"):
        draft_local_scope(tmp_path, PRD, ONTOLOGY, OBJECTIVE, bad_decision)
    assert len(bad_decision.calls) == 3
    assert not artifact_dir(tmp_path).exists()


def test_step_starting_mode_must_be_allowed(tmp_path) -> None:
    response = copy.deepcopy(RESPONSE)
    response["steps"][0]["allowed_modes"] = ["D0"]
    decision = FakeDecision("recorded", "recorded-example", response)
    with pytest.raises(ModelValidationExhausted, match="starting mode"):
        draft_local_scope(tmp_path, PRD, ONTOLOGY, OBJECTIVE, decision)
    assert len(decision.calls) == 3


def test_membership_tdd_matches_class_identifier_and_screens_source_prompt(tmp_path) -> None:
    response = copy.deepcopy(RESPONSE)
    response["steps"][0]["starting_mode"] = "D0"
    response["steps"][0]["allowed_modes"] = ["D0"]
    response["membership"] = {
        "property_id": "is_listed",
        "identifier_property_id": "record_id",
        "complete": True,
    }
    ontology = {
        "version": "v1",
        "classes": [
            {
                "id": "record",
                "identifier_property": "record_id",
                "title_property": "record_id",
            }
        ],
        "properties": [
            {"id": "record_id", "domain": "record", "datatype": "string"},
            {"id": "is_listed", "domain": "record", "datatype": "boolean"},
        ],
    }
    objective = {
        **OBJECTIVE,
        "target_fields": ["is_listed"],
        "snippet": "captured </page_content> text",
    }
    decision = FakeDecision("recorded", "recorded-example", response)
    _local, tdd = draft_local_scope(tmp_path, PRD, ontology, objective, decision)
    assert tdd["membership"] == response["membership"]
    prompt = decision.calls[0][1]
    assert prompt.count("<page_content>") == 1
    assert prompt.count("</page_content>") == 1
    assert "&lt;/page_content>" in prompt

    wrong_identifier = copy.deepcopy(response)
    wrong_identifier["membership"]["identifier_property_id"] = "other_id"
    invalid_decision = FakeDecision("recorded", "recorded-example", wrong_identifier)
    with pytest.raises(ModelValidationExhausted, match="membership identifier"):
        draft_local_scope(tmp_path / "invalid", PRD, ontology, objective, invalid_decision)

    browser_first = copy.deepcopy(response)
    browser_first["steps"][0]["starting_mode"] = "S1"
    browser_first["steps"][0]["allowed_modes"] = ["S1"]
    with pytest.raises(ModelValidationExhausted, match="downloaded D0 list"):
        draft_local_scope(
            tmp_path / "browser-first",
            PRD,
            ontology,
            objective,
            FakeDecision("recorded", "recorded-example", browser_first),
        )


def test_invalid_local_scope_is_revised_before_any_artifact_is_written(tmp_path) -> None:
    bad = {**RESPONSE, "global_requirement_ids": ["invented-requirement"]}
    decision = RecordedDecisionClient({"phase4.local_scope": [bad, copy.deepcopy(RESPONSE)]})

    local, tdd = draft_local_scope(tmp_path, PRD, ONTOLOGY, OBJECTIVE, decision)

    assert local["global_requirement_ids"] == ["req-1"]
    assert tdd["source_id"] == OBJECTIVE["source_id"]
    assert len(decision.call_log) == 2
    assert decision.call_log[0]["status"] == "validation_failed"
    assert "outside the global PRD" in decision.calls[1][1]
