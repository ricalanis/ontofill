"""R26d: PRD open issues reflect only the current draft review."""

from __future__ import annotations

import json

from ontofill.phases.p1_scope.phase import draft_prd
from tests.approval_support import bind_approval
from tests.test_r26c_prd_patch import PatchDecision, _case_with_v4_denial

RESOLVED_ISSUE = "Prior objection subject unchanged: dod2 basis lacks a human quote"
CURRENT_ISSUE = "The replacement procurement publisher still needs a resolvable domain"


def _case_with_stale_issue(case_dir) -> None:
    prior = _case_with_v4_denial(case_dir)
    prior["open_issues"] = [RESOLVED_ISSUE]
    prd_path = case_dir / "01-scope/prd.json"
    prd_path.write_text(json.dumps(prior) + "\n", encoding="utf-8")

    approval_path = case_dir / "01-scope/APPROVED"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval = bind_approval(case_dir, ["01-scope/prd.json"], approval)
    approval_path.write_text(json.dumps(approval) + "\n", encoding="utf-8")


def test_resolved_previous_objection_is_removed_from_revised_prd(tmp_path) -> None:
    _case_with_stale_issue(tmp_path)

    document = draft_prd(tmp_path, PatchDecision(), run_id="run-r26d-resolved")

    assert "open_issues" not in document
    persisted = json.loads((tmp_path / "01-scope/prd.json").read_text(encoding="utf-8"))
    assert "open_issues" not in persisted


def test_current_unresolved_objection_replaces_inherited_open_issue(tmp_path) -> None:
    _case_with_stale_issue(tmp_path)

    class CurrentObjection(PatchDecision):
        def review_json(self, _purpose: str, _artifact: dict, _prompt: str) -> dict:
            return {"accepted": False, "reason": CURRENT_ISSUE}

    document = draft_prd(tmp_path, CurrentObjection(), run_id="run-r26d-unresolved")

    assert document["open_issues"] == [CURRENT_ISSUE]
    persisted = json.loads((tmp_path / "01-scope/prd.json").read_text(encoding="utf-8"))
    assert persisted["open_issues"] == [CURRENT_ISSUE]
