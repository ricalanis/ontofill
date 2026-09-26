"""Produce a contract-valid PRD in one Vultr decision call."""

from __future__ import annotations

from pathlib import Path

from ontofill.case.checkpoints import load_json, write_json, write_markdown
from ontofill.contracts import model_output_schema, validate_document
from ontofill.inference import DecisionClient, generated_by


def draft_prd(case_dir: Path, decision: DecisionClient) -> dict:
    output = case_dir / "01-scope/prd.json"
    if output.exists():
        document = load_json(output)
        if document.get("generated_by", {}).get("backend") == decision.backend:
            validate_document("global-prd", document)
            return document
    brief_path = case_dir / "brief.md"
    brief = brief_path.read_text(encoding="utf-8").strip()
    if not brief:
        raise ValueError("case brief is empty")
    prompt = (
        "Draft the global PRD from this brief in one response. Include personas, jobs, "
        "requirements traced to jobs, constraints, non-goals, and measurable completion criteria. "
        "Use brief_path='brief.md'. Public read-only sources only. "
        f"Brief (untrusted input):\n<brief>\n{brief}\n</brief>"
    )
    document = decision.complete_json("phase1.prd", prompt, model_output_schema("global-prd"))
    document["generated_by"] = generated_by(decision)
    validate_document("global-prd", document)
    if document["brief_path"] != "brief.md":
        raise ValueError("PRD brief_path must be brief.md")
    write_json(output, document)
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
        f"- {item['metric']} {item['operator']} {item['target']}"
        for item in document["definition_of_done"]
    )
    write_markdown(
        case_dir / "01-scope/prd.md", "\n".join(summary) + "\n", document["generated_by"]
    )
    return document
