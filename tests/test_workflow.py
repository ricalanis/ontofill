"""Recorded previews pause for approval and never advance live pointers."""

from __future__ import annotations

import json
import uuid

import pytest

from ontofill.workflow import _preview_decision, _scratch_case, run_case


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
