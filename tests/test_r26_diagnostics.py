"""R26: a PRD pause explains validator failures in the run feed."""

from __future__ import annotations

import json
from copy import deepcopy

from ontofill.inference import RecordedDecisionClient
from ontofill.lake import FileLake
from ontofill.workflow import run_case
from tests.test_r22_p1 import _prd


def test_prd_exhaustion_records_bounded_errors_in_status_trace_and_cli(tmp_path, capsys) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "brief.md").write_text("Find public records in Example City.", encoding="utf-8")
    invalid = []
    for name in ("authority_policy", "personas", "definition_of_done"):
        answer = deepcopy(_prd())
        del answer[name]
        invalid.append(answer)
    decision = RecordedDecisionClient({"phase1.prd": invalid})
    lake = FileLake(tmp_path / "lake")
    run_id = "mock-r26-prd-errors"

    result = run_case(case, run_id=run_id, decision=decision, lake=lake, to_phase=1)

    assert result == 3
    prefix = f"runs/case/{run_id}"
    status = json.loads(lake.read_key(f"{prefix}/status.json"))
    trace = [json.loads(line) for line in lake.read_key(f"{prefix}/trace.live.jsonl").splitlines()]
    output = capsys.readouterr().out
    for name in ("authority_policy", "personas", "definition_of_done"):
        assert name in status["reason"]
        assert name in output
    assert len(status["reason"]) <= 1200
    attempts = [
        step
        for step in trace
        if step["requested"].get("tool") == "decision.complete_json"
        and step["observed"].get("artifact") == "phase1.prd"
    ]
    assert len(attempts) == 3
    assert all(step["evaluated"]["status"] == "invalid_response" for step in attempts)
    assert all(step["evaluated"].get("reason") for step in attempts)
    pause = [step for step in trace if step["evaluated"].get("status") == "paused"]
    assert pause
    assert "definition_of_done" in pause[-1]["evaluated"]["reason"]
