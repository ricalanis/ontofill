"""Infer factors, taxonomy, case schema, and declarative completion queries."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, ValidationError

from ontofill.case.checkpoints import (
    checkpoint_revisions,
    load_json,
    load_verified_approval,
    write_json,
    write_markdown,
)
from ontofill.contracts import load_schema, model_output_schema, validate_document
from ontofill.inference import DecisionClient, ModelValidationExhausted, generated_by
from ontofill.inference.page_content import screened_page_content

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
                            "required": ["id", "label", "level"],
                            "properties": {
                                "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                                "label": {"type": "string", "minLength": 1},
                                "level": {"const": 1},
                            },
                        },
                    },
                },
            },
        }
    },
}

# The generator proposes nodes; a separate critic (another model family) grades them.
NODE_LABELS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["labels"],
    "properties": {
        "labels": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["factor_id", "node_id", "critic_label"],
                "properties": {
                    "factor_id": {"type": "string", "minLength": 1},
                    "node_id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
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
        }
    },
}

_GOOD_LABELS = {"Good-Overlapping", "Good-Exclusive"}
_TAXONOMY_ALGORITHM_VERSION = "r10-separate-critic-v1"
_VALIDATION_ATTEMPTS = 3
_VALIDATION_ERROR_LIMIT = 600


class OntologyDraftUnavailable(RuntimeError):
    """A P2 model step exhausted its bounded schema or semantic repair attempts."""

    def __init__(self, purpose: str, attempts: int, reason: str) -> None:
        self.purpose = purpose
        self.attempts = attempts
        self.reason = reason
        noun = "attempt" if attempts == 1 else "attempts"
        super().__init__(f"{purpose} remained invalid after {attempts} {noun}: {reason}")


ValidationErrorCallback = Callable[[str, int, str], None]


def _bounded_validation_error(error: Exception) -> str:
    reason = str(error)
    if not reason:
        reason = error.__class__.__name__
    return reason[:_VALIDATION_ERROR_LIMIT]


def _complete_validated_json(
    decision: DecisionClient,
    purpose: str,
    prompt: str,
    schema: dict,
    validate: Callable[[dict], None],
    on_validation_error: ValidationErrorCallback | None = None,
) -> dict:
    """Run a P2 model step with bounded JSON Schema and semantic repair feedback."""
    current_prompt = prompt
    for attempt in range(1, _VALIDATION_ATTEMPTS + 1):
        try:
            response = decision.complete_json(purpose, current_prompt, schema)
        except ModelValidationExhausted as exc:
            reason = exc.reason[:_VALIDATION_ERROR_LIMIT]
            if on_validation_error is not None:
                on_validation_error(exc.purpose, exc.attempts, reason)
            raise OntologyDraftUnavailable(exc.purpose, exc.attempts, reason) from exc
        except (ValidationError, ValueError) as exc:
            validation_error: ValidationError | ValueError | None = exc
        else:
            try:
                Draft202012Validator(schema).validate(response)
                validate(response)
            except ModelValidationExhausted as exc:
                reason = exc.reason[:_VALIDATION_ERROR_LIMIT]
                if on_validation_error is not None:
                    on_validation_error(exc.purpose, exc.attempts, reason)
                raise OntologyDraftUnavailable(exc.purpose, exc.attempts, reason) from exc
            except (ValidationError, ValueError) as exc:
                validation_error = exc
            else:
                return response

        reason = _bounded_validation_error(validation_error)
        if on_validation_error is not None:
            on_validation_error(purpose, attempt, reason)
        if attempt == _VALIDATION_ATTEMPTS:
            raise OntologyDraftUnavailable(purpose, attempt, reason) from validation_error
        current_prompt += (
            f"\nThe previous response failed validation on attempt {attempt}. "
            "Return a complete corrected object. Validator error: "
            f"{screened_page_content(reason)}"
        )
    raise AssertionError("bounded validation attempts did not return or raise")


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def label_taxonomy_nodes(
    decision: DecisionClient,
    taxonomies: list[dict],
    *,
    on_validation_error: ValidationErrorCallback | None = None,
) -> list[dict]:
    """Grade every proposed node with a separate critic from another model family.

    The generator proposes the tree; this critic supplies each node's `critic_label`. Returns the
    taxonomies with `critic_label` set, plus `soundness` (share of Good-* nodes) and a null `coverage`
    (coverage is unknown until entities are classified in refine).
    """
    nodes = [
        {"factor_id": tax["factor_id"], "node_id": child["id"], "label": child["label"]}
        for tax in taxonomies
        for child in tax["children"]
    ]
    prompt = (
        "You are an independent taxonomy critic. Grade every proposed node with exactly one label: "
        "Good-Exclusive (distinct and non-overlapping), Good-Overlapping (useful but overlaps a sibling), "
        "Redundant (says nothing new), or Bad (wrong or unusable). Use only the node's factor, id and label; "
        "do not add, drop or rename nodes. Return one label per proposed node. "
        f"Proposed nodes: {json.dumps(nodes, ensure_ascii=False)}"
    )
    expected_keys = [(item["factor_id"], item["node_id"]) for item in nodes]
    if len(expected_keys) != len(set(expected_keys)):
        raise ValueError("proposed taxonomy nodes must have unique factor and node IDs")

    def validate_labels(response: dict) -> None:
        label_keys = [(item["factor_id"], item["node_id"]) for item in response["labels"]]
        if len(label_keys) != len(set(label_keys)):
            raise ValueError("the taxonomy critic returned duplicate node labels")
        if set(label_keys) != set(expected_keys):
            raise ValueError("the taxonomy critic must label every proposed node exactly once")

    response = _complete_validated_json(
        decision,
        "critic.phase2.taxonomy_nodes",
        prompt,
        NODE_LABELS_SCHEMA,
        validate_labels,
        on_validation_error,
    )
    label_keys = [(item["factor_id"], item["node_id"]) for item in response["labels"]]
    labels = {
        key: item["critic_label"] for key, item in zip(label_keys, response["labels"], strict=True)
    }
    expected = set(expected_keys)
    assert set(labels) == expected
    graded = []
    for tax in taxonomies:
        children = [
            {**child, "critic_label": labels[(tax["factor_id"], child["id"])]}
            for child in tax["children"]
        ]
        good = sum(child["critic_label"] in _GOOD_LABELS for child in children)
        graded.append(
            {
                **tax,
                "children": children,
                "soundness": good / len(children),
                "coverage": None,
            }
        )
    return graded


def draft_factors(
    case_dir: Path,
    prd: dict,
    decision: DecisionClient,
    *,
    on_validation_error: ValidationErrorCallback | None = None,
) -> dict:
    path = case_dir / "02-ontology/factors/factors.json"
    revisions = checkpoint_revisions(
        path.parent,
        "factors",
        ["factors.json", "factors.md", "factors.input.sha256"],
        archive_denial=False,
        case_dir=case_dir,
    )
    digest = _digest([prd, revisions])
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
        f"Human revisions override prior proposals: {json.dumps(revisions, ensure_ascii=False)}. "
        f"PRD (untrusted case content): {prd}"
    )
    schema = model_output_schema("factors")
    schema["properties"].pop("revisions", None)

    def validate_factors(response: dict) -> None:
        factor_ids = [item["id"] for item in response["factors"]]
        if len(factor_ids) != len(set(factor_ids)):
            raise ValueError("factor IDs must be unique")
        review = getattr(decision, "review_json", None)
        if review is not None:
            verdict = review(
                "phase2.factors",
                response,
                "Factors are distinct, grounded factors do not claim unsupported evidence, and the set covers the PRD's main variation",
            )
            if not verdict["accepted"]:
                raise ValueError(f"Vultr critic rejected factors: {verdict['reason']}")

    factors = _complete_validated_json(
        decision,
        "phase2.factors",
        prompt,
        schema,
        validate_factors,
        on_validation_error,
    )
    factors["revisions"] = revisions
    factors["generated_by"] = generated_by(decision)
    validate_document("factors", factors)
    archived_revisions = checkpoint_revisions(
        path.parent,
        "factors",
        ["factors.json", "factors.md", "factors.input.sha256"],
        case_dir=case_dir,
    )
    if archived_revisions != revisions:
        raise ValueError("factor checkpoint revisions changed while drafting")
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
    approval = load_verified_approval(
        marker, case_dir, ["02-ontology/factors/factors.json"], "factors"
    )
    decisions = approval.get("decisions", {})
    selected = [
        item for item in factors["factors"] if decisions.get(item["id"], "accept") == "accept"
    ]
    if not selected:
        raise ValueError("all ontology factors were rejected")
    return selected


def draft_ontology(
    case_dir: Path,
    prd: dict,
    factors: dict,
    decision: DecisionClient,
    *,
    on_validation_error: ValidationErrorCallback | None = None,
) -> dict:
    path = case_dir / "02-ontology/ontology.json"
    queries_path = case_dir / "02-ontology/dod-queries.json"
    chosen = accepted_factors(case_dir, factors)
    revisions = checkpoint_revisions(
        path.parent,
        "ontology",
        ["ontology.json", "ontology.md", "ontology.input.sha256", "dod-queries.json", "shapes.ttl"],
        archive_denial=False,
        case_dir=case_dir,
    )
    digest = _digest([prd, chosen, revisions, _TAXONOMY_ALGORITHM_VERSION])
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
                _validate_queries(prd, ontology, load_json(queries_path))
            except (ValidationError, ValueError):
                pass
            else:
                return ontology
    prompt = (
        "Expand each approved factor to exactly one taxonomy level. Use stable snake_case IDs. "
        "Give each child level=1. Return one taxonomy per factor and do not assert observed data. "
        f"Human revisions override prior proposals: {json.dumps(revisions, ensure_ascii=False)}. "
        f"Approved factors: {chosen}. PRD: {prd}"
    )
    factor_ids = {factor["id"] for factor in chosen}

    def validate_taxonomies(response: dict) -> None:
        received_ids = [tax["factor_id"] for tax in response["taxonomies"]]
        if len(received_ids) != len(set(received_ids)) or set(received_ids) != factor_ids:
            raise ValueError("taxonomies must cover exactly the approved factors")
        for taxonomy in response["taxonomies"]:
            node_ids = [child["id"] for child in taxonomy["children"]]
            if len(node_ids) != len(set(node_ids)):
                raise ValueError("taxonomy child IDs must be unique within each factor")
        review = getattr(decision, "review_json", None)
        if review is not None:
            verdict = review(
                "phase2.taxonomies",
                response,
                "Every approved factor has one taxonomy; children are mutually coherent and grounded in approved factors",
            )
            if not verdict["accepted"]:
                raise ValueError(f"Vultr critic rejected taxonomies: {verdict['reason']}")

    response = _complete_validated_json(
        decision,
        "phase2.taxonomies",
        prompt,
        TAXONOMY_SCHEMA,
        validate_taxonomies,
        on_validation_error,
    )
    # The generator never grades itself: a separate critic labels each proposed node.
    taxonomies = label_taxonomy_nodes(
        decision,
        response["taxonomies"],
        on_validation_error=on_validation_error,
    )
    proposal = _schema_proposal()
    case_spec = {
        "question": (case_dir / "brief.md").read_text(encoding="utf-8")[:3000],
        "jobs": [item["description"] for item in prd["jobs_to_be_done"]],
        "requirements": prd["requirements"],
        "definition_of_done": prd["definition_of_done"],
        "factors": chosen,
        "taxonomies": taxonomies,
        "human_revisions": revisions,
    }
    schema_prompt = (
        "Produce the smallest useful data schema for this case. Use stable snake_case IDs. "
        "Choose a primary class, title and identifier properties for every class, typed properties, "
        "relations, rules, source classes and public alignment URIs. Mark DoD properties. "
        "Every relation must have a typed match using same_value over one property from each "
        "endpoint class. Every executable rule must have a nonempty predicate.all list using only "
        "same_value, equals, and date_before over typed property IDs; checks and verify remain "
        "human-readable descriptions and are never executable. Omit a relation or rule when its "
        "match or predicate cannot be stated with typed properties. "
        "Allowed datatypes: string, integer, number, boolean, date, datetime, uri. "
        "Each class title_property and identifier_property must name a property whose domain is that class. "
        "All relation domain/range and property domains must name declared classes. "
        "Do not invent observations. Keep the response concise. "
        f"Approved case specification: {json.dumps(case_spec, ensure_ascii=False)}"
    )

    def validate_schema(proposed: dict) -> None:
        ontology = {
            "version": "1",
            "prd_path": "01-scope/prd.json",
            "factors": chosen,
            "taxonomies": taxonomies,
            **proposed,
            "shacl_path": "02-ontology/shapes.ttl",
            "dod_queries_path": "02-ontology/dod-queries.json",
            "generated_by": generated_by(decision),
            "revisions": revisions,
        }
        validate_document("ontology", ontology)
        _validate_ontology(ontology)

    proposed = _complete_validated_json(
        decision,
        "phase2.schema",
        schema_prompt,
        proposal,
        validate_schema,
        on_validation_error,
    )
    ontology = {
        "version": "1",
        "prd_path": "01-scope/prd.json",
        "factors": chosen,
        "taxonomies": taxonomies,
        **proposed,
        "shacl_path": "02-ontology/shapes.ttl",
        "dod_queries_path": "02-ontology/dod-queries.json",
        "generated_by": generated_by(decision),
        "revisions": revisions,
    }
    queries = _draft_dod_queries(
        prd,
        ontology,
        decision,
        on_validation_error=on_validation_error,
    )
    archived_revisions = checkpoint_revisions(
        path.parent,
        "ontology",
        ["ontology.json", "ontology.md", "ontology.input.sha256", "dod-queries.json", "shapes.ttl"],
        case_dir=case_dir,
    )
    if archived_revisions != revisions:
        raise ValueError("ontology checkpoint revisions changed while drafting")
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
    limits = {"classes": 6, "properties": 24, "relations": 12, "rules": 12, "source_classes": 8}
    properties = {
        name: {
            **schema["properties"][name],
            **({"maxItems": limits[name]} if name in limits else {}),
            **({"minItems": 1} if name == "source_classes" else {}),
        }
        for name in names
    }
    properties["relations"]["items"] = {
        "allOf": [{"$ref": "#/$defs/relation"}, {"required": ["match"]}]
    }
    properties["rules"]["items"] = {
        "allOf": [{"$ref": "#/$defs/rule"}, {"required": ["predicate"]}]
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(names),
        "properties": properties,
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
        match = item.get("match")
        if match is not None and item["symmetric"] and item["domain"] != item["range"]:
            raise ValueError("symmetric relation domain and range must be the same class")
        if match is not None:
            domain_property = properties.get(match["domain_property"])
            range_property = properties.get(match["range_property"])
            if (
                domain_property is None
                or range_property is None
                or domain_property["domain"] != item["domain"]
                or range_property["domain"] != item["range"]
            ):
                raise ValueError("relation match properties must belong to their endpoint classes")
            if _semantic_datatype(domain_property["datatype"]) != _semantic_datatype(
                range_property["datatype"]
            ):
                raise ValueError("relation match properties must have the same datatype")
            if item["symmetric"] and match["domain_property"] != match["range_property"]:
                raise ValueError(
                    "symmetric relation must match the same property on both endpoints"
                )
    for item in ontology["rules"]:
        predicate = item.get("predicate")
        if predicate is None:
            continue
        operand_domains = set()
        for term in predicate["all"]:
            operation = term["op"]
            if operation == "same_value":
                left = properties.get(term["left_property"])
                right = properties.get(term["right_property"])
                operands = [left, right]
                if any(prop is None for prop in operands):
                    raise ValueError("rule predicate references an unknown property")
                if _semantic_datatype(left["datatype"]) != _semantic_datatype(right["datatype"]):
                    raise ValueError("same_value predicate properties must have the same datatype")
            elif operation == "equals":
                prop = properties.get(term["property"])
                if prop is None:
                    raise ValueError("rule predicate references an unknown property")
                _validate_typed_literal(term["value"], prop["datatype"])
                operands = [prop]
            elif operation == "date_before":
                earlier = properties.get(term["earlier_property"])
                later = properties.get(term["later_property"])
                operands = [earlier, later]
                if any(prop is None for prop in operands):
                    raise ValueError("rule predicate references an unknown property")
                earlier_type = _semantic_datatype(earlier["datatype"])
                later_type = _semantic_datatype(later["datatype"])
                if earlier_type not in {"date", "datetime"} or earlier_type != later_type:
                    raise ValueError("date_before properties must share date or datetime datatype")
            else:
                raise ValueError("unsupported rule predicate operator")
            operand_domains.update(prop["domain"] for prop in operands)
        if len(operand_domains) != 1:
            raise ValueError("all rule predicate properties must belong to one class")


def _semantic_datatype(datatype: str) -> str:
    name = datatype.rsplit("#", 1)[-1].rsplit("/", 1)[-1].rsplit(":", 1)[-1].lower()
    aliases = {
        "text": "string",
        "normalizedstring": "string",
        "token": "string",
        "int": "integer",
        "long": "integer",
        "nonnegativeinteger": "integer",
        "decimal": "number",
        "float": "number",
        "double": "number",
        "bool": "boolean",
        "date-time": "datetime",
        "url": "uri",
        "anyuri": "uri",
    }
    return aliases.get(name, name)


def _validate_typed_literal(value: object, datatype: str) -> None:
    kind = _semantic_datatype(datatype)
    valid = False
    if kind == "string":
        valid = isinstance(value, str)
    elif kind == "boolean":
        valid = isinstance(value, bool)
    elif kind == "integer":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "number":
        valid = (
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        )
    elif kind == "date" and isinstance(value, str):
        try:
            valid = date.fromisoformat(value).isoformat() == value
        except ValueError:
            valid = False
    elif kind == "datetime" and isinstance(value, str):
        try:
            valid = datetime.fromisoformat(value).tzinfo is not None
        except ValueError:
            valid = False
    elif kind == "uri" and isinstance(value, str):
        valid = bool(urlparse(value).scheme)
    if not valid:
        raise ValueError(f"equals predicate literal does not match datatype {datatype}")


def _draft_dod_queries(
    prd: dict,
    ontology: dict,
    decision: DecisionClient,
    *,
    on_validation_error: ValidationErrorCallback | None = None,
) -> dict:
    schema = model_output_schema("dod-queries")
    prompt = (
        "Compile every approved definition-of-done criterion into exactly one safe declarative "
        "query. Use only ontology class/property/relation IDs and the supported aggregate/condition operators. "
        "For a per-entity completeness criterion, use entities_meeting_completeness with "
        "class equal to the primary class, properties='dod', and min_ratio copied from the PRD. "
        "For a criterion that counts linked records, use count_entities_with_relation and its "
        "relation_id; class_id, if included, must be the relation domain. "
        "Copy each criterion's target and comparison operator exactly. Do not write SQL or code. "
        f"Criteria: {prd['definition_of_done']}. Classes: {ontology['classes']}. "
        f"Properties: {ontology['properties']}. Relations: {ontology['relations']}. "
        f"Source classes: {ontology.get('source_classes', [])}."
    )

    def validate_queries(response: dict) -> None:
        document = {
            **response,
            "generated_by": generated_by(decision),
            "prd_path": "01-scope/prd.json",
            "ontology_version": ontology["version"],
        }
        validate_document("dod-queries", document)
        _validate_queries(prd, ontology, document)

    response = _complete_validated_json(
        decision,
        "phase2.dod_queries",
        prompt,
        schema,
        validate_queries,
        on_validation_error,
    )
    return {
        **response,
        "generated_by": generated_by(decision),
        "prd_path": "01-scope/prd.json",
        "ontology_version": ontology["version"],
    }


def _validate_queries(prd: dict, ontology: dict, document: dict) -> None:
    criteria = {item["id"]: item for item in prd["definition_of_done"]}
    queries = document["queries"]
    if len(queries) != len(criteria) or {item["criterion_id"] for item in queries} != set(criteria):
        raise ValueError("DoD queries must cover each approved criterion exactly once")
    classes = {item["id"] for item in ontology["classes"]}
    properties = {item["id"]: item for item in ontology["properties"]}
    relations = {item["id"]: item for item in ontology.get("relations", [])}
    for query in queries:
        criterion = criteria[query["criterion_id"]]
        if query["target"] != criterion["target"] or query["operator"] != criterion["operator"]:
            raise ValueError("DoD query target/operator differs from approved PRD")
        if (
            criterion.get("min_ratio") is not None
            and query.get("min_ratio") != criterion["min_ratio"]
        ):
            raise ValueError("DoD query min_ratio differs from approved PRD")
        if (
            criterion.get("min_ratio") is not None
            and query["aggregate"] != "entities_meeting_completeness"
        ):
            raise ValueError("per-entity completeness requires its declarative aggregate")
        if query["aggregate"] == "entities_meeting_completeness":
            if query["class"] != ontology["primary_class"]:
                raise ValueError("completeness query must use the primary class")
            if criterion.get("min_ratio") is None:
                raise ValueError("completeness query requires an approved PRD min_ratio")
        class_id = query.get("class_id", query.get("class"))
        if query["aggregate"] == "count_entities_with_relation":
            relation = relations.get(query["relation_id"])
            if relation is None:
                raise ValueError("DoD query references an unknown relation")
            if class_id is not None and class_id != relation["domain"]:
                raise ValueError("relation count class must equal the relation domain")
            class_id = relation["domain"]
        if class_id is not None and class_id not in classes:
            raise ValueError("DoD query references an unknown class")
        listed = query.get("properties", [])
        listed = [] if listed == "dod" else listed
        for property_id in [
            *listed,
            *(c["property"] for c in query.get("conditions", [])),
        ]:
            if property_id not in properties:
                raise ValueError("DoD query references an unknown property")
            if class_id is not None and properties[property_id]["domain"] != class_id:
                raise ValueError("DoD query property belongs to another class")


def _compile_shapes(ontology: dict) -> str:
    receipt = json.dumps(ontology["generated_by"], ensure_ascii=False, sort_keys=True)
    lines = [
        f"# generated_by: {receipt}",
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
