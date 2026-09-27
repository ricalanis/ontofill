"""Recorded previews pause for approval and never advance live pointers."""

from __future__ import annotations

import json
import uuid

import pytest

from ontofill.inference import generated_by
from ontofill.lake import FileLake
from ontofill.phases.p1_scope.phase import PrdDraftUnavailable
from ontofill.phases.p2_ontology.phase import OntologyDraftUnavailable
from ontofill.phases.p3_fanout.authority import source_fingerprint
from ontofill.workflow import (
    NEEDS_HUMAN_EXIT,
    _capture_with_live_trace,
    _final_dod_status,
    _persisted_run_trace,
    _preview_decision,
    _scratch_case,
    _source_review,
    run_case,
)
from tests.approval_support import bind_approval


def test_unmet_approved_dod_pauses_final_status_and_names_criteria() -> None:
    state, reason, exit_code = _final_dod_status(
        {
            "dod": [
                {"criterion_id": "dod1", "met": True},
                {"criterion_id": "dod2", "met": False, "reason": "unresolved"},
                {"criterion_id": "dod3", "met": False},
            ]
        }
    )

    assert state == "paused"
    assert reason == "approved DoD remains unresolved or unmet: dod2, dod3"
    assert exit_code == NEEDS_HUMAN_EXIT


def test_export_trace_includes_steps_from_prior_checkpoint_runs(tmp_path) -> None:
    lake = FileLake(tmp_path / "lake")
    lake.write_key(
        "runs/example/run-1/trace.live.jsonl",
        b'{"step_id":"before-approval"}\n{"step_id":"after-approval"}\n',
    )
    assert [step["step_id"] for step in _persisted_run_trace(lake, "example", "run-1", [])] == [
        "before-approval",
        "after-approval",
    ]


def test_capture_trace_bridge_publishes_during_capture_and_deduplicates_result() -> None:
    events: list[str] = []
    step = {"step_id": "step:synthetic-page", "executed": {"bronze_key": "sha256:page"}}

    def capture(_url: str, **kwargs: object) -> dict:
        kwargs["on_trace"](step)
        events.append("capture-returned")
        return {"page_trace": [step]}

    traced_capture, published_ids = _capture_with_live_trace(
        capture, lambda _step: events.append("published")
    )
    result = traced_capture("https://example.invalid/")

    assert events == ["published", "capture-returned"]
    assert result["page_trace"] == [step]
    assert published_ids == {step["step_id"]}


@pytest.mark.parametrize("purpose", ["phase1.prd", "phase1.prd.publisher_patch"])
def test_exhausted_prd_validation_needs_human_without_empty_approval(
    tmp_path, monkeypatch, capsys, purpose
) -> None:
    case = tmp_path / "tracked-case"
    case.mkdir()
    (case / "brief.md").write_text("Find public reading rooms in Example City.\n")
    run_id = "mock-" + uuid.uuid4().hex
    objections = (
        "Authority policy needs a primary publisher in the case jurisdiction; "
        "trusted domain is a broad namespace"
    )

    def exhausted(*_args, **_kwargs):
        raise PrdDraftUnavailable(
            f"phase1.prd failed validation after 3 attempts: {objections}",
            purpose=purpose,
            attempts=3,
            reason=objections,
        )

    monkeypatch.setattr("ontofill.workflow.draft_prd", exhausted)
    result = run_case(case, run_id=run_id, decision=_preview_decision("Public reading rooms"))

    assert result == NEEDS_HUMAN_EXIT
    scratch, lake = _scratch_case(case, run_id)
    assert not (scratch / "01-scope/prd.json").exists()
    assert not (scratch / "01-scope/APPROVAL_PENDING.md").exists()
    status = json.loads(lake.read_key(f"runs/{case.name}/{run_id}/status.json"))
    assert status["state"] == "paused"
    assert status["checkpoint_pending"] is None
    assert "primary publisher" in status["reason"]
    assert "broad namespace" in status["reason"]
    trace = [
        json.loads(line)
        for line in lake.read_key(f"runs/{case.name}/{run_id}/trace.live.jsonl").splitlines()
    ]
    pause = next(step for step in trace if step["requested"].get("tool") == "phase1.prd.pause")
    assert pause["evaluated"]["status"] == "needs_human"
    assert "needs_human=true" in capsys.readouterr().out


