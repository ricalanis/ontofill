"""Recorded previews pause for approval and never advance live pointers."""

from __future__ import annotations

import json
import uuid

import pytest

from ontofill.lake import FileLake
from ontofill.workflow import _persisted_run_trace, _preview_decision, _scratch_case, run_case


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
        '{"approver":"Example Reviewer","date":"2026-09-26","checkpoint":"prd"}',
        encoding="utf-8",
    )
    assert run_case(case, run_id=run_id, decision=_preview_decision("Public reading rooms")) == 3
    assert (
        "APPROVED exists but the artifact was produced by the recorded backend"
        in capsys.readouterr().out
    )
    assert sorted(path.name for path in case.iterdir()) == ["brief.md"]
    assert not lake.exists(f"runs/{case.name}/latest.json")


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
