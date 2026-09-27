"""Human decisions apply only to the exact raw artifacts the console reviewed."""

from __future__ import annotations

import json

import pytest

from ontofill.case.actions import action_gate
from ontofill.case.checkpoints import (
    ApprovalArtifactMismatch,
    checkpoint_revisions,
    require_approval,
)
from ontofill.contracts import validate_document
from tests.approval_support import bind_approval

VULTR = {"backend": "vultr", "model": "synthetic", "at": "2026-09-26T00:00:00Z"}


def _snapshot(case_dir):
    return {
        p.relative_to(case_dir).as_posix(): p.read_bytes()
        for p in case_dir.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize("decision", ["approve", "deny"])
def test_changed_prd_rejects_review_without_writing(tmp_path, decision) -> None:
    scope = tmp_path / "01-scope"
    scope.mkdir()
    (scope / "prd.json").write_bytes(b'{"version":1}\n')
    marker = bind_approval(
        tmp_path,
        ["01-scope/prd.json"],
        {
            "approver": "Reviewer",
            "date": "2026-09-26",
            "checkpoint": "prd",
            "decision": decision,
            **({"reason": "Needs revision"} if decision == "deny" else {}),
            "run_id": "run-synthetic",
        },
    )
    validate_document("approved", marker)
    (scope / "APPROVED").write_text(json.dumps(marker), encoding="utf-8")
    (scope / "prd.json").write_bytes(b'{"version":2}\n')
    before = _snapshot(tmp_path)

    with pytest.raises(ApprovalArtifactMismatch, match="different artifact version"):
        checkpoint_revisions(scope, "prd", ["prd.json"], case_dir=tmp_path)
    with pytest.raises(ApprovalArtifactMismatch, match="different artifact version"):
        require_approval(
            scope,
            phase=1,
            checkpoint="prd",
            artifact_paths=["01-scope/prd.json"],
            generated_by=VULTR,
            case_dir=tmp_path,
        )
    assert _snapshot(tmp_path) == before


def test_approval_must_name_exact_reviewed_paths(tmp_path) -> None:
    scope = tmp_path / "01-scope"
    scope.mkdir()
    (scope / "prd.json").write_bytes(b"{}\n")
    (scope / "prd.md").write_bytes(b"# Review\n")
    marker = bind_approval(
        tmp_path,
        ["01-scope/prd.md"],
        {"approver": "Reviewer", "date": "2026-09-26", "checkpoint": "prd"},
    )
    (scope / "APPROVED").write_text(json.dumps(marker), encoding="utf-8")
    before = _snapshot(tmp_path)
    with pytest.raises(ApprovalArtifactMismatch):
        require_approval(
            scope,
            phase=1,
            checkpoint="prd",
            artifact_paths=["01-scope/prd.json"],
            generated_by=VULTR,
            case_dir=tmp_path,
        )
    assert _snapshot(tmp_path) == before


def test_stale_action_approval_cannot_execute_or_rewrite_request(tmp_path) -> None:
    kwargs = {
        "action": "form.submit",
        "risk_tier": "HIGH",
        "job_id": "job-1",
        "screenshot_key": "sha256:" + "a" * 64,
        "generated_by": VULTR,
    }
    assert action_gate(tmp_path, "request-1", **kwargs)["outcome"] == "pending_approval"
    relative = "05-actions/request-1/request.json"
    marker = bind_approval(
        tmp_path,
        [relative],
        {
            "approver": "Reviewer",
            "date": "2026-09-26",
            "checkpoint": "action",
            "decision": "approve",
        },
    )
    directory = tmp_path / "05-actions/request-1"
    (directory / "APPROVED").write_text(json.dumps(marker), encoding="utf-8")
    request = directory / "request.json"
    request.write_bytes(request.read_bytes() + b" ")
    before = _snapshot(tmp_path)
    with pytest.raises(ApprovalArtifactMismatch, match="different artifact version"):
        action_gate(tmp_path, "request-1", **kwargs)
    assert _snapshot(tmp_path) == before