@pytest.mark.parametrize("phase", ["factors", "ontology"])
def test_exhausted_p2_without_artifact_needs_human_and_has_no_pending_checkpoint(
    tmp_path, monkeypatch, capsys, phase
) -> None:
    case = tmp_path / "tracked-case"
    case.mkdir()
    (case / "brief.md").write_text("Find public reading rooms in Example City.\n")
    run_id = "mock-" + uuid.uuid4().hex
    decision = _preview_decision("Public reading rooms")

    def prd(*_args, **_kwargs):
        return {"generated_by": generated_by(decision)}

    def factors(*_args, **_kwargs):
        if phase == "factors":
            raise OntologyDraftUnavailable("phase2.factors", 3, "synthetic factors exhaustion")
        return {"generated_by": generated_by(decision)}

    def ontology(*_args, **_kwargs):
        raise OntologyDraftUnavailable("phase2.schema", 3, "synthetic ontology exhaustion")

    monkeypatch.setattr("ontofill.workflow.draft_prd", prd)
    monkeypatch.setattr("ontofill.workflow.draft_factors", factors)
    monkeypatch.setattr("ontofill.workflow.draft_ontology", ontology)
    monkeypatch.setattr("ontofill.workflow.require_approval", lambda *_args, **_kwargs: True)

    assert run_case(case, run_id=run_id, decision=decision) == NEEDS_HUMAN_EXIT
    scratch, lake = _scratch_case(case, run_id)
    artifact = (
        scratch / "02-ontology/factors/factors.json"
        if phase == "factors"
        else scratch / "02-ontology/ontology.json"
    )
    assert not artifact.exists()
    status = json.loads(lake.read_key(f"runs/{case.name}/{run_id}/status.json"))
    assert status["state"] == "paused"
    assert status["checkpoint_pending"] is None
    assert "synthetic" in status["reason"]
    trace = [
        json.loads(line)
        for line in lake.read_key(f"runs/{case.name}/{run_id}/trace.live.jsonl").splitlines()
    ]
    tool = f"phase2.{phase}.pause"
    pause = next(step for step in trace if step["requested"].get("tool") == tool)
    assert pause["evaluated"]["status"] == "needs_human"
    assert pause["evaluated"]["reason_code"] == "model_validation_exhausted"
    assert "synthetic" in pause["evaluated"]["reason"]
    assert "needs_human=true" in capsys.readouterr().out


def test_retained_auto_source_is_rechecked_against_revised_authority_policy(tmp_path) -> None:
    url = "https://community.example.test/rooms"
    primary_policy = {
        "trusted_publishers": [{"kind": "Community listing", "domains": ["community.example.test"]}]
    }
    secondary_policy = {
        "trusted_publishers": [
            {
                "kind": "Community listing",
                "tier": "secondary",
                "domains": ["community.example.test"],
            }
        ]
    }
    old_fingerprint = source_fingerprint(
        url=url,
        title="Reading room list",
        snippet="A public directory",
        provider="synthetic",
        capture_key=None,
        authority_policy=primary_policy,
    )
    objective = {
        "id": "objective-one",
        "source_id": "source-one",
        "source_url": url,
        "source_fingerprint": old_fingerprint,
    }
    sources = tmp_path / "03-fanout/sources/source-one"
    sources.mkdir(parents=True)
    (sources / "candidate.json").write_text(
        json.dumps(
            {
                "url": url,
                "title": "Reading room list",
                "snippet": "A public directory",
                "provider": "synthetic",
                "capture_key": None,
                "fingerprint": old_fingerprint,
                "authority": "auto",
            }
        ),
        encoding="utf-8",
    )
    (sources / "APPROVED").write_text(
        json.dumps(
            bind_approval(
                tmp_path,
                ["03-fanout/sources/source-one/candidate.json"],
                {
                    "approver": "Example Reviewer",
                    "date": "2026-09-26",
                    "checkpoint": "source",
                    "source_fingerprint": old_fingerprint,
                },
            )
        ),
        encoding="utf-8",
    )
    objectives_path = tmp_path / "03-fanout/objectives.json"
    objectives_path.write_text(json.dumps({"objectives": [objective]}), encoding="utf-8")
    provenance = {"backend": "vultr", "model": "synthetic-test", "at": "2026-09-26T00:00:00Z"}
    approved, directory = _source_review(tmp_path, objective, provenance, secondary_policy)
    assert not approved and directory == sources
    manifest = json.loads((sources / "candidate.json").read_text())
    assert manifest["authority"] == "review"
    assert manifest["fingerprint"] != old_fingerprint
    assert objective["source_fingerprint"] == manifest["fingerprint"]
    assert (
        json.loads(objectives_path.read_text())["objectives"][0]["source_fingerprint"]
        == manifest["fingerprint"]
    )
    assert "APPROVAL_PENDING" in {path.stem for path in sources.iterdir()}


