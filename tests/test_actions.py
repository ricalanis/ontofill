"""High-risk actions pause at an approval bound to one screenshot and job."""

from __future__ import annotations

import json

import pytest
import yaml
from jsonschema import ValidationError

from ontofill.case.actions import action_gate
from ontofill.contracts import validate_document

VULTR = {"backend": "vultr", "model": "synthetic", "at": "2026-09-26T00:00:00Z"}
RECORDED = {"backend": "recorded", "model": "synthetic", "at": "2026-09-26T00:00:00Z"}
SCREENSHOT = "sha256:" + "a" * 64


def request(case_dir, *, generated_by=VULTR):
    return action_gate(
        case_dir,
        "request-1",
        action="form.submit",
        risk_tier="HIGH",
        job_id="job-1",
        screenshot_key=SCREENSHOT,
        generated_by=generated_by,
    )


def test_high_risk_action_waits_for_explicit_approval_and_denial(tmp_path) -> None:
    assert (
        action_gate(
            tmp_path,
            "safe-1",
            action="page.read",
            risk_tier="SAFE",
            job_id="job-1",
            screenshot_key=SCREENSHOT,
            generated_by=VULTR,
        )["outcome"]
        == "allowed"
    )
    assert not (tmp_path / "05-actions").exists()
    pending = request(tmp_path)
    assert pending["outcome"] == "pending_approval"
    directory = tmp_path / "05-actions/request-1"
    metadata = yaml.safe_load((directory / "APPROVAL_PENDING.md").read_text().split("---", 2)[1])
    validate_document("approval-pending", metadata)
    assert metadata["screenshot_key"] == SCREENSHOT
    assert metadata["intended_action"] == "form.submit"
    assert metadata["job_id"] == "job-1"
    (directory / "APPROVED").write_text(
        json.dumps(
            {
                "approver": "Reviewer",
                "date": "2026-09-26",
                "checkpoint": "action",
                "decision": "deny",
                "reason": "Insufficient review",
            }
        )
    )
    assert request(tmp_path)["outcome"] == "denied"
    (directory / "APPROVED").write_text(
        json.dumps(
            {
                "approver": "Reviewer",
                "date": "2026-09-26",
                "checkpoint": "action",
                "decision": "approve",
            }
        )
    )
    assert request(tmp_path)["outcome"] == "allowed"
    recorded_case = tmp_path / "recorded-case"
    assert request(recorded_case, generated_by=RECORDED)["outcome"] == "pending_approval"
    recorded_approval = recorded_case / "05-actions/request-1/APPROVED"
    recorded_approval.write_text(
        json.dumps(
            {
                "approver": "Reviewer",
                "date": "2026-09-26",
                "checkpoint": "action",
                "decision": "approve",
            }
        )
    )
    assert request(recorded_case, generated_by=RECORDED)["outcome"] == "pending_approval"
    with pytest.raises(ValueError, match="different content"):
        action_gate(
            tmp_path,
            "request-1",
            action="other.submit",
            risk_tier="HIGH",
            job_id="job-1",
            screenshot_key=SCREENSHOT,
            generated_by=VULTR,
        )


def test_track_trace_events_validate_exact_contract_shapes() -> None:
    base = {
        "step_id": "step-1",
        "run_id": "run-1",
        "phase": 5,
        "source_id": "source-1",
        "objective_id": "objective-1",
        "tdd_path": "04-local/tdd.json",
        "mode": "S1",
        "observed": {},
        "requested": {},
        "executed": {},
        "evaluated": {},
        "parent_step_id": None,
        "value_ids": [],
        "ts": "2026-09-26T00:00:00Z",
        "generated_by": VULTR,
    }
    validate_document(
        "trace-step",
        {
            **base,
            "event": "verify",
            "verify": {
                "goal": "Page loaded",
                "verdict": "achieved",
                "confidence": 0.9,
                "backend": "vultr",
                "model": "qwen3.8-27b",
                "screenshot_key": SCREENSHOT,
            },
        },
    )
    validate_document(
        "trace-step",
        {
            **base,
            "event": "repair",
            "repair": {
                "attempt": 1,
                "max_attempts": 2,
                "code_key": SCREENSHOT,
                "result": "fail",
                "stderr_excerpt": "assertion failed",
                "diff_key": SCREENSHOT,
                "test": {"pages": 1, "precision": 0.5, "coverage": 0.8},
            },
        },
    )
    validate_document(
        "trace-step",
        {
            **base,
            "event": "action_gate",
            "gate": {
                "action": "form.submit",
                "risk_tier": "HIGH",
                "decided_by": "code",
                "outcome": "pending_approval",
                "approval_path": "05-actions/request-1/APPROVAL_PENDING.md",
            },
        },
    )
    validate_document(
        "trace-step", {**base, "event": "limit_kill", "evaluated": {"reason": "timeout"}}
    )
    screened = {
        "flagged": True,
        "jev_choice": None,
        "jev_confidence": None,
        "safety_verdict": "unavailable",
        "reason": "Safety confirmation unavailable",
        "by": "gateway",
    }
    validate_document(
        "trace-step",
        {**base, "session_id": "session-1", "event": "quarantine", "screen": screened},
    )
    with pytest.raises(ValidationError):
        validate_document("trace-step", {**base, "screen": screened})
