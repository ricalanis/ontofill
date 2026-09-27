"""Recorded generic case probes the outer gap loop without live inference."""

from __future__ import annotations

import json
import uuid
from collections import deque
from copy import deepcopy

from ontofill.outer_gap import (
    decide_outer_gap,
    gaps_from_metrics,
    outer_trace_step,
    prior_gap_iterations,
)
from ontofill.sandbox import parse as parse_module
from ontofill.workflow import _scratch_case, run_case
from tests.genericity.fixtures.libraries import library_decisions
from tests.genericity.test_library_workflow import BRIEF, LibrarySearch, _capture, _fetch
from tests.r17_helpers import SyntheticParseExecutor


def _run_library_case(tmp_path, monkeypatch, *, target: int, reopen: list[dict] | None = None):
    monkeypatch.setattr(parse_module, "DockerParseExecutor", SyntheticParseExecutor)
    case = tmp_path / "case"
    case.mkdir()
    (case / "brief.md").write_text(BRIEF.read_text(encoding="utf-8"), encoding="utf-8")
    run_id = f"mock-{uuid.uuid4().hex[:16]}"
    decision = library_decisions()
    decision.responses["phase1.prd"][0]["definition_of_done"][0]["target"] = target
    decision.responses["phase2.dod_queries"][0]["queries"][0]["target"] = target
    if reopen:
        decision.responses["outer.gap_decision"] = deque(reopen)
        decision.responses["phase5.select_download"].extend(
            deepcopy(decision.responses["phase5.select_download"][0]) for _ in reopen
        )
        if any(item["reopen"] == 4 for item in reopen):
            decision.responses["phase4.local_scope"].append(
                deepcopy(decision.responses["phase4.local_scope"][0])
            )
    code = run_case(
        case,
        run_id=run_id,
        decision=decision,
        preview_past_checkpoints=True,
        search_client=LibrarySearch(),
        capture=_capture,
        fetch=_fetch,
    )
    scratch, lake = _scratch_case(case, run_id)
    prefix = f"runs/{case.name}/{run_id}"
    trace = [json.loads(line) for line in lake.read_key(f"{prefix}/trace.live.jsonl").splitlines()]
    metrics = json.loads(lake.read_key(f"gold/{case.name}/{run_id}/metrics.json"))
    outer = [step for step in trace if step.get("loop", {}).get("phase") == "outer"]
    return case, scratch, lake, code, outer, metrics


def test_no_measured_gap_stops_without_model_reopen(tmp_path, monkeypatch) -> None:
    case, _scratch, lake, code, outer, metrics = _run_library_case(tmp_path, monkeypatch, target=1)
    assert code == 3  # recorded checkpoints remain unsatisfied
    assert len(outer) == 1
    assert outer[0]["executed"]["reopen"] is None
    assert outer[0]["loop"]["stop_reason"] == "checks_passed"
    assert metrics["loops"][-1] == {
        "phase": "outer",
        "iterations": 1,
        "stop_reason": "checks_passed",
        "usd": 0,
    }
    assert all(item["met"] is False for item in metrics["dod"])
    assert sorted(path.name for path in case.iterdir()) == ["brief.md"]
    assert not lake.exists(f"runs/{case.name}/latest.json")


