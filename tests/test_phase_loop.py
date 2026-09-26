"""The shared loop keeps control flow and spend out of model decisions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from ontofill.lake import FileLake
from ontofill.phase_loop import CheckResult, LoopBudget, PhaseLoop
from ontofill.runfeed import RunFeed

RECORDED = {"backend": "recorded", "model": "recorded-test", "at": "2026-01-01T00:00:00Z"}
VULTR = {"backend": "vultr", "model": "glm-5.3", "at": "2026-01-01T00:00:00Z"}


def test_rejected_draft_gets_revised_and_rechecked_with_live_trace(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    feed = RunFeed(lake, "example-case", "mock-loop", RECORDED, start_heartbeat=False)
    feed.update_status(state="running", phase=1)
    calls: list[str] = []
    loop = PhaseLoop[dict](
        phase=1,
        run_id="mock-loop",
        generated_by=RECORDED,
        budget=LoopBudget(max_iterations=3),
        emit=feed.append_step,
    )

    def gather(iteration: int, previous: dict | None) -> dict:
        calls.append("gather")
        return {"iteration": iteration, "previous": previous}

    def propose(context: dict, iteration: int) -> dict:
        calls.append("propose")
        return {"version": iteration}

    def critique(artifact: dict, context: dict, iteration: int) -> dict:
        calls.append("critique")
        return {"accepted": iteration > 1, "reason": "unsupported target"}

    def revise(artifact: dict, critic: CheckResult, context: dict, iteration: int) -> dict:
        calls.append("revise")
        return {**artifact, "critic_objections": critic.objections}

    def check(artifact: dict, context: dict, iteration: int) -> CheckResult:
        calls.append("check")
        return CheckResult(passed=True)

    result = loop.run(
        gather=gather, propose=propose, critique=critique, revise=revise, check=check
    )
    assert result.stop_reason == "checks_passed"
    assert result.iterations == 2
    assert result.usd == 0
    assert result.metric(1) == {
        "phase": 1,
        "iterations": 2,
        "stop_reason": "checks_passed",
        "usd": 0,
    }
    assert calls == ["gather", "propose", "critique", "revise", "check"] * 2
    steps = [
        json.loads(line)
        for line in lake.read_key("runs/example-case/mock-loop/trace.live.jsonl").splitlines()
    ]
    assert [step["loop"]["role"] for step in steps] == [
        "gather", "propose", "critique", "revise", "check", "decide"
    ] * 2
    assert steps[2]["loop"]["objections"] == ["unsupported target"]
    assert steps[-1]["loop"]["stop_reason"] == "checks_passed"
    assert all(step["event"] == "loop" for step in steps)
    feed.close()


def test_live_spend_budget_stops_before_next_model_stage() -> None:
    call_log: list[dict] = []
    steps: list[dict] = []
    calls: list[str] = []
    loop = PhaseLoop[dict](
        phase=1,
        run_id="live-run",
        generated_by=VULTR,
        budget=LoopBudget(max_iterations=3, max_usd=0.01),
        emit=steps.append,
        call_log=call_log,
    )

    def propose(_context: object, _iteration: int) -> dict:
        calls.append("propose")
        call_log.append(
            {
                "usage": {
                    "model": "glm-5.3", "backend": "vultr", "input_tokens": 10,
                    "output_tokens": 10, "est_usd": 0.02,
                }
            }
        )
        return {"draft": True}

    result = loop.run(
        gather=lambda _iteration, _previous: {},
        propose=propose,
        critique=lambda *_args: pytest.fail("critic ran after spend limit"),
        revise=lambda *_args: pytest.fail("revision ran after spend limit"),
        check=lambda *_args: pytest.fail("check ran after spend limit"),
    )
    assert result.stop_reason == "budget"
    assert result.artifact == {"draft": True}
    assert result.usd == 0.02
    assert calls == ["propose"]
    assert steps[-1]["loop"]["stop_reason"] == "budget"
    assert steps[1]["usage"]["est_usd"] == 0.02


def test_human_gate_and_max_iterations_have_distinct_stop_reasons() -> None:
    common = {
        "phase": 1, "run_id": "mock-example", "generated_by": RECORDED,
        "budget": LoopBudget(max_iterations=2),
    }
    hooks = {
        "gather": lambda iteration, previous: {},
        "propose": lambda context, iteration: {"iteration": iteration},
        "revise": lambda artifact, critic, context, iteration: artifact,
        "check": lambda artifact, context, iteration: True,
    }
    pending = PhaseLoop[dict](**common).run(
        **hooks,
        critique=lambda *_args: {"accepted": True, "reason": "okay"},
        gate=lambda *_args: None,
    )
    assert pending.stop_reason == "human"
    rejected = PhaseLoop[dict](**common).run(
        **hooks,
        critique=lambda *_args: {"accepted": False, "reason": "unsupported target"},
    )
    assert rejected.stop_reason == "max_iterations"
    assert rejected.iterations == 2
    assert rejected.objections == ("unsupported target",)


def test_wall_budget_and_unpriced_live_call_fail_closed() -> None:
    time_values = iter([0, 0, 10])
    clock = lambda: next(time_values)
    wall = PhaseLoop[dict](
        phase=1, run_id="mock-wall", generated_by=RECORDED,
        budget=LoopBudget(wall_seconds=5), monotonic=clock,
    ).run(
        gather=lambda *_args: {}, propose=lambda *_args: pytest.fail("propose after wall budget"),
        critique=lambda *_args: {}, revise=lambda *_args: {}, check=lambda *_args: True,
    )
    assert wall.stop_reason == "budget"

    call_log: list[dict] = []
    unpriced = PhaseLoop[dict](
        phase=1, run_id="live-unpriced", generated_by=VULTR,
        budget=LoopBudget(max_usd=1), call_log=call_log,
    ).run(
        gather=lambda *_args: {},
        propose=lambda *_args: call_log.append(
            {"usage": {"model": "glm-5.3", "backend": "vultr", "input_tokens": 1,
                       "output_tokens": 1, "est_usd": None}}
        ) or {"draft": True},
        critique=lambda *_args: pytest.fail("critic after unpriced call"),
        revise=lambda *_args: {}, check=lambda *_args: True,
    )
    assert unpriced.stop_reason == "budget"
    with pytest.raises(ValueError, match="call log"):
        PhaseLoop(phase=1, run_id="live-no-log", generated_by=VULTR, budget=LoopBudget(max_usd=1))


def test_loop_schema_and_metrics_reject_invalid_rows() -> None:
    schema_dir = Path(__file__).resolve().parents[1] / "schemas"
    trace_schema = json.loads((schema_dir / "trace-step.schema.json").read_text())
    metrics_schema = json.loads((schema_dir / "metrics.schema.json").read_text())
    trace = []
    result = PhaseLoop[dict](
        phase=1, run_id="mock-schema", generated_by=RECORDED,
        budget=LoopBudget(max_iterations=1), emit=trace.append,
    ).run(
        gather=lambda *_args: {}, propose=lambda *_args: {},
        critique=lambda *_args: True, revise=lambda artifact, *_args: artifact,
        check=lambda *_args: True,
    )
    Draft202012Validator(trace_schema["$defs"]["loop"]).validate(trace[-1]["loop"])
    metrics = {"loops": [result.metric(1)]}
    Draft202012Validator(metrics_schema["properties"]["loops"]).validate(metrics["loops"])
    invalid = {**trace[-1], "loop": {**trace[-1]["loop"], "role": "invented"}}
    with pytest.raises(ValidationError):
        Draft202012Validator(trace_schema["$defs"]["loop"]).validate(invalid["loop"])
