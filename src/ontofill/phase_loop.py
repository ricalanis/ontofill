"""Bounded, code-owned phase loop with observable stage decisions.

Phase modules provide the content of each stage. This runner owns their order,
the stopping rules, budget accounting, and trace shape.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, Literal, TypeVar

T = TypeVar("T")
StopReason = Literal["checks_passed", "budget", "human", "max_iterations"]
Role = Literal["gather", "propose", "critique", "revise", "check", "decide"]


@dataclass(frozen=True)
class LoopBudget:
    max_iterations: int = 3
    max_usd: float | None = None
    wall_seconds: float = 300

    def __post_init__(self) -> None:
        if self.max_iterations < 1 or self.wall_seconds <= 0:
            raise ValueError("loop iteration and wall budgets must be positive")
        if self.max_usd is not None and self.max_usd < 0:
            raise ValueError("loop USD budget must be nonnegative")


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    objections: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoopResult(Generic[T]):
    artifact: T | None
    iterations: int
    stop_reason: StopReason
    usd: float
    objections: tuple[str, ...]

    def metric(self, phase: int | Literal["outer"]) -> dict:
        """Return the CONTRACT v0.9.6 metrics.loops row."""
        return {
            "phase": phase,
            "iterations": self.iterations,
            "stop_reason": self.stop_reason,
            "usd": self.usd,
        }


def _critique(result: Mapping | bool) -> CheckResult:
    if isinstance(result, bool):
        return CheckResult(result, () if result else ("critic rejected the draft",))
    accepted = result.get("accepted")
    if not isinstance(accepted, bool):
        raise TypeError("critic verdict must include boolean accepted")
    reason = result.get("reason")
    if not accepted and (not isinstance(reason, str) or not reason.strip()):
        raise ValueError("rejected critic verdict needs a reason")
    return CheckResult(accepted, () if accepted else (reason.strip(),))


def _check(result: CheckResult | bool) -> CheckResult:
    if isinstance(result, CheckResult):
        return result
    if isinstance(result, bool):
        return CheckResult(result, () if result else ("phase exit check failed",))
    raise TypeError("phase check must return bool or CheckResult")


class PhaseLoop(Generic[T]):
    """Run one phase until its code check and critic pass, or a bound stops it.

    ``call_log`` is the mutable Vultr decision client's log. Only its usage and
    model metadata are read; prompts and responses never enter the trace.
    ``emit`` receives one schema-valid trace-step dictionary per stage.
    """

    def __init__(
        self,
        *,
        phase: int | Literal["outer"],
        run_id: str,
        generated_by: Mapping[str, str],
        budget: LoopBudget,
        emit: Callable[[dict], None] | None = None,
        call_log: Sequence[Mapping] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if phase != "outer" and (not isinstance(phase, int) or phase not in range(1, 6)):
            raise ValueError("loop phase must be 1..5 or outer")
        if not run_id:
            raise ValueError("run_id is required")
        if generated_by.get("backend") not in {"recorded", "vultr"}:
            raise ValueError("phase loop needs a recorded or Vultr run provenance")
        if budget.max_usd is not None and generated_by["backend"] == "vultr" and call_log is None:
            raise ValueError("a live USD budget requires the decision call log")
        self.phase = phase
        self.run_id = run_id
        self.generated_by = dict(generated_by)
        self.budget = budget
        self.emit = emit
        self.call_log = call_log
        self.monotonic = monotonic
        self.usd = 0.0
        self._unknown_cost = False
        self._started: float | None = None
        self._used = False

    def _budget_reached(self) -> bool:
        assert self._started is not None
        return (
            self.monotonic() - self._started >= self.budget.wall_seconds
            or self._unknown_cost
            or (self.budget.max_usd is not None and self.usd >= self.budget.max_usd)
        )

    def _stage(
        self,
        role: Role,
        iteration: int,
        call: Callable[[], T],
        *,
        verdict: Callable[[T], str] | None = None,
        objections: Callable[[T], tuple[str, ...]] | None = None,
    ) -> T:
        before = len(self.call_log) if self.call_log is not None else 0
        result = call()
        calls = list(self.call_log[before:]) if self.call_log is not None else []
        usage = self._usage(calls)
        self._emit(
            iteration,
            role,
            usage=usage,
            verdict=verdict(result) if verdict else "completed",
            objections=objections(result) if objections else (),
            executed={"status": "completed", "calls": len(calls)},
        )
        return result

    def _usage(self, calls: list[Mapping]) -> dict:
        last_usage: Mapping | None = None
        input_tokens = 0
        output_tokens = 0
        stage_usd = 0.0
        for call in calls:
            usage = call.get("usage")
            if not isinstance(usage, Mapping):
                self._unknown_cost = True
                continue
            last_usage = usage
            input_tokens += usage.get("input_tokens", 0)
            output_tokens += usage.get("output_tokens", 0)
            cost = usage.get("est_usd")
            if isinstance(cost, (int, float)) and cost >= 0:
                self.usd += float(cost)
                stage_usd += float(cost)
            else:
                self._unknown_cost = True
        if last_usage is None:
            return {
                "model": self.generated_by["model"],
                "backend": self.generated_by["backend"],
                "input_tokens": 0,
                "output_tokens": 0,
                "est_usd": 0,
            }
        return {
            **last_usage,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "est_usd": None if self._unknown_cost else stage_usd,
        }

    def _emit(
        self,
        iteration: int,
        role: Role,
        *,
        usage: Mapping | None = None,
        verdict: str,
        objections: tuple[str, ...] = (),
        stop_reason: StopReason | None = None,
        executed: dict | None = None,
    ) -> None:
        if self.emit is None:
            return
        now = datetime.now(UTC).isoformat()
        usage = usage or self._usage([])
        by = {
            "backend": usage["backend"],
            "model": usage["model"],
            "at": now,
        }
        loop = {
            "phase": self.phase,
            "iteration": iteration,
            "role": role,
            "model": usage["model"],
            "verdict": verdict,
            "objections": list(objections),
        }
        if stop_reason is not None:
            loop["stop_reason"] = stop_reason
        self.emit(
            {
                "step_id": f"loop-{self.phase}-{iteration}-{role}-{uuid.uuid4().hex[:12]}",
                "run_id": self.run_id,
                "phase": 5 if self.phase == "outer" else self.phase,
                "source_id": None,
                "objective_id": None,
                "tdd_path": None,
                "mode": "D0",
                "event": "loop",
                "loop": loop,
                "usage": dict(usage),
                "observed": {"iteration": iteration},
                "requested": {"role": role},
                "executed": executed or {"status": "completed"},
                "evaluated": {"verdict": verdict, "usd": self.usd},
                "parent_step_id": None,
                "value_ids": [],
                "ts": now,
                "generated_by": by,
            }
        )

    def _finish(
        self,
        artifact: T | None,
        iteration: int,
        reason: StopReason,
        objections: tuple[str, ...],
    ) -> LoopResult[T]:
        self._emit(
            max(1, iteration),
            "decide",
            verdict="stop" if reason != "max_iterations" else "open_issues",
            objections=objections,
            stop_reason=reason,
            executed={"stop_reason": reason},
        )
        return LoopResult(artifact, iteration, reason, self.usd, objections)

    def run(
        self,
        *,
        gather: Callable[[int, T | None], object],
        propose: Callable[[object, int], T],
        critique: Callable[[T, object, int], Mapping | bool],
        revise: Callable[[T, CheckResult, object, int], T],
        check: Callable[[T, object, int], CheckResult | bool],
        gate: Callable[[T, int], bool | None] | None = None,
    ) -> LoopResult[T]:
        if self._used:
            raise RuntimeError("a PhaseLoop instance runs once")
        self._used = True
        self._started = self.monotonic()
        artifact: T | None = None
        objections: tuple[str, ...] = ()
        if self._budget_reached():
            return self._finish(None, 0, "budget", ())
        for iteration in range(1, self.budget.max_iterations + 1):
            context = self._stage(
                "gather", iteration, lambda n=iteration, previous=artifact: gather(n, previous)
            )
            if self._budget_reached():
                return self._finish(artifact, iteration, "budget", objections)
            artifact = self._stage(
                "propose", iteration, lambda value=context, n=iteration: propose(value, n)
            )
            if self._budget_reached():
                return self._finish(artifact, iteration, "budget", objections)
            critic = self._stage(
                "critique",
                iteration,
                lambda draft=artifact, value=context, n=iteration: _critique(
                    critique(draft, value, n)
                ),
                verdict=lambda result: "accepted" if result.passed else "rejected",
                objections=lambda result: result.objections,
            )
            if self._budget_reached():
                return self._finish(artifact, iteration, "budget", critic.objections)
            artifact = self._stage(
                "revise",
                iteration,
                lambda draft=artifact, review=critic, value=context, n=iteration: revise(
                    draft, review, value, n
                ),
            )
            if self._budget_reached():
                return self._finish(artifact, iteration, "budget", critic.objections)
            checked = self._stage(
                "check",
                iteration,
                lambda draft=artifact, value=context, n=iteration: _check(check(draft, value, n)),
                verdict=lambda result: "passed" if result.passed else "failed",
                objections=lambda result: result.objections,
            )
            objections = (*critic.objections, *checked.objections)
            if self._budget_reached():
                return self._finish(artifact, iteration, "budget", objections)
            if critic.passed and checked.passed:
                if gate is not None:
                    allowed = gate(artifact, iteration)
                    if self._budget_reached():
                        return self._finish(artifact, iteration, "budget", objections)
                    if allowed is not True:
                        return self._finish(artifact, iteration, "human", objections)
                return self._finish(artifact, iteration, "checks_passed", ())
            if iteration < self.budget.max_iterations:
                self._emit(
                    iteration,
                    "decide",
                    verdict="continue",
                    objections=objections,
                    executed={"next_iteration": iteration + 1},
                )
        return self._finish(artifact, self.budget.max_iterations, "max_iterations", objections)
