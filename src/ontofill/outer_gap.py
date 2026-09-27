"""Code-owned outer DoD gap decision for bounded case runs."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from jsonschema import Draft202012Validator

from ontofill.refiner.export import _compare

MAX_OUTER_ITERATIONS = 3  # initial pass plus at most two reopen passes
REOPEN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reopen", "reason"],
    "properties": {
        "reopen": {"enum": [2, 3, 4, None]},
        "reason": {"type": "string", "minLength": 1},
    },
}
StopReason = Literal["checks_passed", "budget", "human", "max_iterations"]


@dataclass(frozen=True)
class Gap:
    criterion_id: str
    actual: float
    target: float
    operator: str
    properties: tuple[str, ...]
    iteration: int = 1

    def public_summary(self) -> dict:
        return {
            "criterion_id": self.criterion_id,
            "actual": self.actual,
            "target": self.target,
            "operator": self.operator,
            "properties": list(self.properties),
            "iteration": self.iteration,
        }


@dataclass(frozen=True)
class OuterDecision:
    iteration: int
    gaps: tuple[Gap, ...]
    reopen: int | None
    reason: str
    stop_reason: StopReason | None
    usage: dict
    usd: float


def gaps_from_metrics(
    metrics: dict,
    dod_queries: dict,
    ontology: dict,
    *,
    prior_iterations: dict[str, int] | None = None,
) -> tuple[Gap, ...]:
    """Use approved query operators; recorded previews can assess without satisfying DoD."""
    results = {item["criterion_id"]: item for item in metrics["dod"]}
    queries = dod_queries["queries"]
    if set(results) != {item["criterion_id"] for item in queries}:
        raise ValueError("metrics DoD rows differ from approved queries")
    default_fields = [item["id"] for item in ontology["properties"] if item["dod"]]
    gaps: list[Gap] = []
    for query in queries:
        result = results[query["criterion_id"]]
        if _compare(result["actual"], query["target"], query["operator"]):
            continue
        selected = query.get("properties", [])
        properties = default_fields if selected == "dod" or not selected else selected
        properties = [*properties, *(item["property"] for item in query.get("conditions", []))]
        gaps.append(
            Gap(
                query["criterion_id"],
                result["actual"],
                query["target"],
                query["operator"],
                tuple(dict.fromkeys(properties)),
                (prior_iterations or {}).get(query["criterion_id"], 0) + 1,
            )
        )
    return tuple(gaps)


def spent_usd(trace: list[dict]) -> float | None:
    """Return measured inference spend, or None if a recorded call was unpriced."""
    total = 0.0
    for step in trace:
        usage = step.get("usage")
        if not usage:
            continue
        cost = usage.get("est_usd")
        if cost is None:
            return None
        total += cost
    return total


def prior_reopens(trace: list[dict]) -> int:
    return sum(
        step.get("event") == "loop"
        and step.get("loop", {}).get("phase") == "outer"
        and step.get("loop", {}).get("role") == "decide"
        and step.get("executed", {}).get("reopen") in {2, 3, 4}
        for step in trace
    )


def prior_gap_iterations(trace: list[dict]) -> dict[str, int]:
    """Count prior reopened passes separately for each stable DoD criterion ID."""
    counts: dict[str, int] = defaultdict(int)
    for step in trace:
        if not (
            step.get("event") == "loop"
            and step.get("loop", {}).get("phase") == "outer"
            and step.get("loop", {}).get("role") == "decide"
            and step.get("executed", {}).get("reopen") in {2, 3, 4}
        ):
            continue
        for gap in step.get("observed", {}).get("gaps", []):
            criterion_id = gap.get("criterion_id")
            if isinstance(criterion_id, str) and criterion_id:
                counts[criterion_id] += 1
    return dict(counts)


def _zero_usage(provenance: dict) -> dict:
    return {
        "model": provenance["model"],
        "backend": provenance["backend"],
        "input_tokens": 0,
        "output_tokens": 0,
        "est_usd": 0,
    }


def decide_outer_gap(
    *,
    metrics: dict,
    dod_queries: dict,
    ontology: dict,
    objectives: dict,
    decision: object,
    provenance: dict,
    trace: list[dict],
    budget_usd: float | None,
) -> OuterDecision:
    """Let a typed model choose only among code-authorized phase transitions."""
    iteration = prior_reopens(trace) + 1
    gaps = gaps_from_metrics(
        metrics, dod_queries, ontology, prior_iterations=prior_gap_iterations(trace)
    )
    spent = spent_usd(trace)
    usage = _zero_usage(provenance)
    if not gaps:
        return OuterDecision(
            iteration,
            gaps,
            None,
            "All measured DoD targets met",
            "checks_passed",
            usage,
            spent or 0,
        )
    if iteration >= MAX_OUTER_ITERATIONS:
        return OuterDecision(
            iteration,
            gaps,
            None,
            "Outer iteration limit reached",
            "max_iterations",
            usage,
            spent or 0,
        )
    if spent is None or (budget_usd is not None and spent >= budget_usd):
        return OuterDecision(
            iteration, gaps, None, "Run inference budget exhausted", "budget", usage, spent or 0
        )

    prompt = (
        "Choose one bounded repair for the unmet approved DoD criteria. Choose phase 3 to "
        "find additional public sources, phase 4 to revise extraction for an existing source, "
        "phase 2 only when ontology or DoD mapping needs human review, or null when no safe "
        "repair is available. Never change an approved DoD target. Do not invent a source. "
        f"Gaps: {json.dumps([gap.public_summary() for gap in gaps], ensure_ascii=False)}. "
        f"Current source objectives: {json.dumps([{'id': item['id'], 'source_id': item['source_id'], 'target_fields': item['target_fields']} for item in objectives['objectives']], ensure_ascii=False)}. "
        f"Budget remaining USD: {None if budget_usd is None else budget_usd - spent}."
    )
    before = len(getattr(decision, "call_log", []))
    choice = decision.complete_json("outer.gap_decision", prompt, REOPEN_SCHEMA)
    Draft202012Validator(REOPEN_SCHEMA).validate(choice)
    calls = getattr(decision, "call_log", [])[before:]
    if calls:
        parts = [call.get("usage") for call in calls]
        if any(not isinstance(item, dict) or item.get("est_usd") is None for item in parts):
            return OuterDecision(
                iteration, gaps, None, "Unpriced gap decision", "budget", usage, spent
            )
        usage = {
            **parts[-1],
            "input_tokens": sum(item["input_tokens"] for item in parts),
            "output_tokens": sum(item["output_tokens"] for item in parts),
            "est_usd": sum(item["est_usd"] for item in parts),
        }
        spent += usage["est_usd"]
    if budget_usd is not None and spent >= budget_usd:
        return OuterDecision(
            iteration, gaps, None, "Run inference budget exhausted", "budget", usage, spent
        )
    reopen = choice["reopen"]
    stop_reason = "human" if reopen in {None, 2} else None
    return OuterDecision(iteration, gaps, reopen, choice["reason"], stop_reason, usage, spent)


def outer_trace_step(run_id: str, result: OuterDecision) -> dict:
    now = datetime.now(UTC).isoformat()
    usage = result.usage
    loop = {
        "phase": "outer",
        "iteration": result.iteration,
        "role": "decide",
        "model": usage["model"],
        "verdict": "reopen" if result.reopen is not None else "stop",
        "objections": [gap.criterion_id for gap in result.gaps],
    }
    if result.stop_reason is not None:
        loop["stop_reason"] = result.stop_reason
    return {
        "step_id": f"step:{uuid.uuid4().hex}",
        "run_id": run_id,
        "phase": 5,
        "source_id": None,
        "objective_id": None,
        "tdd_path": None,
        "mode": "D0",
        "event": "loop",
        "loop": loop,
        "usage": usage,
        "observed": {"gaps": [gap.public_summary() for gap in result.gaps]},
        "requested": {"tool": "outer.gap_decision"},
        "executed": {"reopen": result.reopen},
        "evaluated": {"reason": result.reason, "usd": result.usd},
        "parent_step_id": None,
        "value_ids": [],
        "ts": now,
        "generated_by": {"backend": usage["backend"], "model": usage["model"], "at": now},
    }