def test_gap_reopens_discovery_twice_then_stops_at_code_bound(tmp_path, monkeypatch) -> None:
    repair = {"reopen": 3, "reason": "Find another captured public source"}
    case, scratch, lake, code, outer, metrics = _run_library_case(
        tmp_path, monkeypatch, target=2, reopen=[repair, repair]
    )
    assert code == 3
    assert [step["executed"]["reopen"] for step in outer] == [3, 3, None]
    assert [step["loop"]["iteration"] for step in outer] == [1, 2, 3]
    assert outer[-1]["loop"]["stop_reason"] == "max_iterations"
    assert outer[-1]["observed"]["gaps"][0]["criterion_id"] == "library_count"
    assert metrics["loops"][-1] == {
        "phase": "outer",
        "iterations": 3,
        "stop_reason": "max_iterations",
        "usd": 0,
    }
    assert metrics["entities_total"] == {"library": 1}
    ledger = json.loads((scratch / "03-fanout/surface-map/discovery.json").read_text())
    assert len(ledger["rounds"]) == 3
    objectives = json.loads((scratch / "03-fanout/objectives.json").read_text())
    assert len(objectives["objectives"]) == 1
    saved_trace = [
        json.loads(line)
        for line in lake.read_key(f"gold/{case.name}/{outer[0]['run_id']}/trace.jsonl").splitlines()
    ]
    assert sum(step.get("requested", {}).get("url") is not None for step in saved_trace) >= 3
    assert sorted(path.name for path in case.iterdir()) == ["brief.md"]
    assert not lake.exists(f"gold/{case.name}/latest.json")


def test_gap_reopens_local_tdd_with_gap_context_and_retains_prior_draft(
    tmp_path, monkeypatch
) -> None:
    case, scratch, _lake, code, outer, metrics = _run_library_case(
        tmp_path,
        monkeypatch,
        target=2,
        reopen=[
            {"reopen": 4, "reason": "Try a revised extraction plan"},
            {"reopen": None, "reason": "No further safe repair"},
        ],
    )
    assert code == 3
    assert [step["executed"]["reopen"] for step in outer] == [4, None]
    assert outer[-1]["loop"]["stop_reason"] == "human"
    objective = json.loads((scratch / "03-fanout/objectives.json").read_text())["objectives"][0]
    local = scratch / "04-local" / f"{objective['source_id']}__{objective['id']}"
    assert (local / "revisions/gap-1/tdd.json").is_file()
    assert (local / "tdd.md").is_file()
    assert metrics["entities_total"] == {"library": 1}
    assert sorted(path.name for path in case.iterdir()) == ["brief.md"]


def test_ontology_reopen_waits_for_human_review(tmp_path, monkeypatch) -> None:
    case, scratch, lake, code, outer, _metrics = _run_library_case(
        tmp_path,
        monkeypatch,
        target=2,
        reopen=[{"reopen": 2, "reason": "The ontology may not cover the approved query"}],
    )
    assert code == 3
    assert [step["executed"]["reopen"] for step in outer] == [2]
    assert outer[0]["loop"]["stop_reason"] == "human"
    pending = (scratch / "02-ontology/APPROVAL_PENDING.md").read_text()
    assert "Outer-loop DoD gaps" in pending
    assert "library_count" in pending
    status = json.loads(lake.read_key(f"runs/{case.name}/{outer[0]['run_id']}/status.json"))
    assert status["state"] == "paused"
    assert status["checkpoint_pending"] == "ontology"


def test_invalid_outer_decision_pauses_step_after_three_traced_attempts(
    tmp_path, monkeypatch
) -> None:
    bad = {"reopen": 99, "reason": "Synthetic invalid choice"}
    _case, _scratch, lake, code, outer, _metrics = _run_library_case(
        tmp_path, monkeypatch, target=2, reopen=[bad, bad, bad]
    )

    assert code == 3
    assert [step["loop"]["stop_reason"] for step in outer] == ["human"]
    assert "invalid after 3 attempts" in outer[0]["evaluated"]["reason"]
    run_id = outer[0]["run_id"]
    steps = [
        json.loads(line)
        for line in lake.read_key(f"runs/case/{run_id}/trace.live.jsonl").splitlines()
    ]
    attempts = [
        step
        for step in steps
        if step.get("requested", {}).get("tool") == "decision.complete_json"
        and step["observed"].get("artifact") == "outer.gap_decision"
    ]
    assert len(attempts) == 3
    assert all(step["evaluated"]["status"] == "invalid_response" for step in attempts)