def test_jev_cannot_be_primary_checkpoint_backend(tmp_path) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "brief.md").write_text("Synthetic public brief", encoding="utf-8")

    class JevOnly:
        backend = "jev"
        model = "jev-test"

    with pytest.raises(ValueError, match="Jev is supporting only"):
        run_case(case, decision=JevOnly())


def test_recorded_default_pauses_at_first_checkpoint(tmp_path, capsys) -> None:
    case = tmp_path / "tracked-case"
    case.mkdir()
    (case / "brief.md").write_text("Public reading rooms in Example City", encoding="utf-8")
    run_id = "mock-" + uuid.uuid4().hex
    assert run_case(case, run_id=run_id, decision=_preview_decision("Public reading rooms")) == 3
    scratch, lake = _scratch_case(case, run_id)
    assert (scratch / "01-scope/prd.json").exists()
    prd = json.loads((scratch / "01-scope/prd.json").read_text())
    assert not prd["jobs_to_be_done"][0]["description"].startswith("#")
    assert {item["metric"] for item in prd["definition_of_done"]} == {
        "records_total",
        "values_without_evidence",
    }
    assert not (scratch / "02-ontology/factors/factors.json").exists()
    assert not lake.exists(f"gold/{case.name}/{run_id}/metrics.json")
    output = capsys.readouterr().out
    assert f"run_id={run_id}" in output
    assert "checkpoint_pending=prd" in output
    assert "recorded artifacts cannot satisfy" in output
    (scratch / "01-scope/APPROVED").write_text(
        json.dumps(
            bind_approval(
                scratch,
                ["01-scope/prd.json"],
                {"approver": "Example Reviewer", "date": "2026-09-26", "checkpoint": "prd"},
            )
        ),
        encoding="utf-8",
    )
    assert run_case(case, run_id=run_id, decision=_preview_decision("Public reading rooms")) == 3
    assert "recorded artifacts cannot satisfy" in capsys.readouterr().out
    assert list((scratch / "01-scope").glob("APPROVED.stale.*"))
    assert sorted(path.name for path in case.iterdir()) == ["brief.md"]
    assert not lake.exists(f"runs/{case.name}/latest.json")


def test_stale_review_pauses_run_without_changing_case_files(tmp_path, capsys) -> None:
    case = tmp_path / "tracked-case"
    case.mkdir()
    (case / "brief.md").write_text("Public reading rooms in Example City", encoding="utf-8")
    run_id = "mock-" + uuid.uuid4().hex
    assert run_case(case, run_id=run_id, decision=_preview_decision("Public reading rooms")) == 3
    scratch, lake = _scratch_case(case, run_id)
    scope = scratch / "01-scope"
    marker = bind_approval(
        scratch,
        ["01-scope/prd.json"],
        {"approver": "Reviewer", "date": "2026-09-26", "checkpoint": "prd"},
    )
    (scope / "APPROVED").write_text(json.dumps(marker), encoding="utf-8")
    artifact = scope / "prd.json"
    artifact.write_bytes(artifact.read_bytes() + b" ")
    before = {
        path.relative_to(scratch): path.read_bytes()
        for path in scratch.rglob("*")
        if path.is_file()
    }
    capsys.readouterr()
    assert run_case(case, run_id=run_id, decision=_preview_decision("Public reading rooms")) == 3
    assert "reason=approval is for a different artifact version" in capsys.readouterr().out
    after = {
        path.relative_to(scratch): path.read_bytes()
        for path in scratch.rglob("*")
        if path.is_file()
    }
    assert after == before
    status = json.loads(lake.read_key(f"runs/{case.name}/{run_id}/status.json"))
    assert status["state"] == "paused"
    assert status["checkpoint_pending"] == "prd"
    assert status["reason"] == "approval is for a different artifact version"


def test_prd_budget_exhausted_before_draft_pauses_without_artifact(tmp_path, capsys) -> None:
    case = tmp_path / "tracked-case"
    case.mkdir()
    (case / "brief.md").write_text("Find public reading rooms.", encoding="utf-8")
    run_id = "mock-" + uuid.uuid4().hex
    assert (
        run_case(
            case,
            run_id=run_id,
            decision=_preview_decision("Find public reading rooms."),
            budget_usd=0,
        )
        == 3
    )
    scratch, lake = _scratch_case(case, run_id)
    assert not (scratch / "01-scope/prd.json").exists()
    pending = (scratch / "01-scope/APPROVAL_PENDING.md").read_text()
    assert "budget ended before" in pending
    status = json.loads(lake.read_key(f"runs/{case.name}/{run_id}/status.json"))
    assert status["state"] == "paused"
    assert status["checkpoint_pending"] == "prd"
    assert "budget exhausted" in capsys.readouterr().out
