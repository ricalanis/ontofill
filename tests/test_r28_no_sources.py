"""R28: an empty P3 source search pauses for a human without crashing the run."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ontofill.inference import generated_by
from ontofill.lake import FileLake
from ontofill.phase_loop import LoopBudget
from ontofill.phases.p3_fanout.discovery_loop import DiscoveryLoop
from ontofill.phases.p3_fanout.leads import Lead, LeadContext, LeadProvider
from ontofill.workflow import NEEDS_HUMAN_EXIT, run_case
from tests.genericity.fixtures.libraries import library_decisions


class EmptyRecordedProvider(LeadProvider):
    name = "synthetic-recorded"

    def leads(self, context: LeadContext) -> list[Lead]:
        query = "; ".join(item.text for item in context.queries)
        self._attempt(query, "empty", 0)
        return []


def test_recorded_p3_no_sources_pauses_with_bounded_reason_and_trace(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    original = tmp_path / "case"
    original.mkdir()
    (original / "brief.md").write_text(
        "Find public library hours and internet access in Example City.\n", encoding="utf-8"
    )
    run_id = "mock-r28-no-sources"
    lake = FileLake(tmp_path / "lake")

    def scratch_case(_case_dir: Path, _run_id: str) -> tuple[Path, FileLake]:
        scratch = tmp_path / "scratch" / "case"
        scratch.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original / "brief.md", scratch / "brief.md")
        return scratch, lake

    monkeypatch.setattr("ontofill.workflow._scratch_case", scratch_case)
    decision = library_decisions()

    def forbidden_capture(*_args, **_kwargs) -> dict:
        raise AssertionError("an empty provider must not dispatch a browser capture")

    discovery = DiscoveryLoop(
        [EmptyRecordedProvider()],
        capture=forbidden_capture,
        lake=lake,
        run_id=run_id,
        provenance=generated_by(decision),
        budget=LoopBudget(max_iterations=2, wall_seconds=60),
    )

    result = run_case(
        original,
        to_phase=3,
        run_id=run_id,
        preview_past_checkpoints=True,
        decision=decision,
        search_client=discovery,
        lake=lake,
    )

    assert result == NEEDS_HUMAN_EXIT
    status_key = f"runs/{original.name}/{run_id}/status.json"
    status = json.loads(lake.read_key(status_key))
    assert status["state"] == "paused"
    assert status["phase"] == 3 and status["checkpoint_pending"] is None
    assert status["reason"].startswith("no authoritative source found for ")
    assert "queries:" in status["reason"]
    assert "objections:" in status["reason"]
    assert "iterations: 2" in status["reason"]
    assert len(status["reason"]) <= 750
    trace = [
        json.loads(line)
        for line in lake.read_key(f"runs/{original.name}/{run_id}/trace.live.jsonl").splitlines()
    ]
    outcome = next(step for step in trace if step["evaluated"].get("status") == "needs_human")
    assert outcome["evaluated"]["queries"]
    assert outcome["evaluated"]["objections"]
    assert outcome["evaluated"]["stop_reason"] == "max_iterations"
    assert len(outcome["evaluated"]["queries"]) <= 8
    assert len(outcome["evaluated"]["objections"]) <= 8
    output = capsys.readouterr().out
    assert "needs_human=true" in output
    assert "Traceback" not in output and "state=failed" not in output
