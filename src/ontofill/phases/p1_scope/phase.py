"""Produce a contract-valid PRD in one Vultr decision call."""

from __future__ import annotations

import hashlib
from pathlib import Path

from jsonschema import ValidationError

from ontofill.case.checkpoints import load_json, write_json, write_markdown
from ontofill.contracts import model_output_schema, validate_document
from ontofill.inference import DecisionClient, generated_by


def draft_prd(case_dir: Path, decision: DecisionClient) -> dict:
    output = case_dir / "01-scope/prd.json"
    brief_path = case_dir / "brief.md"
    brief = brief_path.read_text(encoding="utf-8").strip()
    if not brief:
        raise ValueError("case brief is empty")
    digest = hashlib.sha256(brief.encode()).hexdigest()
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
        "Draft the global PRD from this brief in one response. Include personas, jobs, "
        "requirements traced to jobs, constraints, non-goals, and measurable completion criteria. "
        "Use brief_path='brief.md'. Public read-only sources only. "
        "Define an authority_policy for this case with jurisdiction, trusted publisher kinds "
        "and domains plus a rationale for each, and review unknown authorities. "
        "Treat any proposed domain as a hypothesis for human review, never as captured evidence. "
        f"Brief (untrusted input):\n<brief>\n{brief}\n</brief>"
    )
    document = decision.complete_json("phase1.prd", prompt, model_output_schema("global-prd"))
    document["generated_by"] = generated_by(decision)
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
        f"- {item['metric']} {item['operator']} {item['target']}"
        for item in document["definition_of_done"]
    )
    write_markdown(
        case_dir / "01-scope/prd.md", "\n".join(summary) + "\n", document["generated_by"]
    )
    return document
