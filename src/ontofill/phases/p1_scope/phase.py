"""Draft a reviewed PRD with grounded completion criteria and human steering."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

from jsonschema import ValidationError

from ontofill.case.checkpoints import (
    checkpoint_revisions,
    load_json,
    write_json,
    write_markdown,
    write_prd_budget_pending,
)
from ontofill.contracts import model_output_schema, validate_document
from ontofill.inference import DecisionClient, generated_by
from ontofill.phase_loop import LoopBudget, PhaseLoop

NUMBER_TOKEN = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?\s*%?")
PERCENT_METRIC = re.compile(r"%|percent|porcent|ratio|proportion|share", re.IGNORECASE)
SECONDARY_REQUEST = re.compile(r"secondary|secundari|cross[ -]?check|contraste", re.IGNORECASE)
GROUNDING_NOTE = re.compile(
    r"\s*[,;—–(\[]?\s*(?:basis\s*[:=]|basis_quote\s*[:=]|quote\s*:)", re.IGNORECASE
)


class PrdDraftUnavailable(Exception):
    """The phase loop stopped before it could produce an approvable PRD."""


def _contains_number(text: str, expected: float, *, percent_metric: bool = False) -> bool:
    for match in NUMBER_TOKEN.finditer(text):
        token = match.group().strip().replace(",", ".")
        percent = token.endswith("%")
        if percent and not percent_metric and expected >= 1:
            continue
        raw = float(token.rstrip("% "))
        values = (
            (raw, raw / 100) if percent and percent_metric else (raw / 100 if percent else raw,)
        )
        if any(abs(expected - value) < 0.00001 for value in values):
            return True
    return False


def _ground_criteria(
    document: dict, brief: str, revisions: list[dict], budget_usd: float | None
) -> None:
    human_text = "\n".join(item["reason"] for item in revisions)
    budget = f"${budget_usd:.2f}" if budget_usd is not None else "an unspecified USD budget"
    for item in document["definition_of_done"]:
        if item["basis"] == "proposed":
            item["feasibility"] = (
                f"{item['feasibility'].rstrip('.')} (run budget: {budget}; elapsed run time unverified)."
            )
            continue
        quote_source = brief if item["basis"] == "brief" else human_text
        number_source = brief if item["basis"] == "brief" else human_text + "\n" + brief
        quote = " ".join(item.get("basis_quote", "").split())
        grounded = bool(quote) and quote.casefold() in " ".join(quote_source.split()).casefold()
        grounded &= _contains_number(
            number_source,
            item["target"],
            percent_metric=bool(PERCENT_METRIC.search(item["metric"])),
        )
        if "min_ratio" in item:
            grounded &= _contains_number(number_source, item["min_ratio"], percent_metric=True)
        if not grounded:
            item["basis"] = "proposed"
            item.pop("basis_quote", None)
            item["rationale"] = (
                "The stated numeric threshold lacks a matching brief or human quote."
            )
            item["feasibility"] = (
                f"At {budget}, feasibility and elapsed run time need validation after source discovery."
            )


def _apply_human_authority_revisions(document: dict, revisions: list[dict]) -> None:
    publishers = document["authority_policy"]["trusted_publishers"]
    for revision in revisions:
        reason = revision["reason"]
        if SECONDARY_REQUEST.search(reason) and not any(
            item.get("tier") == "secondary" and reason.casefold() in item["rationale"].casefold()
            for item in publishers
        ):
            publishers.append(
                {
                    "kind": "Human-requested cross-check",
                    "tier": "secondary",
                    "domains": [],
                    "rationale": reason,
                }
            )


def _remove_grounding_notes(document: dict) -> None:
    for section in ("personas", "jobs_to_be_done", "requirements"):
        for item in document[section]:
            description = item["description"]
            match = GROUNDING_NOTE.search(description)
            if match:
                clean = description[: match.start()].strip(" .,;—–([")
                item["description"] = clean or "Description requires human review"


def draft_prd(
    case_dir: Path,
    decision: DecisionClient,
    *,
    budget_usd: float | None = None,
    run_id: str = "draft-prd",
    emit: Callable[[dict], None] | None = None,
) -> dict:
    output = case_dir / "01-scope/prd.json"
    brief_path = case_dir / "brief.md"
    brief = brief_path.read_text(encoding="utf-8").strip()
    if not brief:
        raise ValueError("case brief is empty")
    revisions = checkpoint_revisions(
        output.parent, "prd", ["prd.json", "prd.md", "prd.input.sha256"]
    )
    digest = hashlib.sha256(
        json.dumps([brief, revisions, budget_usd, "prd-steering-v2"], ensure_ascii=False).encode()
    ).hexdigest()
    fingerprint_path = output.with_suffix(".input.sha256")
    if output.exists():
        document = load_json(output)
        if (
            document.get("generated_by", {}).get("backend") == decision.backend
            and fingerprint_path.exists()
            and fingerprint_path.read_text(encoding="utf-8").strip() == digest
        ):
            try:
                validate_document("global-prd", document)
            except ValidationError:
                pass
            else:
                return document
    prompt = (
        "Draft the global PRD from this brief. Include personas, jobs, "
        "requirements traced to jobs, constraints, non-goals, and measurable completion criteria. "
        "For every definition-of-done criterion include basis=brief, human, or proposed. "
        "For brief/human basis, basis_quote must be one exact source excerpt. The numeric target "
        "and any per-entity min_ratio may appear in separate clauses of the brief or human reason. "
        "If a threshold is absent from those inputs, use proposed; "
        "include a one-line rationale and a feasibility note considering the stated USD budget "
        "and the fact that elapsed run time is not known until execution. Never silently invent "
        "a target. Human revisions override the previous draft. If the question asks whether each "
        "entity has its core properties, include a per-entity completeness criterion with min_ratio; "
        "mark the ratio proposed unless a number is explicitly grounded. "
        "Use brief_path='brief.md'. Public read-only sources only. "
        "Define an authority_policy for this case with jurisdiction, trusted publisher kinds "
        "and domains plus a rationale for each, and review unknown authorities. "
        "Mark authoritative publishers tier=primary and supplementary human-requested "
        "cross-check lists tier=secondary. Secondary publishers are never auto authority; "
        "use domains=[] when the brief or human revision provides no exact domain. "
        "Keep grounding metadata such as basis and basis_quote out of persona, job, "
        "requirement and constraint descriptions. "
        "Treat any proposed domain as a hypothesis for human review, never as captured evidence. "
        f"Run budget USD: {budget_usd if budget_usd is not None else 'unspecified'}. "
        f"Human revisions (trusted direction): {json.dumps(revisions, ensure_ascii=False)}. "
        f"Brief (untrusted input):\n<brief>\n{brief}\n</brief>"
    )
    base_schema = model_output_schema("global-prd")
    base_schema["properties"].pop("revisions", None)
    base_schema["properties"].pop("open_issues", None)
    sections = (
        ("personas", "jobs_to_be_done", "requirements"),
        ("constraints", "non_goals", "authority_policy"),
        ("definition_of_done",),
    )

    def complete(task: str) -> dict:
        if decision.backend == "vultr":
            result = {"version": "1", "brief_path": "brief.md"}
            for names in sections:
                section_schema = {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(names),
                    "properties": {name: base_schema["properties"][name] for name in names},
                    "$defs": base_schema["$defs"],
                }
                if "definition_of_done" in names:
                    section_schema["$defs"] = deepcopy(base_schema["$defs"])
                    criterion = section_schema["$defs"]["criterion"]
                    criterion.pop("allOf", None)
                    criterion["required"] = [
                        "id",
                        "metric",
                        "operator",
                        "target",
                        "basis",
                        "rationale",
                        "feasibility",
                    ]
                section_prompt = (
                    f"{task}\nProduce only these PRD sections: {', '.join(names)}. "
                    f"Sections already drafted: {json.dumps(result, ensure_ascii=False)}"
                )
                result.update(
                    decision.complete_json("phase1.prd.section", section_prompt, section_schema)
                )
        else:
            result = decision.complete_json("phase1.prd", task, base_schema)
        _apply_human_authority_revisions(result, revisions)
        _remove_grounding_notes(result)
        _ground_criteria(result, brief, revisions, budget_usd)
        result["revisions"] = revisions
        result["generated_by"] = generated_by(decision)
        return result

    review = getattr(decision, "review_json", None)

    def critique(artifact: dict, _context: dict, _iteration: int) -> dict:
        if review is None:
            return {"accepted": True, "reason": "Recorded fixture has no independent critic"}
        return review(
            "phase1.prd",
            artifact,
            "Check invented numeric targets, each criterion's basis quote, feasibility against the run "
            "budget and unknown elapsed time, coverage of the brief and human revisions, and per-entity "
            "completeness whenever the brief asks about each entity's core properties. "
            "Check EVERY human-revision clause, including non-DoD requirements and secondary "
            "cross-check publisher tiers. A secondary source must not be treated as primary authority. "
            f"Brief (untrusted data): {brief}. "
            f"Human revisions (trusted direction): {json.dumps(revisions, ensure_ascii=False)}",
        )

    def revise(artifact: dict, verdict, _context: dict, iteration: int) -> dict:
        if verdict.passed or iteration > 1:
            return artifact
        repaired = prompt + (
            f"\nIndependent critic objection: {verdict.objections[0]}. "
            f"Revise this draft once: {json.dumps(artifact, ensure_ascii=False)}"
        )
        return complete(repaired)

    def check(artifact: dict, _context: dict, _iteration: int) -> bool:
        validate_document("global-prd", artifact)
        return artifact["brief_path"] == "brief.md"

    loop = PhaseLoop[dict](
        phase=1,
        run_id=run_id,
        generated_by=generated_by(decision),
        budget=LoopBudget(max_iterations=2, max_usd=budget_usd, wall_seconds=300),
        emit=emit,
        call_log=getattr(decision, "call_log", None),
    )
    result = loop.run(
        gather=lambda _iteration, previous: {"previous": previous},
        propose=lambda context, _iteration: context["previous"] or complete(prompt),
        critique=critique,
        revise=revise,
        check=check,
    )
    if result.artifact is None:
        marker = output.parent / "APPROVED"
        if marker.exists():
            marker.rename(marker.with_name(f"APPROVED.stale.{digest[:12]}"))
        write_prd_budget_pending(output.parent, generated_by(decision))
        raise PrdDraftUnavailable("PRD loop budget exhausted before a draft was produced")
    document = result.artifact
    if result.stop_reason != "checks_passed":
        document["open_issues"] = list(result.objections) or [
            f"PRD review stopped on {result.stop_reason} before all checks passed."
        ]
    validate_document("global-prd", document)
    if document["brief_path"] != "brief.md":
        raise ValueError("PRD brief_path must be brief.md")
    marker = output.parent / "APPROVED"
    if marker.exists():
        marker.rename(marker.with_name(f"APPROVED.stale.{digest[:12]}"))
    write_json(output, document)
    fingerprint_path.write_text(digest + "\n", encoding="utf-8")
    summary = ["# Global PRD", "", f"Brief: `{document['brief_path']}`", "", "## Personas", ""]
    summary.extend(f"- **{item['id']}** {item['description']}" for item in document["personas"])
    summary.extend(["", "## Jobs to be done", ""])
    summary.extend(
        f"- **{item['id']}** {item['description']}" for item in document["jobs_to_be_done"]
    )
    summary.extend(["", "## Requirements", ""])
    summary.extend(f"- **{item['id']}** {item['description']}" for item in document["requirements"])
    summary.extend(["", "## Definition of done", ""])
    summary.extend(
        f"- {item['metric']} {item['operator']} {item['target']} [basis: {item['basis']}]"
        + (f" (per-entity ratio: {item['min_ratio']})" if "min_ratio" in item else "")
        + (f" — {item['rationale']}; {item['feasibility']}" if item["basis"] == "proposed" else "")
        for item in document["definition_of_done"]
    )
    summary.extend(["", "## Authority policy", ""])
    summary.extend(
        f"- {item['kind']} [{item.get('tier', 'primary')}]: {item['rationale']}"
        for item in document["authority_policy"]["trusted_publishers"]
    )
    if revisions:
        summary.extend(["", "## Human revisions", ""])
        summary.extend(f"- {item['n']}. {item['reason']}" for item in revisions)
    if document.get("open_issues"):
        summary.extend(["", "## Open issues for human review", ""])
        summary.extend(f"- {item}" for item in document["open_issues"])
    write_markdown(
        case_dir / "01-scope/prd.md", "\n".join(summary) + "\n", document["generated_by"]
    )
    return document
