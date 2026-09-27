"""P3 stops visibly when query planning cannot dispatch a provider."""

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


class CountingProvider(LeadProvider):
    name = "synthetic-counting"

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def leads(self, context: LeadContext) -> list[Lead]:
        self.calls += 1
        self._attempt("; ".join(query.text for query in context.queries), "empty", 0)
        return []


def test_p3_empty_query_plan_pauses_before_repeating_model_decisions(
    tmp_path: Path, monkeypatch
) -> None:
    case = tmp_path / "case"
    case.mkdir()
    (case / "brief.md").write_text("Find public library hours in Example City.\n")
    lake = FileLake(tmp_path / "lake")
    run_id = "mock-p3-no-dispatch"

    def scratch_case(_case_dir: Path, _run_id: str) -> tuple[Path, FileLake]:
        scratch = tmp_path / "scratch" / "case"
        scratch.mkdir(parents=True, exist_ok=True)
        shutil.copy2(case / "brief.md", scratch / "brief.md")
        return scratch, lake

    monkeypatch.setattr("ontofill.workflow._scratch_case", scratch_case)
    decision = library_decisions()
    provider = CountingProvider()
    discovery = DiscoveryLoop(
        [provider],
        capture=lambda _url, **_kwargs: {},
        lake=lake,
        run_id=run_id,
        provenance=generated_by(decision),
        budget=LoopBudget(max_iterations=20, wall_seconds=3600),
    )
    monkeypatch.setattr(discovery, "_plan_queries", lambda *_args: [])

    result = run_case(
        case,
        to_phase=3,
        run_id=run_id,
        preview_past_checkpoints=True,
        decision=decision,
        search_client=discovery,
        lake=lake,
    )

    assert result == NEEDS_HUMAN_EXIT
    assert provider.calls == 0
    status = json.loads(lake.read_key(f"runs/{case.name}/{run_id}/status.json"))
    assert status["state"] == "paused"
    assert "no provider" in status["reason"].lower()
    trace = [
        json.loads(line)
        for line in lake.read_key(f"runs/{case.name}/{run_id}/trace.live.jsonl").splitlines()
    ]
    stops = [step for step in trace if step["evaluated"].get("status") == "needs_human"]
    assert len(stops) == 1
    assert stops[0]["evaluated"]["stop_reason"] == "no_provider_dispatch"
    assert not [step for step in trace if step["requested"].get("tool", "").startswith("lead.")]
