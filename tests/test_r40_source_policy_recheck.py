"""A changed source policy cannot rewrite bytes before checking the human decision."""

from __future__ import annotations

import json

import pytest

from ontofill.case.checkpoints import ApprovalArtifactMismatch
from ontofill.phases.p3_fanout.authority import source_fingerprint
from ontofill.workflow import _source_review
from tests.approval_support import bind_approval


def _case(tmp_path):
    url = "https://records.example.test/catalog"
    primary = {
        "trusted_publishers": [
            {"kind": "Public records", "tier": "primary", "domains": ["records.example.test"]}
        ]
    }
    secondary = {
        "trusted_publishers": [
            {"kind": "Public records", "tier": "secondary", "domains": ["records.example.test"]}
        ]
    }
    fingerprint = source_fingerprint(
        url=url,
        title="Records catalog",
        snippet="Public record index",
        provider="synthetic",
        capture_key=None,
        authority_policy=primary,
    )
    objective = {
        "id": "objective-records",
        "source_id": "source-records",
        "source_url": url,
        "source_fingerprint": fingerprint,
    }
    directory = tmp_path / "03-fanout/sources/source-records"
    directory.mkdir(parents=True)
    candidate = directory / "candidate.json"
    candidate.write_text(
        json.dumps(
            {
                "source_id": objective["source_id"],
                "url": url,
                "title": "Records catalog",
                "snippet": "Public record index",
                "provider": "synthetic",
                "capture_key": None,
                "fingerprint": fingerprint,
                "authority": "auto",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "03-fanout/objectives.json").write_text(
        json.dumps({"objectives": [objective]}), encoding="utf-8"
    )
    marker = directory / "APPROVED"
    marker.write_text(
        json.dumps(
            bind_approval(
                tmp_path,
                ["03-fanout/sources/source-records/candidate.json"],
                {
                    "approver": "Reviewer",
                    "date": "2026-09-27",
                    "checkpoint": "source",
                    "source_fingerprint": fingerprint,
                },
            )
        ),
        encoding="utf-8",
    )
    return objective, secondary, candidate, marker


def test_stale_source_digest_refuses_policy_rewrite_without_any_write(tmp_path) -> None:
    objective, secondary, candidate, _marker = _case(tmp_path)
    candidate.write_bytes(candidate.read_bytes() + b" ")
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ApprovalArtifactMismatch, match="different artifact version"):
        _source_review(
            tmp_path,
            objective,
            {"backend": "vultr", "model": "synthetic", "at": "2026-09-27T00:00:00Z"},
            secondary,
        )

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_valid_source_approval_is_archived_before_new_policy_review(tmp_path) -> None:
    objective, secondary, candidate, marker = _case(tmp_path)
    old_candidate, old_marker = candidate.read_bytes(), marker.read_bytes()
    provenance = {"backend": "vultr", "model": "synthetic", "at": "2026-09-27T00:00:00Z"}

    approved, directory = _source_review(tmp_path, objective, provenance, secondary)

    assert not approved
    assert directory == candidate.parent
    assert not marker.exists()
    assert (directory / "APPROVAL_PENDING.md").exists()
    archives = list((directory / "revisions").glob("policy-*/candidate.json"))
    assert len(archives) == 1
    assert archives[0].read_bytes() == old_candidate
    assert (archives[0].parent / "APPROVED").read_bytes() == old_marker
    new_candidate = json.loads(candidate.read_text(encoding="utf-8"))
    assert new_candidate["fingerprint"] == objective["source_fingerprint"]
    assert new_candidate["fingerprint"] != json.loads(old_candidate)["fingerprint"]
    assert new_candidate["generated_by"] == provenance
