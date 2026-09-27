"""R22 P1: bounded schema and semantic PRD repair before checkpoint writes."""

from __future__ import annotations

import json

import pytest

from ontofill.case.checkpoints import require_approval
from ontofill.inference import ModelValidationExhausted, RecordedDecisionClient
from ontofill.phases.p1_scope.phase import PrdDraftUnavailable, draft_prd
from tests.approval_support import bind_approval


def _prd() -> dict:
    return {
        "version": "1",
        "brief_path": "brief.md",
        "personas": [{"id": "reader", "description": "Reads public records"}],
        "jobs_to_be_done": [
            {"id": "find", "persona_id": "reader", "description": "Find useful records"}
        ],
        "requirements": [
            {"id": "evidence", "job_id": "find", "description": "Show source evidence"}
        ],
        "constraints": ["Use public read-only sources"],
        "non_goals": ["Do not infer private data"],
        "definition_of_done": [
            {
                "id": "record-count",
                "metric": "records",
                "operator": ">=",
                "target": 1,
                "basis": "proposed",
                "rationale": "One record demonstrates the definition.",
                "feasibility": "One synthetic record fits the available run budget.",
            }
        ],
        "authority_policy": {
            "jurisdiction": "Example City",
            "trusted_publishers": [
                {
                    "kind": "City archive",
                    "tier": "primary",
                    "jurisdiction": "Example City",
                    "domains": ["archive.example.test"],
                    "rationale": "Official local public publisher.",
                }
            ],
            "unknown_source_action": "review",
        },
    }


def _case(case_dir) -> None:
    (case_dir / "brief.md").write_text("Find public records in Example City.", encoding="utf-8")


def _invalid_authority_prd() -> dict:
    invalid = _prd()
    invalid["authority_policy"]["trusted_publishers"] = []
    return invalid


def _client(responses: list[dict]) -> RecordedDecisionClient:
    return RecordedDecisionClient({"phase1.prd": responses})


def test_invalid_prd_schema_retries_with_screened_validator_feedback(tmp_path) -> None:
    _case(tmp_path)
    invalid = _prd()
    invalid.pop("authority_policy")
    decision = _client([invalid, _prd()])

    document = draft_prd(tmp_path, decision)

    reason = "'authority_policy' is a required property"
    assert document["brief_path"] == "brief.md"
    assert len(decision.calls) == 2
    assert reason in decision.calls[1][1]
    assert f"<page_content>{reason}</page_content>" in decision.calls[1][1]
    assert decision.call_log[0]["reason"] == reason
    assert decision.call_log[1]["status"] == "ok"
    assert (tmp_path / "01-scope/prd.json").exists()


def test_invalid_authority_semantics_retry_with_exact_feedback(tmp_path) -> None:
    _case(tmp_path)
    decision = _client([_invalid_authority_prd(), _prd()])

    document = draft_prd(tmp_path, decision)

    reason = "Authority policy needs a primary publisher in the case jurisdiction"
    assert document["authority_policy"]["trusted_publishers"]
    assert len(decision.calls) == 2
    assert f"<page_content>{reason}</page_content>" in decision.calls[1][1]
    assert decision.call_log[0]["status"] == "validation_failed"
    assert decision.call_log[0]["reason"] == reason
    assert decision.call_log[1]["status"] == "ok"


def test_recorded_mock_preview_keeps_authority_gap_open(tmp_path) -> None:
    _case(tmp_path)
    decision = _client([_invalid_authority_prd()])

    document = draft_prd(tmp_path, decision, mock_preview=True)

    assert document["authority_policy"]["trusted_publishers"] == []
    assert any("primary publisher" in issue for issue in document["open_issues"])
    assert len(decision.calls) == 1
    assert (tmp_path / "01-scope/prd.json").exists()


def test_mock_preview_cache_fingerprint_does_not_match_strict_mode(tmp_path) -> None:
    _case(tmp_path)
    preview_decision = _client([_prd()])
    draft_prd(tmp_path, preview_decision, mock_preview=True)
    preview_fingerprint = (tmp_path / "01-scope/prd.input.sha256").read_text(encoding="utf-8")

    strict_decision = _client([_prd()])
    draft_prd(tmp_path, strict_decision)

    strict_fingerprint = (tmp_path / "01-scope/prd.input.sha256").read_text(encoding="utf-8")
    assert len(strict_decision.calls) == 1
    assert strict_fingerprint != preview_fingerprint


def test_three_invalid_prds_pause_without_archiving_denial_or_writing(tmp_path) -> None:
    _case(tmp_path)
    old = draft_prd(tmp_path, _client([_prd()]))
    scope = tmp_path / "01-scope"
    assert not require_approval(
        scope,
        phase=1,
        checkpoint="prd",
        artifact_paths=["01-scope/prd.json"],
        generated_by=old["generated_by"],
    )
    denial = {
        "approver": "Test Reviewer",
        "date": "2026-09-26",
        "checkpoint": "prd",
        "decision": "deny",
        "reason": "The publisher needs to be in scope.",
    }
    marker_path = scope / "APPROVED"
    marker_path.write_text(
        json.dumps(bind_approval(tmp_path, ["01-scope/prd.json"], denial)), encoding="utf-8"
    )
    names = ("prd.json", "prd.md", "prd.input.sha256", "APPROVAL_PENDING.md", "APPROVED")
    before = {name: (scope / name).read_bytes() for name in names}
    decision = _client([_invalid_authority_prd()] * 3)

    with pytest.raises(PrdDraftUnavailable) as raised:
        draft_prd(tmp_path, decision)

    assert raised.value.purpose == "phase1.prd"
    assert raised.value.attempts == 3
    assert "Authority policy needs a primary publisher" in raised.value.reason
    assert len(decision.call_log) == 3
    assert all(call["status"] == "validation_failed" for call in decision.call_log)
    assert {name: (scope / name).read_bytes() for name in names} == before
    assert not (scope / "revisions").exists()


def test_typed_model_exhaustion_is_wrapped_as_prd_pause(tmp_path) -> None:
    _case(tmp_path)

    class ExhaustedDecision:
        backend = "recorded"
        model = "synthetic"

        def __init__(self) -> None:
            self.call_log = [
                {"purpose": "phase1.prd", "attempt": attempt} for attempt in range(1, 4)
            ]

        def complete_json(self, purpose, prompt, schema):
            raise ModelValidationExhausted(purpose, "'authority_policy' is a required property", 3)

    decision = ExhaustedDecision()

    with pytest.raises(PrdDraftUnavailable) as raised:
        draft_prd(tmp_path, decision)

    assert raised.value.purpose == "phase1.prd"
    assert raised.value.attempts == 3
    assert raised.value.reason == "'authority_policy' is a required property"
    assert len(decision.call_log) == 3
    assert not (tmp_path / "01-scope/prd.json").exists()
