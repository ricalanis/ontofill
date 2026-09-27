"""All three definition checkpoints keep denied drafts and reviewer reasons."""

import json

import pytest

from ontofill.case.checkpoints import checkpoint_revisions
from tests.approval_support import bind_approval


@pytest.mark.parametrize(
    ("checkpoint", "artifact"),
    [("prd", "prd.json"), ("factors", "factors.json"), ("ontology", "ontology.json")],
)
def test_denial_archives_artifact_and_approval(tmp_path, checkpoint, artifact) -> None:
    directory = tmp_path / checkpoint
    directory.mkdir()
    (directory / artifact).write_text('{"draft":1}', encoding="utf-8")
    (directory / "APPROVAL_PENDING.md").write_text("review", encoding="utf-8")
    marker = {
        "approver": "Reviewer",
        "date": "2026-09-26",
        "checkpoint": checkpoint,
        "decision": "deny",
        "reason": "The requested threshold has no basis.",
    }
    marker = bind_approval(tmp_path, [f"{checkpoint}/{artifact}"], marker)
    (directory / "APPROVED").write_text(json.dumps(marker), encoding="utf-8")
    revisions = checkpoint_revisions(directory, checkpoint, [artifact], case_dir=tmp_path)
    assert revisions == [
        {
            "n": 1,
            "decision": "deny",
            "reason": marker["reason"],
            "approver": "Reviewer",
            "date": "2026-09-26",
        }
    ]
    assert (directory / "revisions/1" / artifact).read_text() == '{"draft":1}'
    assert not (directory / "APPROVED").exists()
    assert checkpoint_revisions(directory, checkpoint, [artifact], case_dir=tmp_path) == revisions
