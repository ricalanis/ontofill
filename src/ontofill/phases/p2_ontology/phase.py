"""Infer factors, taxonomy, case schema, and declarative completion queries."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from ontofill.case.checkpoints import load_json, write_json, write_markdown
from ontofill.contracts import load_schema, model_output_schema, validate_document
from ontofill.inference import DecisionClient, generated_by

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_DATATYPES = {
    "string": "xsd:string",
    "number": "xsd:decimal",
    "integer": "xsd:integer",
    "boolean": "xsd:boolean",
    "date": "xsd:date",
    "datetime": "xsd:dateTime",
    "uri": "xsd:anyURI",
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


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def draft_factors(case_dir: Path, prd: dict, decision: DecisionClient) -> dict:
    path = case_dir / "02-ontology/factors/factors.json"
    digest = _digest(prd)
    fingerprint = path.with_suffix(".input.sha256")
    if path.exists():
        factors = load_json(path)
        if (
            factors.get("generated_by", {}).get("backend") == decision.backend
            and fingerprint.exists()
            and fingerprint.read_text(encoding="utf-8").strip() == digest
        ):
            try:
                validate_document("factors", factors)
            except ValidationError:
                pass
            else:
                return factors
    prompt = (
        "Propose the prime factors of variation for the subject of this case. "
        "Keep factors broad and distinct; label each grounded or conceptual. "
        "Include evidence only if it is actually in the PRD; an empty list is allowed. "
        "Do not invent observed entities or evidence URLs. "
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
            "Factors are distinct, grounded factors do not claim unsupported evidence, and the set covers the PRD's main variation",
        )
        if verdict["accepted"]:
            break
        if attempt:
            raise ValueError("Vultr critic rejected factors after repair")
        prompt += f"\nRepair this material issue: {verdict['reason']}"
    factors["generated_by"] = generated_by(decision)
    validate_document("factors", factors)
    marker = path.parent / "APPROVED"
    if marker.exists():
        marker.rename(marker.with_name(f"APPROVED.stale.{digest[:12]}"))
    write_json(path, factors)
    fingerprint.write_text(digest + "\n", encoding="utf-8")
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
    queries_path = case_dir / "02-ontology/dod-queries.json"
    chosen = accepted_factors(case_dir, factors)
    digest = _digest([prd, chosen])
    fingerprint = path.with_suffix(".input.sha256")
    if path.exists():
        ontology = load_json(path)
        if (
            ontology.get("generated_by", {}).get("backend") == decision.backend
            and fingerprint.exists()
            and fingerprint.read_text(encoding="utf-8").strip() == digest
            and queries_path.exists()
        ):
            try:
                validate_document("ontology", ontology)
                validate_document("dod-queries", load_json(queries_path))
                _validate_ontology(ontology)
            except (ValidationError, ValueError):
                pass
            else:
                return ontology
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
    proposal = _schema_proposal()
    schema_prompt = (
        "Design the case-specific data schema from the approved PRD, factors and taxonomies. "
        "Choose a primary class, classes with title and identifier properties, typed properties, "
        "relations, rule descriptions, source classes, and public alignment URIs. "
        "Use only these property datatypes: string, integer, number, boolean, date, datetime, uri. "
        "Every value must come from the case's question, never from an assumed domain. "
        "Use stable snake_case IDs, and mark the properties needed for the definition of done. "
        f"PRD: {prd}. Approved factors: {chosen}. Taxonomies: {taxonomies}"
    )
    proposed = decision.complete_json("phase2.schema", schema_prompt, proposal)
    Draft202012Validator(proposal).validate(proposed)
    ontology = {
        "version": "1",
        "prd_path": "01-scope/prd.json",
        "factors": chosen,
        "taxonomies": taxonomies,
        **proposed,
        "shacl_path": "02-ontology/shapes.ttl",
        "dod_queries_path": "02-ontology/dod-queries.json",
        "generated_by": generated_by(decision),
    }
    validate_document("ontology", ontology)
    _validate_ontology(ontology)
    queries = _draft_dod_queries(prd, ontology, decision)
    validate_document("dod-queries", queries)
    _validate_queries(prd, ontology, queries)
    marker = path.parent / "APPROVED"
    if marker.exists():
        marker.rename(marker.with_name(f"APPROVED.stale.{digest[:12]}"))
    write_json(path, ontology)
    write_json(queries_path, queries)
    fingerprint.write_text(digest + "\n", encoding="utf-8")
    (case_dir / ontology["shacl_path"]).write_text(_compile_shapes(ontology), encoding="utf-8")
    lines = [
        "# Ontology v1",
        "",
        f"Primary class: `{ontology['primary_class']}`",
        "",
        "Properties: " + ", ".join(item["id"] for item in ontology["properties"]),
        "",
    ]
    for taxonomy in taxonomies:
        lines.extend([f"## {taxonomy['root_label']}", ""])
        lines.extend(f"- {child['label']} (`{child['id']}`)" for child in taxonomy["children"])
        lines.append("")
    write_markdown(path.parent / "ontology.md", "\n".join(lines), ontology["generated_by"])
    return ontology


def _schema_proposal() -> dict:
    schema = load_schema("ontology")
    names = ("primary_class", "classes", "properties", "relations", "rules", "source_classes")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(names),
        "properties": {
            name: (
                {**schema["properties"][name], "minItems": 1}
                if name == "source_classes"
                else schema["properties"][name]
            )
            for name in names
        },
        "$defs": schema["$defs"],
    }


def _validate_ontology(ontology: dict) -> None:
    classes = {item["id"]: item for item in ontology["classes"]}
    properties = {item["id"]: item for item in ontology["properties"]}
    if len(classes) != len(ontology["classes"]) or len(properties) != len(ontology["properties"]):
        raise ValueError("ontology class and property IDs must be unique")
    if any(not _ID.fullmatch(value) for value in (*classes, *properties)):
        raise ValueError("ontology IDs must be safe identifiers")
    if ontology["primary_class"] not in classes:
        raise ValueError("primary_class must refer to an ontology class")
    for item in ontology["classes"]:
        for key in ("title_property", "identifier_property"):
            property_id = item[key]
            if property_id not in properties or properties[property_id]["domain"] != item["id"]:
                raise ValueError(f"{key} must refer to a property of its class")
    for item in ontology["properties"]:
        if item["domain"] not in classes:
            raise ValueError("property domain must refer to an ontology class")
        if item["datatype"] not in _DATATYPES and item["datatype"] not in _DATATYPES.values():
            raise ValueError("unsupported ontology property datatype")
    relation_ids = [item["id"] for item in ontology["relations"]]
    rule_ids = [item["id"] for item in ontology["rules"]]
    source_ids = [item["id"] for item in ontology.get("source_classes", [])]
    for values in (relation_ids, rule_ids, source_ids):
        if len(values) != len(set(values)) or any(not _ID.fullmatch(value) for value in values):
            raise ValueError("ontology relation, rule and source-class IDs must be unique and safe")
    for item in ontology["relations"]:
        if item["domain"] not in classes or item["range"] not in classes:
            raise ValueError("relation domain and range must refer to ontology classes")


def _draft_dod_queries(prd: dict, ontology: dict, decision: DecisionClient) -> dict:
    schema = model_output_schema("dod-queries")
    prompt = (
        "Compile every approved definition-of-done criterion into exactly one safe declarative "
        "query. Use only ontology class/property IDs and the supported aggregate/condition operators. "
        "Copy each criterion's target and comparison operator exactly. Do not write SQL or code. "
        f"Criteria: {prd['definition_of_done']}. Classes: {ontology['classes']}. "
        f"Properties: {ontology['properties']}. Source classes: {ontology.get('source_classes', [])}."
    )
    result = decision.complete_json("phase2.dod_queries", prompt, schema)
    result["generated_by"] = generated_by(decision)
    result["prd_path"] = "01-scope/prd.json"
    result["ontology_version"] = ontology["version"]
    return result


def _validate_queries(prd: dict, ontology: dict, document: dict) -> None:
    criteria = {item["id"]: item for item in prd["definition_of_done"]}
    queries = document["queries"]
    if len(queries) != len(criteria) or {item["criterion_id"] for item in queries} != set(criteria):
        raise ValueError("DoD queries must cover each approved criterion exactly once")
    classes = {item["id"] for item in ontology["classes"]}
    properties = {item["id"]: item for item in ontology["properties"]}
    for query in queries:
        criterion = criteria[query["criterion_id"]]
        if query["target"] != criterion["target"] or query["operator"] != criterion["operator"]:
            raise ValueError("DoD query target/operator differs from approved PRD")
        class_id = query.get("class_id")
        if class_id is not None and class_id not in classes:
            raise ValueError("DoD query references an unknown class")
        for property_id in [
            *query.get("properties", []),
            *(c["property"] for c in query.get("conditions", [])),
        ]:
            if property_id not in properties:
                raise ValueError("DoD query references an unknown property")
            if class_id is not None and properties[property_id]["domain"] != class_id:
                raise ValueError("DoD query property belongs to another class")


def _compile_shapes(ontology: dict) -> str:
    lines = [
        "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "@prefix onto: <https://ontofill.dev/ontology/> .",
        "",
    ]
    for entity_class in ontology["classes"]:
        properties = [
            item for item in ontology["properties"] if item["domain"] == entity_class["id"]
        ]
        lines.extend(
            [
                f"onto:{entity_class['id']}Shape a sh:NodeShape ;",
                f"    sh:targetClass onto:{entity_class['id']}",
            ]
        )
        for item in properties:
            datatype = _DATATYPES.get(item["datatype"], item["datatype"])
            required = " ; sh:minCount 1" if item["dod"] else ""
            lines.append(
                f"    ; sh:property [ sh:path onto:{item['id']} ; sh:datatype {datatype}{required} ]"
            )
        lines.extend(["    .", ""])
    return "\n".join(lines) + "\n"
