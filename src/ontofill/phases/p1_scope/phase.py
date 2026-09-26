"""Produce a contract-valid PRD in one Vultr decision call."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from ontofill.case.checkpoints import load_json, write_json, write_markdown
from ontofill.contracts import model_output_schema, validate_document
from ontofill.inference import DecisionClient, generated_by

RATIO_TOKEN = re.compile(
    r"(?<!\d)(?:100|[1-9]?\d)(?:[.,]\d+)?\s*(?:%|percent\b|por\s+ciento\b|pct\b)"
    r"|(?<!\d)(?:0[.,]\d+|1[.,]0+)\b",
    re.IGNORECASE,
)
RATIO_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ratios"],
    "properties": {
        "ratios": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["criterion_id", "min_ratio", "evidence_quote"],
                "properties": {
                    "criterion_id": {"type": "string", "minLength": 1},
                    "min_ratio": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence_quote": {"type": "string", "minLength": 1},
                },
            },
        }
    },
}


def _ratio_in_quote(quote: str, ratio: float) -> bool:
    for match in RATIO_TOKEN.finditer(quote):
        token = match.group().lower().replace(",", ".")
        number = re.match(r"\d+(?:\.\d+)?", token)
        if number is None:
            continue
        parsed = float(number.group())
        if parsed > 1 or any(suffix in token for suffix in ("%", "percent", "por", "pct")):
            parsed /= 100
        if abs(parsed - ratio) <= 0.005:
            return True
    return False


def _infer_dod_ratios(brief: str, document: dict, decision: DecisionClient) -> None:
    criteria = document["definition_of_done"]
    if not RATIO_TOKEN.search(brief + " " + " ".join(item["metric"] for item in criteria)):
        return
    prompt = (
        "For each criterion that explicitly requires a per-entity share of required properties, "
        "return its criterion_id, min_ratio as a number from 0 to 1, and an exact quote that states "
        "that threshold in the brief or criterion metric. Omit all other criteria. "
        "Return an empty ratios list if no per-entity threshold is explicit. Do not infer a default. "
        f"Brief: {brief}\nCriteria: {json.dumps(criteria, ensure_ascii=False)}"
    )
    result = decision.complete_json("phase1.dod_thresholds", prompt, RATIO_SCHEMA)
    Draft202012Validator(RATIO_SCHEMA).validate(result)
    by_id = {item["id"]: item for item in criteria}
    seen: set[str] = set()
    for item in result["ratios"]:
        criterion_id = item["criterion_id"]
        if criterion_id not in by_id or criterion_id in seen:
            raise ValueError("DoD ratio references an unknown or repeated criterion")
        seen.add(criterion_id)
        quote = " ".join(item["evidence_quote"].split())
        evidence_text = " ".join((brief, by_id[criterion_id]["metric"]))
        normalized_evidence = " ".join(evidence_text.split()).casefold()
        if quote.casefold() not in normalized_evidence or not _ratio_in_quote(
            quote, item["min_ratio"]
        ):
            raise ValueError("DoD ratio lacks matching threshold evidence")
        by_id[criterion_id]["min_ratio"] = item["min_ratio"]


def draft_prd(case_dir: Path, decision: DecisionClient) -> dict:
    output = case_dir / "01-scope/prd.json"
    brief_path = case_dir / "brief.md"
    brief = brief_path.read_text(encoding="utf-8").strip()
    if not brief:
        raise ValueError("case brief is empty")
    digest = hashlib.sha256((brief + "\n\nprd-thresholds-v1").encode()).hexdigest()
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
    base_schema = model_output_schema("global-prd")
    base_schema["$defs"]["criterion"]["properties"].pop("min_ratio", None)
    document = decision.complete_json("phase1.prd", prompt, base_schema)
    provenance = generated_by(decision)
    _infer_dod_ratios(brief, document, decision)
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
        f"- {item['metric']} {item['operator']} {item['target']}"
        + (f" (per-entity ratio: {item['min_ratio']})" if "min_ratio" in item else "")
        for item in document["definition_of_done"]
    )
    write_markdown(
        case_dir / "01-scope/prd.md", "\n".join(summary) + "\n", document["generated_by"]
    )
    return document
