"""R24: only authority-valid PRDs can enter the approval checkpoint."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from ontofill.case.checkpoints import require_approval
from ontofill.phase_loop import LoopResult, PhaseLoop
from ontofill.phases.p1_scope.phase import (
    PrdDraftUnavailable,
    _authority_policy_check,
    draft_prd,
)
from tests.approval_support import bind_approval
from tests.test_r22_p1 import _prd


class SectionDecision:
    backend = "vultr"
    model = "synthetic-vultr"

    def __init__(self, drafts: list[dict]) -> None:
        self.drafts = drafts
        self.calls: list[tuple[str, str]] = []
        self.call_log: list[dict] = []

    def complete_json(self, purpose: str, prompt: str, schema: dict) -> dict:
        assert purpose == "phase1.prd.section"
        attempt = len(self.calls) // 3
        draft = self.drafts[attempt]
        self.calls.append((purpose, prompt))
        self.call_log.append(
            {
                "purpose": purpose,
                "status": "ok",
                "usage": {
                    "backend": "vultr",
                    "model": self.model,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "est_usd": 0.0,
                },
            }
        )
        return {name: deepcopy(draft[name]) for name in schema["required"]}

    def review_json(self, _purpose: str, _artifact: dict, _prompt: str) -> dict:
        return {"accepted": True, "reason": "Synthetic critic accepted"}


def _case(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Find public records in Example City.", encoding="utf-8")


def _tier_in_rationale_only() -> dict:
    draft = _prd()
    publisher = draft["authority_policy"]["trusted_publishers"][0]
    publisher["tier"] = "secondary"
    publisher["rationale"] = "Tier=primary; official local public publisher."
    return draft


def _approve(case_dir, artifact: dict) -> None:
    scope = case_dir / "01-scope"
    assert not require_approval(
        scope,
        phase=1,
        checkpoint="prd",
        artifact_paths=["01-scope/prd.json"],
        generated_by=artifact["generated_by"],
        case_dir=case_dir,
    )
    approval = bind_approval(
        case_dir,
        ["01-scope/prd.json"],
        {
            "approver": "Test reviewer",
            "date": "2026-09-27",
            "checkpoint": "prd",
            "decision": "approve",
        },
    )
    (scope / "APPROVED").write_text(json.dumps(approval), encoding="utf-8")


def test_policy_error_retries_before_checkpoint_and_approved_prd_is_reused(tmp_path) -> None:
    _case(tmp_path)
    decision = SectionDecision([_tier_in_rationale_only(), _prd()])

    document = draft_prd(tmp_path, decision, run_id="run-r24")

    assert _authority_policy_check(document, []).passed
    assert len(decision.calls) == 6
    assert "Authority policy needs a primary publisher" in decision.calls[3][1]
    assert decision.call_log[2]["status"] == "validation_failed"
    _approve(tmp_path, document)
    scope = tmp_path / "01-scope"
    artifact_before = (scope / "prd.json").read_bytes()
    approval_before = (scope / "APPROVED").read_bytes()
    empty_decision = SectionDecision([])

    reused = draft_prd(tmp_path, empty_decision, run_id="run-r24-next")

    assert reused == document
    assert empty_decision.calls == []
    assert (scope / "prd.json").read_bytes() == artifact_before
    assert (scope / "APPROVED").read_bytes() == approval_before
    assert not list(scope.glob("APPROVED.stale.*"))
    assert require_approval(
        scope,
        phase=1,
        checkpoint="prd",
        artifact_paths=["01-scope/prd.json"],
        generated_by=reused["generated_by"],
        case_dir=tmp_path,
    )


def test_legacy_approved_policy_failure_pauses_without_staling_approval(tmp_path) -> None:
    _case(tmp_path)
    document = draft_prd(tmp_path, SectionDecision([_prd()]), run_id="run-r24")
    scope = tmp_path / "01-scope"
    invalid = deepcopy(document)
    invalid["authority_policy"]["trusted_publishers"][0].update(
        tier="secondary", rationale="Tier=primary; official local public publisher."
    )
    (scope / "prd.json").write_text(json.dumps(invalid) + "\n", encoding="utf-8")
    _approve(tmp_path, invalid)
    names = ("prd.json", "prd.md", "prd.input.sha256", "APPROVAL_PENDING.md", "APPROVED")
    before = {name: (scope / name).read_bytes() for name in names}
    decision = SectionDecision([_prd()])

    with pytest.raises(PrdDraftUnavailable, match="approved PRD fails authority policy"):
        draft_prd(tmp_path, decision, run_id="run-r24-next")

    assert decision.calls == []
    assert {name: (scope / name).read_bytes() for name in names} == before
    assert not list(scope.glob("APPROVED.stale.*"))


def test_final_loop_artifact_gets_policy_feedback_before_any_write(tmp_path, monkeypatch) -> None:
    _case(tmp_path)
    invalid = _tier_in_rationale_only()
    invalid["generated_by"] = {
        "backend": "vultr",
        "model": "synthetic-vultr",
        "at": "2026-09-27T00:00:00+00:00",
    }
    decision = SectionDecision([invalid, _prd()])
    monkeypatch.setattr(
        PhaseLoop,
        "run",
        lambda self, **_stages: LoopResult(invalid, 2, "max_iterations", 0.0, ()),
    )

    document = draft_prd(tmp_path, decision, run_id="run-r24")

    assert _authority_policy_check(document, []).passed
    assert len(decision.calls) == 6
    assert "Authority policy needs a primary publisher" in decision.calls[0][1]
    assert "Authority policy needs a primary publisher" in decision.calls[3][1]
    assert decision.call_log[2]["status"] == "validation_failed"
    assert json.loads((tmp_path / "01-scope/prd.json").read_text()) == document
