"""Draft a reviewed PRD with grounded completion criteria and human steering."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from jsonschema import ValidationError

from ontofill.case.checkpoints import (
    checkpoint_revisions,
    load_json,
    write_json,
    write_markdown,
)
from ontofill.contracts import model_output_schema, validate_document
from ontofill.inference import DecisionClient, generated_by

NUMBER_TOKEN = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?\s*%?")


def _contains_number(quote: str, expected: float) -> bool:
    for match in NUMBER_TOKEN.finditer(quote):
        token = match.group().strip().replace(",", ".")
        ratio = token.endswith("%")
        value = float(token.rstrip("% ")) / (100 if ratio else 1)
        if abs(value - expected) < 0.00001:
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
        source = brief if item["basis"] == "brief" else human_text
        quote = " ".join(item["basis_quote"].split())
        grounded = quote.casefold() in " ".join(source.split()).casefold()
        grounded &= _contains_number(quote, item["target"])
        if "min_ratio" in item:
            grounded &= _contains_number(quote, item["min_ratio"])
        if not grounded:
            item["basis"] = "proposed"
            item.pop("basis_quote", None)
            item["rationale"] = (
                "The stated numeric threshold lacks a matching brief or human quote."
            )
            item["feasibility"] = (
                f"At {budget}, feasibility and elapsed run time need validation after source discovery."
            )


def draft_prd(case_dir: Path, decision: DecisionClient, *, budget_usd: float | None = None) -> dict:
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
        "For brief/human basis, basis_quote must be an exact source excerpt containing every numeric "
        "target, including any per-entity min_ratio. If the exact threshold is absent, use proposed; "
        "include a one-line rationale and a feasibility note considering the stated USD budget "
        "and the fact that elapsed run time is not known until execution. Never silently invent "
        "a target. Human revisions override the previous draft. If the question asks whether each "
        "entity has its core properties, include a per-entity completeness criterion with min_ratio; "
        "mark the ratio proposed unless a number is explicitly grounded. "
        "Use brief_path='brief.md'. Public read-only sources only. "
        "Define an authority_policy for this case with jurisdiction, trusted publisher kinds "
        "and domains plus a rationale for each, and review unknown authorities. "
        "Treat any proposed domain as a hypothesis for human review, never as captured evidence. "
        f"Run budget USD: {budget_usd if budget_usd is not None else 'unspecified'}. "
        f"Human revisions (trusted direction): {json.dumps(revisions, ensure_ascii=False)}. "
        f"Brief (untrusted input):\n<brief>\n{brief}\n</brief>"
    )
    base_schema = model_output_schema("global-prd")
    base_schema["properties"].pop("revisions", None)
    document = decision.complete_json("phase1.prd", prompt, base_schema)
    provenance = generated_by(decision)
    _ground_criteria(document, brief, revisions, budget_usd)
    review = getattr(decision, "review_json", None)
    if review is not None:
        verdict = review(
            "phase1.prd",
            document,
            "Check invented numeric targets, each criterion's basis quote, feasibility against the run "
            "budget and unknown elapsed time, coverage of the brief and human revisions, and per-entity "
            "completeness whenever the brief asks about each entity's core properties",
        )
        if not verdict["accepted"]:
            repaired = prompt + (
                f"\nIndependent critic objection: {verdict['reason']}. "
                f"Revise this draft once: {json.dumps(document, ensure_ascii=False)}"
            )
            document = decision.complete_json("phase1.prd", repaired, base_schema)
            provenance = generated_by(decision)
            _ground_criteria(document, brief, revisions, budget_usd)
    document["revisions"] = revisions
    document["generated_by"] = provenance
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
    if revisions:
        summary.extend(["", "## Human revisions", ""])
        summary.extend(f"- {item['n']}. {item['reason']}" for item in revisions)
    write_markdown(
        case_dir / "01-scope/prd.md", "\n".join(summary) + "\n", document["generated_by"]
    )
    return document
