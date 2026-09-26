"""Lean taxonomy and SHACL construction after factor review."""

from __future__ import annotations

from pathlib import Path

from jsonschema import Draft202012Validator

from ontofill.case.checkpoints import load_json, write_json
from ontofill.inference import DecisionClient

CORE_FIELDS = (
    "legal_name",
    "tax_id",
    "address",
    "founding_date",
    "tax_list_status",
    "sanction_status",
)
FACTOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["factors"],
    "properties": {
        "factors": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "label", "description"],
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                    "label": {"type": "string", "minLength": 1},
                    "description": {"type": "string", "minLength": 1},
                },
            },
        }
    },
}
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
                            "required": ["id", "label"],
                            "properties": {
                                "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                                "label": {"type": "string", "minLength": 1},
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
        Draft202012Validator(FACTOR_SCHEMA).validate(factors)
        return factors
    prompt = (
        "Propose the prime factors of variation for the target supplier dataset. "
        "Keep factors broad and distinct; do not invent observed suppliers. "
        f"PRD (untrusted case content): {prd}"
    )
    factors = decision.complete_json("phase2.factors", prompt, FACTOR_SCHEMA)
    Draft202012Validator(FACTOR_SCHEMA).validate(factors)
    write_json(path, factors)
    lines = ["# Proposed factors of variation", ""]
    lines.extend(
        f"- **{item['label']}** (`{item['id']}`): {item['description']}"
        for item in factors["factors"]
    )
    (path.parent / "factors.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return factors


def draft_ontology(case_dir: Path, prd: dict, factors: dict, decision: DecisionClient) -> dict:
    path = case_dir / "02-ontology/ontology.json"
    if path.exists():
        return load_json(path)
    prompt = (
        "Expand each approved factor to exactly one taxonomy level. Use stable snake_case IDs. "
        "Return one taxonomy per factor and do not assert these categories were observed. "
        f"Approved factors: {factors}. PRD: {prd}"
    )
    response = decision.complete_json("phase2.taxonomies", prompt, TAXONOMY_SCHEMA)
    Draft202012Validator(TAXONOMY_SCHEMA).validate(response)
    factor_ids = {factor["id"] for factor in factors["factors"]}
    if {tax["factor_id"] for tax in response["taxonomies"]} != factor_ids:
        raise ValueError("taxonomies must cover exactly the approved factors")
    ontology = {
        "version": "1",
        "prd_path": "01-scope/prd.json",
        "factors": factors["factors"],
        "taxonomies": response["taxonomies"],
        "classes": [{"id": "Supplier", "aligned_to": "https://schema.org/Organization"}],
        "properties": [{"id": field, "datatype": "xsd:string"} for field in CORE_FIELDS],
        "shacl_path": "02-ontology/supplier-shape.ttl",
    }
    write_json(path, ontology)
    (case_dir / ontology["shacl_path"]).write_text(_supplier_shape(), encoding="utf-8")
    lines = ["# Ontology v1", "", "Supplier properties: " + ", ".join(CORE_FIELDS), ""]
    for taxonomy in response["taxonomies"]:
        lines.extend([f"## {taxonomy['root_label']}", ""])
        lines.extend(f"- {child['label']} (`{child['id']}`)" for child in taxonomy["children"])
        lines.append("")
    (path.parent / "ontology.md").write_text("\n".join(lines), encoding="utf-8")
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
