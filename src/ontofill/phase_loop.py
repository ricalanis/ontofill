"""Bounded, code-owned phase loop with observable stage decisions.

Phase modules provide the content of each stage. This runner owns their order,
the stopping rules, budget accounting, and trace shape.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, Literal, TypeVar

T = TypeVar("T")
StopReason = Literal["checks_passed", "budget", "wall_clock", "human", "max_iterations"]
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


def _draft_sha256(artifact: object) -> str | None:
    if artifact is None:
        return None

    def content(value: object) -> object:
        if isinstance(value, Mapping):
            return {key: content(item) for key, item in value.items() if key != "generated_by"}
        if isinstance(value, (list, tuple)):
            return [content(item) for item in value]
        return value

    try:
        encoded = json.dumps(
            content(artifact), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded.encode()).hexdigest()


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

    def _stop_reason(self) -> StopReason | None:
        assert self._started is not None
        if self.monotonic() - self._started >= self.budget.wall_seconds:
            return "wall_clock"
        if self._unknown_cost or (
            self.budget.max_usd is not None and self.usd >= self.budget.max_usd
        ):
            return "budget"
        return None

    def _stage(
        self,
        role: Role,
        iteration: int,
        call: Callable[[], T],
        *,
        verdict: Callable[[T], str] | None = None,
        objections: Callable[[T], tuple[str, ...]] | None = None,
        draft: object | None = None,
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
            draft_sha256=_draft_sha256(result if role in {"propose", "revise"} else draft),
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
                "model": "none",
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
        draft_sha256: str | None = None,
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
            "verdict": verdict,
            "objections": list(objections),
        }
        if usage["model"] != "none":
            loop["model"] = usage["model"]
        if stop_reason is not None:
            loop["stop_reason"] = stop_reason
        if draft_sha256 is not None:
            loop["draft_sha256"] = draft_sha256
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
            draft_sha256=_draft_sha256(artifact),
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
        objection_addressed: Callable[[T, T, str], bool] | None = None,
    ) -> LoopResult[T]:
        if self._used:
            raise RuntimeError("a PhaseLoop instance runs once")
        self._used = True
        self._started = self.monotonic()
        artifact: T | None = None
        objections: tuple[str, ...] = ()
        prior_rejections: list[tuple[T, str]] = []
        if stop_reason := self._stop_reason():
            return self._finish(None, 0, stop_reason, ())
        for iteration in range(1, self.budget.max_iterations + 1):
            context = self._stage(
                "gather", iteration, lambda n=iteration, previous=artifact: gather(n, previous)
            )
            if stop_reason := self._stop_reason():
                return self._finish(artifact, iteration, stop_reason, objections)
            artifact = self._stage(
                "propose", iteration, lambda value=context, n=iteration: propose(value, n)
            )
            if stop_reason := self._stop_reason():
                return self._finish(artifact, iteration, stop_reason, objections)

            def reviewed(
                draft: T = artifact, gathered: object = context, number: int = iteration
            ) -> CheckResult:
                verdict = _critique(critique(draft, gathered, number))
                if not verdict.passed or not prior_rejections:
                    return verdict
                unresolved = []
                for rejected_draft, objection in prior_rejections:
                    if objection_addressed is None:
                        changed = _draft_sha256(rejected_draft) != _draft_sha256(draft)
                    else:
                        changed = objection_addressed(rejected_draft, draft, objection)
                    if not changed:
                        unresolved.append(f"Prior objection subject unchanged: {objection}")
                return CheckResult(False, tuple(unresolved)) if unresolved else verdict

            critic = self._stage(
                "critique",
                iteration,
                reviewed,
                verdict=lambda result: "accepted" if result.passed else "rejected",
                objections=lambda result: result.objections,
                draft=artifact,
            )
            if stop_reason := self._stop_reason():
                return self._finish(artifact, iteration, stop_reason, critic.objections)
            if not critic.passed:
                prior_rejections.extend(
                    (deepcopy(artifact), objection)
                    for objection in critic.objections
                    if not objection.startswith("Prior objection subject unchanged:")
                )
            artifact = self._stage(
                "revise",
                iteration,
                lambda draft=artifact, review=critic, value=context, n=iteration: revise(
                    draft, review, value, n
                ),
            )
            if stop_reason := self._stop_reason():
                return self._finish(artifact, iteration, stop_reason, critic.objections)
            checked = self._stage(
                "check",
                iteration,
                lambda draft=artifact, value=context, n=iteration: _check(check(draft, value, n)),
                verdict=lambda result: "passed" if result.passed else "failed",
                objections=lambda result: result.objections,
                draft=artifact,
            )
            objections = (*critic.objections, *checked.objections)
            if stop_reason := self._stop_reason():
                return self._finish(artifact, iteration, stop_reason, objections)
            if critic.passed and checked.passed:
                if gate is not None:
                    allowed = gate(artifact, iteration)
                    if stop_reason := self._stop_reason():
                        return self._finish(artifact, iteration, stop_reason, objections)
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
                    draft_sha256=_draft_sha256(artifact),
                )
        return self._finish(artifact, self.budget.max_iterations, "max_iterations", objections)
