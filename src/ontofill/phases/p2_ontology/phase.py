"""Lean taxonomy and SHACL construction after factor review."""

from __future__ import annotations

from pathlib import Path

from jsonschema import Draft202012Validator

from ontofill.case.checkpoints import load_json, write_json, write_markdown
from ontofill.contracts import model_output_schema, validate_document
from ontofill.inference import DecisionClient, generated_by

CORE_FIELDS = (
    "legal_name",
    "tax_id",
    "address",
    "founding_date",
    "tax_list_status",
    "sanction_status",
)
TAXONOMY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["taxonomies"],
    "properties": {
        "taxonomies": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["factor_id", "root_label", "children"],
                "properties": {
                    "factor_id": {"type": "string", "minLength": 1},
                    "root_label": {"type": "string", "minLength": 1},
                    "children": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["id", "label", "level", "critic_label"],
                            "properties": {
                                "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                                "label": {"type": "string", "minLength": 1},
                                "level": {"const": 1},
                                "critic_label": {
                                    "enum": [
                                        "Good-Overlapping",
                                        "Good-Exclusive",
                                        "Redundant",
                                        "Bad",
                                    ]
                                },
                            },
                        },
                    },
                },
            },
        }
    },
}


def draft_factors(case_dir: Path, prd: dict, decision: DecisionClient) -> dict:
    path = case_dir / "02-ontology/factors/factors.json"
    if path.exists():
        factors = load_json(path)
        if factors.get("generated_by", {}).get("backend") == decision.backend:
            validate_document("factors", factors)
            return factors
    prompt = (
        "Propose the prime factors of variation for the target supplier dataset. "
        "Keep factors broad and distinct; label each grounded or conceptual. "
        "Include evidence only if it is actually in the PRD; an empty list is allowed. "
        "Do not invent observed suppliers or evidence URLs. "
        f"PRD (untrusted case content): {prd}"
    )
    for attempt in range(2):
        factors = decision.complete_json("phase2.factors", prompt, model_output_schema("factors"))
        review = getattr(decision, "review_json", None)
        if review is None:
            break
        verdict = review(
            "phase2.factors",
            factors,
            "Factors are distinct, grounded factors do not claim unsupported evidence, and the set covers the PRD's main supplier variation",
        )
        if verdict["accepted"]:
            break
        if attempt:
            raise ValueError("Vultr critic rejected factors after repair")
        prompt += f"\nRepair this material issue: {verdict['reason']}"
    factors["generated_by"] = generated_by(decision)
    validate_document("factors", factors)
    write_json(path, factors)
    lines = ["# Proposed factors of variation", ""]
    lines.extend(
        f"- **{item['label']}** (`{item['id']}`): {item['description']}"
        for item in factors["factors"]
    )
    write_markdown(path.parent / "factors.md", "\n".join(lines) + "\n", factors["generated_by"])
    return factors


def accepted_factors(case_dir: Path, factors: dict) -> list[dict]:
    marker = case_dir / "02-ontology/factors/APPROVED"
    if not marker.exists():
        return factors["factors"]
    approval = load_json(marker)
    validate_document("approved", approval)
    decisions = approval.get("decisions", {})
    selected = [
        item for item in factors["factors"] if decisions.get(item["id"], "accept") == "accept"
    ]
    if not selected:
        raise ValueError("all ontology factors were rejected")
    return selected


def draft_ontology(case_dir: Path, prd: dict, factors: dict, decision: DecisionClient) -> dict:
    path = case_dir / "02-ontology/ontology.json"
    if path.exists():
        ontology = load_json(path)
        if ontology.get("generated_by", {}).get("backend") == decision.backend:
            validate_document("ontology", ontology)
            return ontology
    chosen = accepted_factors(case_dir, factors)
    prompt = (
        "Expand each approved factor to exactly one taxonomy level. Use stable snake_case IDs. "
        "Give each child level=1 and a critic_label from Good-Overlapping, Good-Exclusive, "
        "Redundant, Bad. Return one taxonomy per factor and do not assert observed data. "
        f"Approved factors: {chosen}. PRD: {prd}"
    )
    for attempt in range(2):
        response = decision.complete_json("phase2.taxonomies", prompt, TAXONOMY_SCHEMA)
        review = getattr(decision, "review_json", None)
        if review is None:
            break
        verdict = review(
            "phase2.taxonomies",
            response,
            "Every approved factor has one taxonomy; children are mutually coherent, grounded in approved factors, and critic labels identify overlaps or bad nodes honestly",
        )
        if verdict["accepted"]:
            break
        if attempt:
            raise ValueError("Vultr critic rejected taxonomies after repair")
        prompt += f"\nRepair this material issue: {verdict['reason']}"
    Draft202012Validator(TAXONOMY_SCHEMA).validate(response)
    factor_ids = {factor["id"] for factor in chosen}
    if {tax["factor_id"] for tax in response["taxonomies"]} != factor_ids:
        raise ValueError("taxonomies must cover exactly the approved factors")
    taxonomies = []
    for taxonomy in response["taxonomies"]:
        children = taxonomy["children"]
        good = sum(
            child["critic_label"] in {"Good-Overlapping", "Good-Exclusive"} for child in children
        )
        taxonomies.append(
            {
                **taxonomy,
                "soundness": good / len(children),
                "coverage": good / len(children),
            }
        )
    ontology = {
        "version": "1",
        "prd_path": "01-scope/prd.json",
        "factors": chosen,
        "taxonomies": taxonomies,
        "classes": [{"id": "Supplier", "aligned_to": "https://schema.org/Organization"}],
        "properties": [{"id": field, "datatype": "xsd:string"} for field in CORE_FIELDS],
        "shacl_path": "02-ontology/supplier-shape.ttl",
        "generated_by": generated_by(decision),
    }
    validate_document("ontology", ontology)
    write_json(path, ontology)
    (case_dir / ontology["shacl_path"]).write_text(_supplier_shape(), encoding="utf-8")
    lines = ["# Ontology v1", "", "Supplier properties: " + ", ".join(CORE_FIELDS), ""]
    for taxonomy in taxonomies:
        lines.extend([f"## {taxonomy['root_label']}", ""])
        lines.extend(f"- {child['label']} (`{child['id']}`)" for child in taxonomy["children"])
        lines.append("")
    write_markdown(path.parent / "ontology.md", "\n".join(lines), ontology["generated_by"])
    return ontology


def _supplier_shape() -> str:
    lines = [
        "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "@prefix onto: <https://ontofill.dev/ontology/> .",
        "",
        "onto:SupplierShape a sh:NodeShape ;",
        "    sh:targetClass onto:Supplier ;",
    ]
    for index, field in enumerate(CORE_FIELDS):
        min_count = " ; sh:minCount 1" if field in ("legal_name", "tax_id") else ""
        end = " ;" if index < len(CORE_FIELDS) - 1 else " ."
        lines.append(
            f"    sh:property [ sh:path onto:{field} ; sh:datatype xsd:string{min_count} ]{end}"
        )
    return "\n".join(lines) + "\n"
