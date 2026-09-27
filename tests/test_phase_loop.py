"""The shared loop keeps control flow and spend out of model decisions."""

from __future__ import annotations

import hashlib
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

    result = loop.run(gather=gather, propose=propose, critique=critique, revise=revise, check=check)
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
        "gather",
        "propose",
        "critique",
        "revise",
        "check",
        "decide",
    ] * 2
    assert steps[2]["loop"]["objections"] == ["unsupported target"]
    assert steps[-1]["loop"]["stop_reason"] == "checks_passed"
    assert all(step["event"] == "loop" for step in steps)
    feed.close()


def test_critic_flip_cannot_clear_unchanged_objection_subject() -> None:
    steps: list[dict] = []
    seen_by_critic: list[dict] = []
    original = {"authority_policy": {"publishers": [{"tier": "secondary"}]}}

    def critique(artifact: dict, _context: object, iteration: int) -> dict:
        seen_by_critic.append(artifact)
        return {
            "accepted": iteration == 2,
            "reason": "authority_policy tier contradicts primary rationale",
        }

    result = PhaseLoop[dict](
        phase=1,
        run_id="mock-flip",
        generated_by=RECORDED,
        budget=LoopBudget(max_iterations=2),
        emit=steps.append,
    ).run(
        gather=lambda _iteration, previous: {"previous": previous},
        propose=lambda context, _iteration: context["previous"] or original,
        critique=critique,
        revise=lambda artifact, *_args: artifact,
        check=lambda *_args: True,
        objection_addressed=lambda before, after, _objection: (
            before["authority_policy"] != after["authority_policy"]
        ),
    )
    assert result.stop_reason == "max_iterations"
    assert any("subject unchanged" in issue for issue in result.objections)
    assert seen_by_critic == [original, original]
    draft_steps = [
        step for step in steps if step["loop"]["role"] in {"propose", "critique", "revise", "check"}
    ]
    digests = {step["loop"]["draft_sha256"] for step in draft_steps}
    assert digests == {
        hashlib.sha256(
            json.dumps(original, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    }
    assert steps[-1]["loop"]["draft_sha256"] in digests


def test_next_critique_consumes_latest_revised_draft() -> None:
    seen: list[int] = []
    steps: list[dict] = []

    def critique(artifact: dict, _context: object, _iteration: int) -> dict:
        seen.append(artifact["revision"])
        return {"accepted": artifact["revision"] == 2, "reason": "target needs revision"}

    result = PhaseLoop[dict](
        phase=1,
        run_id="mock-carry",
        generated_by=RECORDED,
        budget=LoopBudget(max_iterations=2),
        emit=steps.append,
    ).run(
        gather=lambda _iteration, previous: {"previous": previous},
        propose=lambda context, _iteration: context["previous"] or {"revision": 1},
        critique=critique,
        revise=lambda artifact, verdict, *_args: (
            {"revision": 2} if not verdict.passed else artifact
        ),
        check=lambda *_args: True,
        objection_addressed=lambda before, after, _objection: before != after,
    )
    assert result.stop_reason == "checks_passed"
    assert seen == [1, 2]
    revised = next(
        step
        for step in steps
        if step["loop"]["iteration"] == 1 and step["loop"]["role"] == "revise"
    )
    proposed = next(
        step
        for step in steps
        if step["loop"]["iteration"] == 2 and step["loop"]["role"] == "propose"
    )
    assert revised["loop"]["draft_sha256"] == proposed["loop"]["draft_sha256"]


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
                    "model": "glm-5.3",
                    "backend": "vultr",
                    "input_tokens": 10,
                    "output_tokens": 10,
                    "est_usd": 0.02,
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


def test_failed_http_attempt_does_not_preempt_first_critique() -> None:
    call_log: list[dict] = []
    steps: list[dict] = []
    criticized: list[dict] = []

    def propose(_context: object, _iteration: int) -> dict:
        call_log.extend(
            [
                {"status": "http_502", "usage": {"est_usd": None}},
                {
                    "status": "ok",
                    "usage": {
                        "model": "glm-5.3",
                        "backend": "vultr",
                        "input_tokens": 10,
                        "output_tokens": 10,
                        "est_usd": 0.001,
                    },
                },
            ]
        )
        return {"draft": True}

    result = PhaseLoop[dict](
        phase=3,
        run_id="live-retry",
        generated_by=VULTR,
        budget=LoopBudget(max_iterations=1, max_usd=1),
        emit=steps.append,
        call_log=call_log,
        stop_details=lambda *_args: {
            "candidate_count": 1,
            "judged_candidate_count": 1,
        },
    ).run(
        gather=lambda *_args: {},
        propose=propose,
        critique=lambda draft, *_args: (
            criticized.append(draft) or {"accepted": True, "reason": "retry succeeded"}
        ),
        revise=lambda draft, *_args: draft,
        check=lambda *_args: True,
    )

    assert criticized == [{"draft": True}]
    assert result.stop_reason == "checks_passed"
    assert result.usd == pytest.approx(0.001)
    basis = steps[-1]["executed"]["budget_basis"]
    assert basis["unknown_cost"] is False
    assert basis["usd_spent"] == pytest.approx(0.001)
    assert basis["usd_limit"] == 1
    assert basis["iterations_used"] == 1
    assert basis["iterations_limit"] == 1
    assert basis["model_calls"] == 2
    assert steps[-1]["executed"]["stop_details"] == {
        "candidate_count": 1,
        "judged_candidate_count": 1,
    }


def test_phase_three_defers_wall_stop_until_first_critique() -> None:
    time_values = iter([0, 0, 10, 10, 10])
    clock = lambda: next(time_values)
    stages: list[str] = []
    steps: list[dict] = []

    result = PhaseLoop[dict](
        phase=3,
        run_id="phase-three-wall-floor",
        generated_by=RECORDED,
        budget=LoopBudget(max_iterations=3, wall_seconds=5),
        emit=steps.append,
        monotonic=clock,
    ).run(
        gather=lambda *_args: stages.append("gather") or {},
        propose=lambda *_args: stages.append("propose") or {"candidate": True},
        critique=lambda *_args: stages.append("critique") or True,
        revise=lambda *_args: stages.append("revise") or {},
        check=lambda *_args: stages.append("check") or True,
    )

    assert result.stop_reason == "wall_clock"
    assert stages == ["gather", "propose", "critique"]
    assert steps[-1]["executed"]["budget_basis"]["wall_seconds_used"] == 10


def test_phase_three_usd_limit_is_not_deferred_until_critique() -> None:
    call_log: list[dict] = []
    steps: list[dict] = []

    def propose(*_args: object) -> dict:
        call_log.append(
            {
                "status": "ok",
                "usage": {
                    "model": "glm-5.3",
                    "backend": "vultr",
                    "input_tokens": 10,
                    "output_tokens": 10,
                    "est_usd": 0.02,
                },
            }
        )
        return {"candidate": True}

    result = PhaseLoop[dict](
        phase=3,
        run_id="phase-three-usd-limit",
        generated_by=VULTR,
        budget=LoopBudget(max_iterations=1, max_usd=0.01),
        call_log=call_log,
        emit=steps.append,
    ).run(
        gather=lambda *_args: {},
        propose=propose,
        critique=lambda *_args: pytest.fail("USD cap was deferred until critique"),
        revise=lambda *_args: {},
        check=lambda *_args: True,
    )

    assert result.stop_reason == "budget"
    assert steps[-1]["executed"]["budget_basis"]["unknown_cost"] is False
    assert steps[-1]["executed"]["budget_basis"]["usd_spent"] == pytest.approx(0.02)


def test_stage_usage_sums_multiple_typed_calls() -> None:
    call_log: list[dict] = []
    steps: list[dict] = []

    def propose(_context: object, _iteration: int) -> dict:
        for tokens, cost in [(10, 0.001), (20, 0.002)]:
            call_log.append(
                {
                    "usage": {
                        "model": "glm-5.3",
                        "backend": "vultr",
                        "input_tokens": tokens,
                        "output_tokens": tokens // 2,
                        "est_usd": cost,
                    }
                }
            )
        return {"draft": True}

    result = PhaseLoop[dict](
        phase=1,
        run_id="live-staged",
        generated_by=VULTR,
        budget=LoopBudget(max_usd=1),
        emit=steps.append,
        call_log=call_log,
    ).run(
        gather=lambda *_args: {},
        propose=propose,
        critique=lambda *_args: True,
        revise=lambda artifact, *_args: artifact,
        check=lambda *_args: True,
    )
    usage = next(step["usage"] for step in steps if step["loop"]["role"] == "propose")
    assert usage["input_tokens"] == 30
    assert usage["output_tokens"] == 15
    assert usage["est_usd"] == 0.003
    assert result.usd == 0.003


def test_human_gate_and_max_iterations_have_distinct_stop_reasons() -> None:
    common = {
        "phase": 1,
        "run_id": "mock-example",
        "generated_by": RECORDED,
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
        phase=1,
        run_id="mock-wall",
        generated_by=RECORDED,
        budget=LoopBudget(wall_seconds=5),
        monotonic=clock,
    ).run(
        gather=lambda *_args: {},
        propose=lambda *_args: pytest.fail("propose after wall budget"),
        critique=lambda *_args: {},
        revise=lambda *_args: {},
        check=lambda *_args: True,
    )
    assert wall.stop_reason == "wall_clock"

    call_log: list[dict] = []
    unpriced_steps: list[dict] = []
    unpriced = PhaseLoop[dict](
        phase=3,
        run_id="live-unpriced",
        generated_by=VULTR,
        budget=LoopBudget(max_usd=1),
        call_log=call_log,
        emit=unpriced_steps.append,
    ).run(
        gather=lambda *_args: {},
        propose=lambda *_args: (
            call_log.append(
                {
                    "status": "ok",
                    "usage": {
                        "model": "glm-5.3",
                        "backend": "vultr",
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "est_usd": None,
                    },
                }
            )
            or {"draft": True}
        ),
        critique=lambda *_args: pytest.fail("critic after unpriced call"),
        revise=lambda *_args: {},
        check=lambda *_args: True,
    )
    assert unpriced.stop_reason == "budget"
    assert unpriced_steps[-1]["executed"]["budget_basis"]["unknown_cost"] is True
    with pytest.raises(ValueError, match="call log"):
        PhaseLoop(phase=1, run_id="live-no-log", generated_by=VULTR, budget=LoopBudget(max_usd=1))


def test_loop_schema_and_metrics_reject_invalid_rows() -> None:
    schema_dir = Path(__file__).resolve().parents[1] / "schemas"
    trace_schema = json.loads((schema_dir / "trace-step.schema.json").read_text())
    metrics_schema = json.loads((schema_dir / "metrics.schema.json").read_text())
    trace = []
    result = PhaseLoop[dict](
        phase=1,
        run_id="mock-schema",
        generated_by=RECORDED,
        budget=LoopBudget(max_iterations=1),
        emit=trace.append,
    ).run(
        gather=lambda *_args: {},
        propose=lambda *_args: {},
        critique=lambda *_args: True,
        revise=lambda artifact, *_args: artifact,
        check=lambda *_args: True,
    )
    Draft202012Validator(trace_schema["$defs"]["loop"]).validate(trace[-1]["loop"])
    metrics = {"loops": [result.metric(1)]}
    Draft202012Validator(metrics_schema["properties"]["loops"]).validate(metrics["loops"])
    wall_metrics = {"phase": 3, "iterations": 4, "stop_reason": "wall_clock", "usd": 1.73}
    Draft202012Validator(metrics_schema["properties"]["loops"]).validate([wall_metrics])
    wall_trace = {**trace[-1]["loop"], "stop_reason": "wall_clock"}
    Draft202012Validator(trace_schema["$defs"]["loop"]).validate(wall_trace)
    invalid = {**trace[-1], "loop": {**trace[-1]["loop"], "role": "invented"}}
    with pytest.raises(ValidationError):
        Draft202012Validator(trace_schema["$defs"]["loop"]).validate(invalid["loop"])
    invalid_digest = {**trace[-1]["loop"], "draft_sha256": "not-a-digest"}
    with pytest.raises(ValidationError):
        Draft202012Validator(trace_schema["$defs"]["loop"]).validate(invalid_digest)
