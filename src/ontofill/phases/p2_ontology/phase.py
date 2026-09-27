"""Infer factors, taxonomy, case schema, and declarative completion queries."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from copy import deepcopy
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
_CORE_FIELD_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["requirements"],
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["requirement_id", "core_fields", "non_core_reason"],
                "properties": {
                    "requirement_id": {"type": "string", "minLength": 1},
                    "core_fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["field", "property_id"],
                            "properties": {
                                "field": {"type": "string", "minLength": 1},
                                "property_id": {
                                    "oneOf": [
                                        {"type": "string", "minLength": 1},
                                        {"type": "null"},
                                    ]
                                },
                            },
                        },
                    },
                    "non_core_reason": {
                        "oneOf": [
                            {"type": "string", "minLength": 1},
                            {"type": "null"},
                        ]
                    },
                },
            },
        }
    },
}
_RELATION_COUNT_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["assessments"],
    "properties": {
        "assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "criterion_id",
                    "counts_related_entities",
                    "class_id",
                    "relation_id",
                    "reason",
                ],
                "properties": {
                    "criterion_id": {"type": "string", "minLength": 1},
                    "counts_related_entities": {"type": "boolean"},
                    "class_id": {
                        "oneOf": [
                            {"type": "string", "minLength": 1},
                            {"type": "null"},
                        ]
                    },
                    "relation_id": {
                        "oneOf": [
                            {"type": "string", "minLength": 1},
                            {"type": "null"},
                        ]
                    },
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        }
    },
}
_RULE_SEMANTICS_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["assessments"],
    "properties": {
        "assessments": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rule_id", "matches", "reason"],
                "properties": {
                    "rule_id": {"type": "string", "minLength": 1},
                    "matches": {"type": "boolean"},
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        }
    },
}
_ALLOWED_RELATION_MATCH = (
    "Relation endpoint classes must be honored. Allowed relation matches: use `same_value` "
    "with one declared property from the relation's domain class and one from its range class; "
    "both properties must have the same datatype. Use a separate typed relation to connect "
    "classes; relation-path predicates are unsupported."
)
_ALLOWED_RULE_PREDICATES = (
    "Allowed rule predicates: use nonempty `predicate.all` with `same_value` "
    "(`left_property`, `right_property`), `equals` (`property`, `value`), or `date_before` "
    "(`earlier_property`, `later_property`) terms. Every referenced property across one "
    "rule must belong to the same declared class. Split cross-class checks into separate "
    "class-local rules and use a declared typed relation to connect those classes; "
    "relation-path rule predicates are unsupported."
)


class OntologyProposalErrors(ValueError):
    """Recoverable proposal or primary-class core-property defects."""

    def __init__(
        self,
        rule_errors: dict[str, str],
        relation_errors: dict[str, str],
        *,
        core_fields: list[dict] | None = None,
        core_errors: list[str] | None = None,
        core_review: dict | None = None,
        drop_all_rules_reason: str | None = None,
    ) -> None:
        self.rule_errors = rule_errors
        self.relation_errors = relation_errors
        self.core_fields = core_fields or []
        self.core_errors = core_errors or []
        self.core_review = core_review
        self.drop_all_rules_reason = drop_all_rules_reason
        messages = []
        if relation_errors:
            messages.extend(
                reason.split(". Relation endpoint classes must be honored.", 1)[0][:280]
                for reason in relation_errors.values()
            )
            messages.append(_ALLOWED_RELATION_MATCH)
        if rule_errors:
            messages.extend(
                reason.split(". Allowed rule predicates:", 1)[0][:280]
                for reason in rule_errors.values()
            )
            messages.append(_ALLOWED_RULE_PREDICATES)
        messages.extend(core_errors or [])
        if drop_all_rules_reason:
            messages.append(drop_all_rules_reason)
        super().__init__("; ".join(messages))


_VALIDATION_ATTEMPTS = 3
_VALIDATION_ERROR_LIMIT = 10000


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
    on_exhaustion: Callable[[dict, Exception], dict | None] | None = None,
) -> dict:
    """Run a P2 model step with bounded JSON Schema and semantic repair feedback."""
    current_prompt = prompt
    last_structurally_valid_response = None
    last_structural_validation_error = None

    def recover(candidate: dict, error: Exception) -> dict | None:
        if on_exhaustion is None:
            return None
        recovered = on_exhaustion(candidate, error)
        if recovered is None:
            return None
        try:
            Draft202012Validator(schema).validate(recovered)
            validate(recovered)
        except (ValidationError, ValueError) as exc:
            reason = _bounded_validation_error(exc)
            raise OntologyDraftUnavailable(purpose, _VALIDATION_ATTEMPTS, reason) from exc
        return recovered

    for attempt in range(1, _VALIDATION_ATTEMPTS + 1):
        structurally_valid_response = None
        try:
            response = decision.complete_json(purpose, current_prompt, schema)
        except ModelValidationExhausted as exc:
            reason = exc.reason[:_VALIDATION_ERROR_LIMIT]
            if on_validation_error is not None:
                on_validation_error(exc.purpose, exc.attempts, reason)
            if (
                last_structurally_valid_response is not None
                and last_structural_validation_error is not None
            ):
                recovered = recover(
                    last_structurally_valid_response, last_structural_validation_error
                )
                if recovered is not None:
                    return recovered
            raise OntologyDraftUnavailable(exc.purpose, exc.attempts, reason) from exc
        except (ValidationError, ValueError) as exc:
            validation_error: ValidationError | ValueError | None = exc
        else:
            try:
                Draft202012Validator(schema).validate(response)
                structurally_valid_response = response
                validate(response)
            except ModelValidationExhausted as exc:
                reason = exc.reason[:_VALIDATION_ERROR_LIMIT]
                if on_validation_error is not None:
                    on_validation_error(exc.purpose, exc.attempts, reason)
                validation_error = ValueError(
                    f"{exc.purpose} critic exhausted its output retries: {reason}"
                )
            except (ValidationError, ValueError) as exc:
                validation_error = exc
            else:
                return response

        if structurally_valid_response is not None:
            last_structurally_valid_response = structurally_valid_response
            last_structural_validation_error = validation_error
        reason = _bounded_validation_error(validation_error)
        if on_validation_error is not None:
            on_validation_error(purpose, attempt, reason)
        if attempt == _VALIDATION_ATTEMPTS:
            salvage_candidate = structurally_valid_response or last_structurally_valid_response
            salvage_error = (
                validation_error
                if structurally_valid_response is not None
                else last_structural_validation_error
            )
            if salvage_candidate is not None and salvage_error is not None:
                recovered = recover(salvage_candidate, salvage_error)
                if recovered is not None:
                    return recovered
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


def _approved_artifact_is_current(case_dir: Path, artifact_path: Path, checkpoint: str) -> bool:
    marker = artifact_path.parent / "APPROVED"
    if not marker.exists():
        return False
    relative_path = artifact_path.relative_to(case_dir).as_posix()
    approval = load_verified_approval(marker, case_dir, [relative_path], checkpoint)
    return approval.get("decision", "approve") != "deny"


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


def _published_factor_records(prd: dict) -> dict[str, str]:
    """Return citation IDs and source text already published in the PRD."""
    records: dict[str, str] = {}
    for section, prefix, text_field in (
        ("personas", "persona", "description"),
        ("jobs_to_be_done", "job", "description"),
        ("requirements", "requirement", "description"),
        ("definition_of_done", "definition_of_done", "basis_quote"),
    ):
        for item in prd.get(section, []):
            if not isinstance(item, dict):
                continue
            record_id = item.get("id")
            text = item.get(text_field)
            if isinstance(record_id, str) and record_id and isinstance(text, str) and text.strip():
                records[f"{prefix}:{record_id}"] = text
    return records


def _normalise_citation_text(value: str) -> str:
    return " ".join(value.split())


def _factor_evidence_is_supported(
    evidence: dict, records: dict[str, str], published_prd: str
) -> bool:
    source = evidence.get("source")
    if not isinstance(source, dict):
        return False
    if source.get("type") != "case_file" or source.get("path") != "01-scope/prd.json":
        return False
    record_id = source.get("record_id")
    quote = source.get("quote")
    source_text = records.get(record_id) if isinstance(record_id, str) else None
    if not isinstance(source_text, str) or not isinstance(quote, str) or not quote.strip():
        return False
    if _normalise_citation_text(quote) not in _normalise_citation_text(source_text):
        return False
    return all(
        key not in evidence or (isinstance(evidence[key], str) and evidence[key] in published_prd)
        for key in ("url", "bronze_key")
    )


def _grounded_factor_evidence_errors(
    factors: list[dict], records: dict[str, str], published_prd: str
) -> list[str]:
    allowed_ids = ", ".join(f"`{record_id}`" for record_id in records) or "none"
    errors = []
    for factor in factors:
        if factor["kind"] != "grounded":
            continue
        factor_id = factor["id"]
        evidence_items = factor["evidence"]
        if not evidence_items:
            errors.append(
                f"grounded factor `{factor_id}` has no evidence; cite an existing PRD record "
                f"using a `case_file` source at `01-scope/prd.json`, with `record_id` and an exact "
                f"quote in `quote` from {allowed_ids}, or set `kind` to `conceptual` and keep "
                "`evidence` empty"
            )
            continue
        for index, evidence in enumerate(evidence_items, start=1):
            source = evidence.get("source")
            if not isinstance(source, dict):
                errors.append(
                    f"grounded factor `{factor_id}` evidence item {index} lacks a typed "
                    "`case_file` source at `01-scope/prd.json`; add its `record_id` and exact "
                    "`quote`, or set `kind` to `conceptual` and clear unsupported evidence"
                )
                continue
            if source.get("type") != "case_file" or source.get("path") != "01-scope/prd.json":
                errors.append(
                    f"grounded factor `{factor_id}` evidence item {index} must point to "
                    "`01-scope/prd.json` with source type `case_file`"
                )
                continue
            record_id = source.get("record_id")
            quote = source.get("quote")
            if not isinstance(record_id, str) or record_id not in records:
                errors.append(
                    f"grounded factor `{factor_id}` evidence item {index} does not cite an "
                    f"existing PRD record; use `record_id` from {allowed_ids} with an exact "
                    "`quote`, or set `kind` to `conceptual` and clear unsupported evidence"
                )
                continue
            if (
                not isinstance(quote, str)
                or not quote.strip()
                or _normalise_citation_text(quote)
                not in _normalise_citation_text(records[record_id])
            ):
                errors.append(
                    f"grounded factor `{factor_id}` evidence item {index} quote is not an "
                    f"exact excerpt from PRD record `{record_id}`; copy its text exactly, or "
                    "set `kind` to `conceptual` and clear unsupported evidence"
                )
                continue
            if not _factor_evidence_is_supported(evidence, records, published_prd):
                errors.append(
                    f"grounded factor `{factor_id}` evidence item {index} includes a URL or "
                    "capture key that is not present in the PRD; remove it or cite only the "
                    "supported PRD quote"
                )
    return errors


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
    records = _published_factor_records(prd)
    published_prd = json.dumps(prd, ensure_ascii=False)
    if path.exists():
        factors = load_json(path)
        cached_digest = (
            fingerprint.read_text(encoding="utf-8").strip() if fingerprint.exists() else None
        )
        cache_matches = cached_digest == digest
        approved = decision.backend == "vultr" and _approved_artifact_is_current(
            case_dir, path, "factors"
        )
        if factors.get("generated_by", {}).get("backend") == decision.backend and (
            cache_matches or approved
        ):
            try:
                validate_document("factors", factors)
                evidence_errors = _grounded_factor_evidence_errors(
                    factors["factors"], records, published_prd
                )
                if evidence_errors:
                    raise ValueError("; ".join(evidence_errors))
            except (ValidationError, ValueError):
                pass
            else:
                if cached_digest != digest:
                    fingerprint.write_text(digest + "\n", encoding="utf-8")
                return factors
    prompt = (
        "Propose the prime factors of variation for the subject of this case. "
        "Keep factors broad and distinct; label each grounded or conceptual. "
        "A grounded factor needs at least one evidence item with a readable `description` and "
        "a typed `source` object: `type`=`case_file`, `path`=`01-scope/prd.json`, `record_id`, "
        "and an exact `quote`. Use only the published PRD record IDs and text listed here. "
        "Do not attach a URL or bronze key unless that exact value is already in the PRD. "
        "If no listed record supports a factor, label it conceptual and use an empty evidence "
        "list. Never invent citations, evidence URLs, or capture keys. "
        f"Published PRD records: {json.dumps(records, ensure_ascii=False)}. "
        f"Human revisions override prior proposals: {json.dumps(revisions, ensure_ascii=False)}. "
        f"PRD (untrusted case content): {prd}"
    )
    schema = model_output_schema("factors")
    schema["properties"].pop("revisions", None)

    def validate_factors(response: dict) -> None:
        factor_ids = [item["id"] for item in response["factors"]]
        if len(factor_ids) != len(set(factor_ids)):
            raise ValueError("factor IDs must be unique")
        evidence_errors = _grounded_factor_evidence_errors(
            response["factors"], records, published_prd
        )
        if evidence_errors:
            raise ValueError("; ".join(evidence_errors))
        review = getattr(decision, "review_json", None)
        if review is not None:
            verdict = review(
                "phase2.factors",
                response,
                "Factors are distinct, grounded factors do not claim unsupported evidence, and the set covers the PRD's main variation",
            )
            if not verdict["accepted"]:
                raise ValueError(f"Vultr critic rejected factors: {verdict['reason']}")

    def recover_unsupported_factors(response: dict, _error: Exception) -> dict | None:
        recovered = deepcopy(response)
        changed = False
        for factor in recovered["factors"]:
            if factor["kind"] != "grounded":
                continue
            evidence_items = factor["evidence"]
            supported = [
                item
                for item in evidence_items
                if _factor_evidence_is_supported(item, records, published_prd)
            ]
            if evidence_items and len(supported) == len(evidence_items):
                continue
            if supported:
                factor["evidence"] = supported
            else:
                factor["kind"] = "conceptual"
                factor["evidence"] = []
            changed = True
        return recovered if changed else None

    factors = _complete_validated_json(
        decision,
        "phase2.factors",
        prompt,
        schema,
        validate_factors,
        on_validation_error,
        on_exhaustion=recover_unsupported_factors,
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
        cached_digest = (
            fingerprint.read_text(encoding="utf-8").strip() if fingerprint.exists() else None
        )
        cache_matches = cached_digest == digest
        approved = decision.backend == "vultr" and _approved_artifact_is_current(
            case_dir, path, "ontology"
        )
        if (
            ontology.get("generated_by", {}).get("backend") == decision.backend
            and (cache_matches or approved)
            and queries_path.exists()
        ):
            try:
                validate_document("ontology", ontology)
                cached_queries = load_json(queries_path)
                validate_document("dod-queries", cached_queries)
                _validate_ontology(ontology)
                _validate_primary_dod_presence(prd, ontology)
                if approved:
                    _validate_queries(
                        prd,
                        ontology,
                        cached_queries,
                        allow_legacy_completeness=True,
                    )
                else:
                    relation_counts = _review_ontology_semantics(prd, ontology, decision)
                    _validate_queries(
                        prd,
                        ontology,
                        cached_queries,
                        relation_count_assessments=relation_counts,
                    )
            except (ValidationError, ValueError):
                if approved:
                    raise
            else:
                if cached_digest != digest:
                    fingerprint.write_text(digest + "\n", encoding="utf-8")
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

    schema_reviews: dict[str, dict] = {}

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
        _validate_primary_dod_presence(prd, ontology)
        relation_counts = _review_ontology_semantics(
            prd,
            ontology,
            decision,
            core_field_review=schema_reviews.pop("core_field_review_override", None),
        )
        schema_reviews["relation_counts"] = relation_counts

    unresolved: list[dict] = []
    repairs: list[dict] = []

    def salvage_invalid_proposals(candidate: dict, error: Exception) -> dict | None:
        if not isinstance(error, OntologyProposalErrors):
            return None
        if error.core_errors and not error.core_fields:
            return None
        recovered = deepcopy(candidate)
        set_aside = []
        for kind, field, errors in (
            ("relation", "relations", error.relation_errors),
            ("rule", "rules", error.rule_errors),
        ):
            retained = []
            for proposal in recovered[field]:
                reason = errors.get(proposal["id"])
                if kind == "rule" and error.drop_all_rules_reason:
                    reason = reason or error.drop_all_rules_reason
                if reason is None:
                    retained.append(proposal)
                else:
                    set_aside.append(
                        {
                            "kind": kind,
                            "id": proposal["id"],
                            "reason": reason,
                            "proposal": deepcopy(proposal),
                        }
                    )
            recovered[field] = retained
        repair_records = []
        repaired_field_keys = set()
        primary_class = recovered["primary_class"]
        for missing in error.core_fields:
            field_label = missing["field"]
            if not isinstance(field_label, str) or not field_label.strip():
                return None
            field_key = _normalise_words(field_label)
            if field_key in repaired_field_keys:
                continue
            repaired_field_keys.add(field_key)
            property_id = _stable_property_id(field_label, recovered["properties"])
            property_order = (
                max((item["order"] for item in recovered["properties"]), default=-1) + 1
            )
            new_property = {
                "id": property_id,
                "label": field_label,
                "domain": primary_class,
                "datatype": "string",
                "dod": True,
                "order": property_order,
                "description": (
                    f"Core value named by PRD requirement `{missing['requirement_id']}`: "
                    f"{missing['requirement_text']}"
                ),
                "aligned_to": None,
            }
            recovered["properties"].append(new_property)
            repair_records.append(
                {
                    "kind": "primary_dod_property",
                    "id": property_id,
                    "requirement_id": missing["requirement_id"],
                    "field": field_label,
                    "reason": (
                        "Added after bounded P2 validation because no exact matching primary-class "
                        "dod:true value property existed; requires human review."
                    ),
                    "property": deepcopy(new_property),
                }
            )
        if not set_aside and not repair_records:
            return None
        unresolved.extend(set_aside)
        repairs.extend(repair_records)
        if error.core_review is not None:
            schema_reviews["core_field_review_override"] = error.core_review
        return recovered

    proposed = _complete_validated_json(
        decision,
        "phase2.schema",
        schema_prompt,
        proposal,
        validate_schema,
        on_validation_error,
        salvage_invalid_proposals,
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
        relation_count_assessments=schema_reviews.get("relation_counts"),
        on_validation_error=on_validation_error,
    )
    recommendation_document = {
        "schema_version": "1",
        "ontology_path": "02-ontology/ontology.json",
        "generated_by": ontology["generated_by"],
        "unresolved": unresolved,
        "repairs": repairs,
    }
    for item in recommendation_document["unresolved"]:
        if item["id"] != item["proposal"]["id"]:
            raise ValueError("unresolved ontology proposal ID must match its original proposal")
    for item in recommendation_document["repairs"]:
        if item["id"] != item["property"]["id"]:
            raise ValueError("ontology repair ID must match its created property")
    validate_document("ontology-recommendations", recommendation_document)
    shapes = _compile_shapes(ontology)
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
    write_json(path.parent / "recommendations/unresolved.json", recommendation_document)
    fingerprint.write_text(digest + "\n", encoding="utf-8")
    (case_dir / ontology["shacl_path"]).write_text(shapes, encoding="utf-8")
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
    relation_errors = _relation_proposal_errors(ontology["relations"], classes, properties)
    rule_errors = _rule_proposal_errors(ontology["rules"], properties)
    if relation_errors or rule_errors:
        raise OntologyProposalErrors(rule_errors, relation_errors)


def _review_model_json(decision: DecisionClient, purpose: str, prompt: str, schema: dict) -> dict:
    """Obtain a typed P2 semantic review through the separate critic model family."""
    try:
        response = decision.complete_json(f"critic.phase2.{purpose}", prompt, schema)
    except ModelValidationExhausted as exc:
        raise ValueError(f"{purpose} critic returned invalid output: {exc.reason}") from exc
    Draft202012Validator(schema).validate(response)
    return response


def _normalise_words(value: str) -> str:
    return re.sub(r"[\W_]+", " ", value.casefold(), flags=re.UNICODE).strip()


def _core_field_matches_property(field: str, prop: dict) -> bool:
    field_words = set(_normalise_words(field).split())
    property_words = set(_normalise_words(f"{prop['label']} {prop['id']}").split())
    if not field_words or not property_words:
        return False
    exact = _normalise_words(field) in {
        _normalise_words(prop["label"]),
        _normalise_words(prop["id"]),
    }
    if exact:
        return True
    provenance_words = {"source", "citation", "evidence", "provenance", "reference"}
    if (property_words & provenance_words) - field_words:
        return False
    return 2 * len(field_words & property_words) >= len(field_words)


def _stable_property_id(field_label: str, properties: list[dict]) -> str:
    """Create a stable, collision-safe property ID from a reviewed PRD field label."""
    base = re.sub(r"[^a-z0-9]+", "_", field_label.casefold()).strip("_") or "core_field"
    if not base[0].isalpha():
        base = f"core_{base}"
    existing = {item["id"] for item in properties}
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _class_named_by_criterion(criterion: dict, classes: list[dict]) -> str | None:
    """Resolve an unambiguous ontology class name stated in a PRD criterion."""
    criterion_text = _normalise_words(
        " ".join(
            str(criterion.get(field) or "")
            for field in ("metric", "basis_quote", "rationale", "feasibility")
        )
    )
    if not criterion_text:
        return None
    padded_text = f" {criterion_text} "
    matches = set()
    for entity_class in classes:
        aliases = {
            _normalise_words(str(entity_class.get(field) or ""))
            for field in ("id", "label", "label_plural")
        }
        if any(alias and f" {alias} " in padded_text for alias in aliases):
            matches.add(entity_class["id"])
    return next(iter(matches)) if len(matches) == 1 else None


def _review_core_field_bindings(
    prd: dict,
    ontology: dict,
    decision: DecisionClient,
    *,
    review_override: dict | None = None,
) -> dict | None:
    completeness_criteria = [
        item for item in prd.get("definition_of_done", []) if item.get("min_ratio") is not None
    ]
    if not completeness_criteria:
        return None

    primary_class = ontology["primary_class"]
    primary_dod = [
        item for item in ontology["properties"] if item["domain"] == primary_class and item["dod"]
    ]
    if not primary_dod:
        raise ValueError(
            f"completeness requires primary class `{primary_class}` to own at least one "
            "`dod: true` property for its core PRD fields"
        )

    requirements = prd.get("requirements", [])
    requirement_ids = [item.get("id") for item in requirements]
    if any(not isinstance(item, str) or not item for item in requirement_ids):
        raise ValueError("core field review requires every PRD requirement to have an ID")
    if len(requirement_ids) != len(set(requirement_ids)):
        raise ValueError("core field review requires unique PRD requirement IDs")
    prompt = (
        "Independently inspect every approved PRD requirement and identify every core data field "
        "whose value must be present for per-entity completeness. Exclude process, navigation, "
        "presentation, and other non-field requirements; give each such requirement a concise "
        "non_core_reason. For each core field, return its requirement ID, a short field name, and "
        "the single matching ontology property ID, or null if none matches. A property counts only "
        "when it belongs to the primary class and is marked dod=true. Do not ask for or revise the "
        "PRD. Return every requirement ID exactly once. "
        f"Requirements: {json.dumps(requirements, ensure_ascii=False)}. "
        f"Primary class: {primary_class}. Properties: "
        f"{json.dumps(ontology['properties'], ensure_ascii=False)}"
    )
    response = review_override or _review_model_json(
        decision, "core_field_bindings", prompt, _CORE_FIELD_REVIEW_SCHEMA
    )
    received_ids = [item["requirement_id"] for item in response["requirements"]]
    if len(received_ids) != len(set(received_ids)) or set(received_ids) != set(requirement_ids):
        raise ValueError(
            "core field critic must assess every approved PRD requirement exactly once"
        )

    properties = {item["id"]: item for item in ontology["properties"]}
    requirements_by_id = {item["id"]: item for item in requirements}
    candidate_labels = ", ".join(f"`{item['label']}` (`{item['id']}`)" for item in primary_dod)
    bindings_seen = set()
    core_fields_seen = 0
    errors = []
    missing_fields = []
    missing_field_errors = set()
    for assessment in response["requirements"]:
        requirement_id = assessment["requirement_id"]
        core_fields = assessment["core_fields"]
        if not core_fields and not assessment["non_core_reason"]:
            errors.append(
                f"requirement `{requirement_id}` has no core field mapping or non-core explanation"
            )
            continue
        for field in core_fields:
            core_fields_seen += 1
            field_label = field["field"]
            property_id = field["property_id"]
            binding_key = (requirement_id, field_label.casefold())
            if binding_key in bindings_seen:
                errors.append(
                    f"core PRD field `{field_label}` is listed more than once for requirement "
                    f"`{requirement_id}`"
                )
                continue
            bindings_seen.add(binding_key)
            prop = properties.get(property_id) if isinstance(property_id, str) else None
            exact_matches = [
                item
                for item in primary_dod
                if _normalise_words(field_label)
                in {_normalise_words(item["label"]), _normalise_words(item["id"])}
            ]
            if len(exact_matches) == 1:
                # A normalized exact label/ID match is sufficient to bind a repaired
                # property even when the critic repeats its earlier null ID.
                prop = exact_matches[0]
            elif len(exact_matches) > 1:
                errors.append(
                    f"core PRD field `{field_label}` has multiple exact primary-class "
                    "`dod: true` property matches"
                )
                prop = None
            else:
                # Permit a narrow normalized-token synonym such as "readable name" →
                # "Record name", while rejecting weak overlap like "weekly opening
                # hours" → "Hours source".
                prop = (
                    prop
                    if prop in primary_dod and _core_field_matches_property(field_label, prop)
                    else None
                )
            if prop is None or prop["domain"] != primary_class or not prop["dod"]:
                requirement_text = requirements_by_id[requirement_id].get("description", "")
                message = (
                    f"core PRD field `{field_label}` from requirement `{requirement_id}` has no "
                    f"matching property on primary class `{primary_class}` marked `dod: true`; "
                    f"PRD wording: {requirement_text!r}. Candidate primary-class `dod: true` "
                    f"properties: {candidate_labels or '(none)'}"
                )
                errors.append(message)
                missing_field_errors.add(message)
                missing_fields.append(
                    {
                        "requirement_id": requirement_id,
                        "field": field_label,
                        "requirement_text": requirement_text,
                    }
                )
    if core_fields_seen == 0:
        errors.append(
            f"completeness has no core PRD fields mapped to `dod: true` properties of primary "
            f"class `{primary_class}`"
        )
    if errors:
        unrepairable = [item for item in errors if item not in missing_field_errors]
        if missing_fields and not unrepairable:
            raise OntologyProposalErrors(
                {},
                {},
                core_fields=missing_fields,
                core_errors=errors,
                core_review=response,
            )
        raise ValueError("; ".join(errors))
    return response


def _validate_primary_dod_presence(prd: dict, ontology: dict) -> None:
    if not any(item.get("min_ratio") is not None for item in prd.get("definition_of_done", [])):
        return
    primary_class = ontology["primary_class"]
    if not any(item["domain"] == primary_class and item["dod"] for item in ontology["properties"]):
        raise ValueError(
            f"completeness requires primary class `{primary_class}` to own at least one "
            "`dod: true` property for its core PRD fields"
        )


def _review_relation_count_semantics(
    prd: dict, ontology: dict, decision: DecisionClient
) -> dict[str, dict]:
    criteria = prd.get("definition_of_done", [])
    criterion_ids = [item.get("id") for item in criteria]
    if any(not isinstance(item, str) or not item for item in criterion_ids):
        raise ValueError("relation-count review requires every DoD criterion to have an ID")
    if len(criterion_ids) != len(set(criterion_ids)):
        raise ValueError("relation-count review requires unique DoD criterion IDs")

    prompt = (
        "Independently assess each approved DoD criterion against the declared ontology. Decide "
        "whether it counts entities because they have a typed relation. When it does, identify "
        "the single declared relation and the entity class named by the criterion that the count "
        "must measure. The current evaluator counts relation domain entities only, so the selected "
        "relation's domain must be that named class. When it does not count related entities, set "
        "counts_related_entities=false and class_id/relation_id=null. Use class IDs, labels, and "
        "the criterion's wording; do not assume a domain. Return every criterion ID exactly once. "
        f"Criteria: {json.dumps(criteria, ensure_ascii=False)}. "
        f"Classes: {json.dumps(ontology['classes'], ensure_ascii=False)}. "
        f"Relations: {json.dumps(ontology['relations'], ensure_ascii=False)}"
    )
    response = _review_model_json(
        decision, "relation_count_semantics", prompt, _RELATION_COUNT_REVIEW_SCHEMA
    )
    assessments = response["assessments"]
    received_ids = [item["criterion_id"] for item in assessments]
    if len(received_ids) != len(set(received_ids)) or set(received_ids) != set(criterion_ids):
        raise ValueError(
            "relation-count critic must assess every approved DoD criterion exactly once"
        )

    classes = {item["id"]: item for item in ontology["classes"]}
    relations = {item["id"]: item for item in ontology["relations"]}
    by_criterion = {}
    for assessment in assessments:
        criterion_id = assessment["criterion_id"]
        if not assessment["counts_related_entities"]:
            if assessment["class_id"] is not None or assessment["relation_id"] is not None:
                raise ValueError(
                    f"relation-count critic for criterion `{criterion_id}` must leave class_id "
                    "and relation_id null when it does not count related entities"
                )
            by_criterion[criterion_id] = assessment
            continue

        class_id = assessment["class_id"]
        relation_id = assessment["relation_id"]
        if class_id not in classes:
            raise ValueError(
                f"relation-count critic for criterion `{criterion_id}` names an unknown counted class"
            )
        relation = relations.get(relation_id)
        if relation is None:
            raise ValueError(
                f"relation-count critic for criterion `{criterion_id}` names an unknown relation"
            )
        criterion = next(item for item in criteria if item["id"] == criterion_id)
        named_class = _class_named_by_criterion(criterion, ontology["classes"])
        if named_class is not None and named_class != class_id:
            raise ValueError(
                f"criterion `{criterion_id}` names class `{named_class}`, but its relation-count "
                f"critic selected class `{class_id}`"
            )
        if relation["domain"] != class_id:
            raise ValueError(
                f"criterion `{criterion_id}` names counted class `{class_id}`, but relation "
                f"`{relation_id}` is oriented `{relation['domain']}` → `{relation['range']}`; "
                "count_entities_with_relation measures the relation domain. Reverse or replace "
                "the ontology relation so the named counted class is its domain"
            )
        by_criterion[criterion_id] = assessment
    return by_criterion


def _review_rule_semantics(ontology: dict, decision: DecisionClient) -> None:
    rules = [item for item in ontology["rules"] if item.get("predicate") is not None]
    if not rules:
        return
    prompt = (
        "Independently compare each executable rule's typed predicate with its label, checks, and "
        "verify text. Decide whether the predicate actually expresses the described check; a rule "
        "that merely has valid property IDs can still be semantically wrong. Assess each rule ID "
        "exactly once, do not rewrite any content, and make each reason name the concrete mismatch. "
        f"Executable rules: {json.dumps(rules, ensure_ascii=False)}. "
        f"Declared properties: {json.dumps(ontology['properties'], ensure_ascii=False)}"
    )
    response = _review_model_json(decision, "rule_semantics", prompt, _RULE_SEMANTICS_REVIEW_SCHEMA)
    expected_ids = {item["id"] for item in rules}
    received_ids = [item["rule_id"] for item in response["assessments"]]
    if len(received_ids) != len(set(received_ids)) or set(received_ids) != expected_ids:
        raise ValueError("rule critic must assess every executable rule exactly once")
    errors = {
        item["rule_id"]: (
            f"rule `{item['rule_id']}` label/checks do not match its executable predicate: "
            f"{item['reason']}"
        )
        for item in response["assessments"]
        if not item["matches"]
    }
    if errors:
        raise OntologyProposalErrors(errors, {})


def _review_ontology_semantics(
    prd: dict,
    ontology: dict,
    decision: DecisionClient,
    *,
    core_field_review: dict | None = None,
) -> dict[str, dict] | None:
    core_error = None
    core_blocker = None
    try:
        _review_core_field_bindings(prd, ontology, decision, review_override=core_field_review)
    except OntologyProposalErrors as exc:
        core_error = exc
    except (ValidationError, ValueError) as exc:
        core_blocker = str(exc)

    relation_counts = None
    relation_blocker = None
    if ontology["relations"]:
        try:
            relation_counts = _review_relation_count_semantics(prd, ontology, decision)
        except (ValidationError, ValueError) as exc:
            relation_blocker = str(exc)

    rule_error = None
    rule_blocker = None
    try:
        _review_rule_semantics(ontology, decision)
    except OntologyProposalErrors as exc:
        rule_error = exc
    except (ValidationError, ValueError) as exc:
        rule_blocker = str(exc)
    blockers = [item for item in (core_blocker, relation_blocker) if item]
    if blockers:
        if rule_error is not None:
            blockers.append(str(rule_error))
        if rule_blocker is not None:
            blockers.append(rule_blocker)
        if core_error is not None:
            blockers.append(str(core_error))
        raise ValueError("; ".join(blockers))
    if rule_blocker is not None:
        raise OntologyProposalErrors(
            rule_error.rule_errors if rule_error is not None else {},
            {},
            core_fields=core_error.core_fields if core_error is not None else None,
            core_errors=core_error.core_errors if core_error is not None else None,
            core_review=core_error.core_review if core_error is not None else None,
            drop_all_rules_reason=(
                f"Rule critic could not return a valid assessment: {rule_blocker}. "
                "Executable rules were set aside for human review."
            ),
        )
    if rule_error is not None or core_error is not None:
        raise OntologyProposalErrors(
            rule_error.rule_errors if rule_error is not None else {},
            rule_error.relation_errors if rule_error is not None else {},
            core_fields=core_error.core_fields if core_error is not None else None,
            core_errors=core_error.core_errors if core_error is not None else None,
            core_review=core_error.core_review if core_error is not None else None,
            drop_all_rules_reason=(
                rule_error.drop_all_rules_reason if rule_error is not None else None
            ),
        )
    return relation_counts


def _relation_proposal_errors(
    relations: list[dict], classes: dict[str, dict], properties: dict[str, dict]
) -> dict[str, str]:
    errors = {}
    for relation in relations:
        details = []
        domain_class = relation["domain"]
        range_class = relation["range"]
        if domain_class not in classes:
            details.append(f"domain class `{domain_class}` is not declared")
        if range_class not in classes:
            details.append(f"range class `{range_class}` is not declared")
        if relation["symmetric"] and domain_class != range_class:
            details.append("a symmetric relation must have the same class at both endpoints")

        match = relation.get("match")
        if match is not None:
            domain_property_id = match["domain_property"]
            range_property_id = match["range_property"]
            domain_property = properties.get(domain_property_id)
            range_property = properties.get(range_property_id)
            if domain_property is None:
                details.append(f"domain_property `{domain_property_id}` is not declared")
            elif domain_property["domain"] != domain_class:
                details.append(
                    f"domain_property `{domain_property_id}` belongs to class "
                    f"`{domain_property['domain']}`, but relation domain `{domain_class}` "
                    f"requires a property of `{domain_class}`"
                )
            if range_property is None:
                details.append(f"range_property `{range_property_id}` is not declared")
            elif range_property["domain"] != range_class:
                details.append(
                    f"range_property `{range_property_id}` belongs to class "
                    f"`{range_property['domain']}`, but relation range `{range_class}` "
                    f"requires a property of `{range_class}`"
                )
            if (
                domain_property is not None
                and range_property is not None
                and _semantic_datatype(domain_property["datatype"])
                != _semantic_datatype(range_property["datatype"])
            ):
                details.append("the endpoint properties have different datatypes")
            if relation["symmetric"] and domain_property_id != range_property_id:
                details.append("a symmetric relation must match the same property at both ends")

        if details:
            errors[relation["id"]] = (
                f"relation `{relation['id']}` is invalid: {'; '.join(details)}. "
                f"{_ALLOWED_RELATION_MATCH}"
            )
    return errors


def _rule_proposal_errors(rules: list[dict], properties: dict[str, dict]) -> dict[str, str]:
    errors = {}
    for rule in rules:
        predicate = rule.get("predicate")
        if predicate is None:
            continue
        details = []
        operand_domains = set()
        for term in predicate["all"]:
            operation = term["op"]
            if operation == "same_value":
                left_id = term["left_property"]
                right_id = term["right_property"]
                left = properties.get(left_id)
                right = properties.get(right_id)
                if left is None or right is None:
                    missing = [
                        f"`{property_id}`"
                        for property_id, value in ((left_id, left), (right_id, right))
                        if value is None
                    ]
                    details.append(f"same_value references unknown property {', '.join(missing)}")
                if left is not None:
                    operand_domains.add(left["domain"])
                if right is not None:
                    operand_domains.add(right["domain"])
                if (
                    left is not None
                    and right is not None
                    and _semantic_datatype(left["datatype"])
                    != _semantic_datatype(right["datatype"])
                ):
                    details.append(
                        "same_value properties must have the same datatype "
                        f"( `{left_id}`: {left['datatype']}; `{right_id}`: {right['datatype']} )"
                    )
            elif operation == "equals":
                property_id = term["property"]
                prop = properties.get(property_id)
                if prop is None:
                    details.append(f"equals references unknown property `{property_id}`")
                else:
                    operand_domains.add(prop["domain"])
                    try:
                        _validate_typed_literal(term["value"], prop["datatype"])
                    except ValueError as exc:
                        details.append(str(exc))
            elif operation == "date_before":
                earlier_id = term["earlier_property"]
                later_id = term["later_property"]
                earlier = properties.get(earlier_id)
                later = properties.get(later_id)
                if earlier is None or later is None:
                    missing = [
                        f"`{property_id}`"
                        for property_id, value in ((earlier_id, earlier), (later_id, later))
                        if value is None
                    ]
                    details.append(f"date_before references unknown property {', '.join(missing)}")
                if earlier is not None:
                    operand_domains.add(earlier["domain"])
                if later is not None:
                    operand_domains.add(later["domain"])
                if earlier is not None and later is not None:
                    earlier_type = _semantic_datatype(earlier["datatype"])
                    later_type = _semantic_datatype(later["datatype"])
                    if earlier_type not in {"date", "datetime"} or earlier_type != later_type:
                        details.append(
                            "date_before properties must share date or datetime datatype "
                            f"(`{earlier_id}`: {earlier['datatype']}; "
                            f"`{later_id}`: {later['datatype']})"
                        )
            else:
                details.append(f"unsupported rule predicate operator `{operation}`")

        if len(operand_domains) > 1:
            details.append(
                "all rule predicate properties must belong to one class; "
                f"this rule references classes {', '.join(f'`{value}`' for value in sorted(operand_domains))}"
            )
        if details:
            errors[rule["id"]] = (
                f"rule `{rule['id']}` is invalid: {'; '.join(details)}. {_ALLOWED_RULE_PREDICATES}"
            )
    return errors


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
    relation_count_assessments: dict[str, dict] | None = None,
    on_validation_error: ValidationErrorCallback | None = None,
) -> dict:
    schema = model_output_schema("dod-queries")
    prompt = (
        "Compile every approved definition-of-done criterion into exactly one safe declarative "
        "query. Use only ontology class/property/relation IDs and the supported aggregate/condition operators. "
        "For a per-entity completeness criterion, use entities_meeting_completeness with "
        "class equal to the primary class, properties='dod', and min_ratio copied from the PRD. "
        "When its approved target is fractional (less than 1), measure the share of primary-class "
        "entities linked by the chosen primary-domain relation that meet min_ratio; set measure="
        "'share' and include that relation_id explicitly. Use measure='count' only for whole-entity "
        "count targets. "
        "For each criterion the separate relation-count critic marked as counting related "
        "entities, use count_entities_with_relation with the exact reviewed relation_id and "
        "class_id. The relation domain must be the class named by that criterion. Do not use the "
        "relation aggregate for a criterion the critic marked as an ordinary entity count. "
        "Copy each criterion's target and comparison operator exactly. Do not write SQL or code. "
        f"Criteria: {prd['definition_of_done']}. Classes: {ontology['classes']}. "
        f"Properties: {ontology['properties']}. Relations: {ontology['relations']}. "
        f"Source classes: {ontology.get('source_classes', [])}. "
        f"Reviewed relation-count semantics: {relation_count_assessments or {}}."
    )

    def validate_queries(response: dict) -> None:
        document = {
            **response,
            "generated_by": generated_by(decision),
            "prd_path": "01-scope/prd.json",
            "ontology_version": ontology["version"],
        }
        validate_document("dod-queries", document)
        _validate_queries(
            prd,
            ontology,
            document,
            relation_count_assessments=relation_count_assessments,
        )

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


def _legacy_completeness_relation_id(
    query: dict,
    queries: list[dict],
    relations: dict[str, dict],
    primary_class: str,
) -> str:
    """Resolve an approved pre-measure query from its unique primary relation count."""
    relation_id = query.get("relation_id")
    if relation_id is not None:
        return relation_id

    matching_relation_ids = []
    for candidate in queries:
        if candidate["aggregate"] != "count_entities_with_relation":
            continue
        candidate_relation = relations.get(candidate.get("relation_id"))
        if candidate_relation is None or candidate_relation["domain"] != primary_class:
            continue
        named_classes = {
            value for value in (candidate.get("class_id"), candidate.get("class")) if value
        }
        if len(named_classes) > 1 or (named_classes and named_classes != {primary_class}):
            continue
        matching_relation_ids.append(candidate["relation_id"])

    unique_relation_ids = set(matching_relation_ids)
    if len(unique_relation_ids) != 1:
        raise ValueError(
            "legacy completeness share needs exactly one matching primary-class relation-count "
            "query; add relation_id explicitly"
        )
    return unique_relation_ids.pop()


def _validate_queries(
    prd: dict,
    ontology: dict,
    document: dict,
    *,
    relation_count_assessments: dict[str, dict] | None = None,
    allow_legacy_completeness: bool = False,
) -> None:
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
            if query.get("properties") != "dod":
                raise ValueError("completeness query must include all primary-class DoD properties")
            primary_dod = [
                item
                for item in ontology["properties"]
                if item["domain"] == ontology["primary_class"] and item["dod"]
            ]
            if not primary_dod:
                raise ValueError(
                    f"completeness query's primary class `{ontology['primary_class']}` has no "
                    "`dod: true` properties to measure"
                )
            measure = query.get("measure")
            target = query["target"]
            if measure == "count" and target < 1:
                raise ValueError("fractional completeness target requires `measure: share`")
            is_share = measure == "share" or (measure is None and target < 1)
            if is_share:
                if measure is None and not allow_legacy_completeness:
                    raise ValueError("fractional completeness target requires `measure: share`")
                if target > 1:
                    raise ValueError("completeness share target must be at most 1")
                relation_id = query.get("relation_id")
                if relation_id is None and allow_legacy_completeness and measure is None:
                    relation_id = _legacy_completeness_relation_id(
                        query, queries, relations, ontology["primary_class"]
                    )
                relation = relations.get(relation_id)
                if relation is None:
                    raise ValueError(
                        "completeness share requires a known primary-domain relation_id"
                    )
                if relation["domain"] != ontology["primary_class"]:
                    raise ValueError(
                        "completeness share relation_id must have the primary class as its domain"
                    )
        class_id = query.get("class_id", query.get("class"))
        if query["aggregate"] == "count_entities_with_relation":
            relation = relations.get(query["relation_id"])
            if relation is None:
                raise ValueError(
                    f"DoD criterion `{query['criterion_id']}` references an unknown relation"
                )
            named_classes = {
                value for value in (query.get("class_id"), query.get("class")) if value is not None
            }
            if len(named_classes) > 1:
                raise ValueError(
                    f"DoD criterion `{query['criterion_id']}` supplies conflicting count classes"
                )
            assessment = None
            if relation_count_assessments is not None:
                assessment = relation_count_assessments.get(query["criterion_id"])
                if assessment is None:
                    raise ValueError(
                        f"DoD criterion `{query['criterion_id']}` has no relation-count review"
                    )
                if not assessment["counts_related_entities"]:
                    raise ValueError(
                        f"DoD criterion `{query['criterion_id']}` was reviewed as an ordinary "
                        "entity count, not a relation count"
                    )
                if query["relation_id"] != assessment["relation_id"]:
                    raise ValueError(
                        f"DoD criterion `{query['criterion_id']}` must count relation `"
                        f"{assessment['relation_id']}` named by the semantic review"
                    )
                expected_class = assessment["class_id"]
                if named_classes and next(iter(named_classes)) != expected_class:
                    raise ValueError(
                        f"DoD criterion `{query['criterion_id']}` must count the named class `"
                        f"{expected_class}`"
                    )
                if relation["domain"] != expected_class:
                    raise ValueError(
                        f"DoD criterion `{query['criterion_id']}` names counted class `"
                        f"{expected_class}`, but relation `{relation['id']}` domain is `"
                        f"{relation['domain']}`"
                    )
            if named_classes and next(iter(named_classes)) != relation["domain"]:
                raise ValueError(
                    f"DoD criterion `{query['criterion_id']}` counts class `"
                    f"{next(iter(named_classes))}`, but relation `{relation['id']}` is oriented `"
                    f"{relation['domain']}` → `{relation['range']}`; its relation domain is `"
                    f"{relation['domain']}`, and relation counts use that domain class"
                )
            named_criterion_class = _class_named_by_criterion(criterion, ontology["classes"])
            if named_criterion_class and relation["domain"] != named_criterion_class:
                raise ValueError(
                    f"DoD criterion `{query['criterion_id']}` names counted class `"
                    f"{named_criterion_class}`, but relation `{relation['id']}` is oriented `"
                    f"{relation['domain']}` → `{relation['range']}`; its relation domain is `"
                    f"{relation['domain']}`, and relation counts use that domain class"
                )
            class_id = relation["domain"]
        elif relation_count_assessments is not None:
            assessment = relation_count_assessments.get(query["criterion_id"])
            if assessment is None:
                raise ValueError(
                    f"DoD criterion `{query['criterion_id']}` has no relation-count review"
                )
            if assessment["counts_related_entities"]:
                raise ValueError(
                    f"DoD criterion `{query['criterion_id']}` must count relation `"
                    f"{assessment['relation_id']}` for named class `{assessment['class_id']}`"
                )
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