def test_gap_fields_derive_only_from_approved_ontology_queries() -> None:
    metrics = {"dod": [{"criterion_id": "c", "actual": 0, "target": 1, "met": False}]}
    queries = {
        "queries": [
            {
                "criterion_id": "c",
                "aggregate": "count_entities_with_properties",
                "properties": ["opening_hours"],
                "operator": ">=",
                "target": 1,
            }
        ]
    }
    ontology = {"properties": [{"id": "opening_hours", "dod": True}]}
    gap = gaps_from_metrics(metrics, queries, ontology)[0]
    assert gap.properties == ("opening_hours",)
    assert gap.public_summary()["criterion_id"] == "c"


def test_gap_iterations_are_counted_per_criterion_id() -> None:
    trace = [
        {
            "event": "loop",
            "loop": {"phase": "outer", "role": "decide"},
            "executed": {"reopen": 4},
            "observed": {"gaps": [{"criterion_id": "gap_a", "properties": ["name"]}]},
        },
        {
            "event": "loop",
            "loop": {"phase": "outer", "role": "decide"},
            "executed": {"reopen": 3},
            "observed": {"gaps": [{"criterion_id": "gap_a", "properties": ["name"]}]},
        },
    ]
    assert prior_gap_iterations(trace) == {"gap_a": 2}
    metrics = {
        "dod": [
            {"criterion_id": "gap_a", "actual": 0, "target": 1},
            {"criterion_id": "gap_b", "actual": 0, "target": 1},
        ]
    }
    queries = {
        "queries": [
            {"criterion_id": "gap_a", "properties": ["name"], "operator": ">=", "target": 1},
            {"criterion_id": "gap_b", "properties": ["category"], "operator": ">=", "target": 1},
        ]
    }
    ontology = {
        "properties": [
            {"id": "name", "dod": True},
            {"id": "category", "dod": True},
        ]
    }
    gaps = gaps_from_metrics(
        metrics, queries, ontology, prior_iterations=prior_gap_iterations(trace)
    )
    assert {gap.criterion_id: gap.iteration for gap in gaps} == {"gap_a": 3, "gap_b": 1}
    assert {gap.criterion_id: gap.properties for gap in gaps} == {
        "gap_a": ("name",),
        "gap_b": ("category",),
    }


def test_outer_budget_blocks_decision_and_counts_retry_usage() -> None:
    metrics = {"dod": [{"criterion_id": "c", "actual": 0, "target": 1, "met": False}]}
    queries = {"queries": [{"criterion_id": "c", "operator": ">=", "target": 1}]}
    ontology = {"properties": [{"id": "title", "dod": True}]}
    objectives = {"objectives": [{"id": "obj", "source_id": "source", "target_fields": ["title"]}]}
    provenance = {"backend": "vultr", "model": "glm-5.3", "at": "2026-01-01T00:00:00Z"}

    class Decision:
        def __init__(self) -> None:
            self.call_log: list[dict] = []
            self.calls = 0

        def complete_json(self, purpose, prompt, schema):
            self.calls += 1
            for cost in (0.01, 0.02):
                self.call_log.append(
                    {
                        "usage": {
                            "model": "glm-5.3",
                            "backend": "vultr",
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "est_usd": cost,
                        }
                    }
                )
            return {"reopen": 3, "reason": "Search for a second source"}

    client = Decision()
    prior = [{"usage": {"est_usd": 0.08}}]
    blocked = decide_outer_gap(
        metrics=metrics,
        dod_queries=queries,
        ontology=ontology,
        objectives=objectives,
        decision=client,
        provenance=provenance,
        trace=prior,
        budget_usd=0.08,
    )
    assert blocked.stop_reason == "budget"
    assert blocked.reopen is None
    assert client.calls == 0

    allowed = decide_outer_gap(
        metrics=metrics,
        dod_queries=queries,
        ontology=ontology,
        objectives=objectives,
        decision=client,
        provenance=provenance,
        trace=prior,
        budget_usd=0.2,
    )
    assert allowed.reopen == 3
    assert allowed.usd == 0.11
    assert allowed.usage["est_usd"] == 0.03
    assert allowed.usage["input_tokens"] == 20
    step = outer_trace_step("live-run", allowed)
    assert step["executed"]["reopen"] == 3
    assert step["usage"]["est_usd"] == 0.03
