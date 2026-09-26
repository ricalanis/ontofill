"""Evidence-preserving observations and ontology-driven gold refinement."""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker
from pyshacl import validate as shacl_validate
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from ontofill.refiner.provenance import validate_generated_by, validate_run_provenance

ONTO = Namespace("https://ontofill.dev/ontology/")
SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "schemas"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_value_id(entity_id: str, property_id: str, value: str | float | bool) -> str:
    """Keep typed values and their IDs stable across sources and runs."""
    digest = hashlib.sha256(_canonical([entity_id, property_id, value]).encode()).hexdigest()
    return f"val:{digest[:24]}"


@dataclass(frozen=True)
class Observation:
    run_id: str
    entity_id: str
    entity_class: str
    property_id: str
    value: str | int | float | bool
    evidence: dict
    step_id: str
    generated_by: dict[str, str]
    confidence: float = 1.0
    classified_as: tuple[str, ...] = ()
    value_id: str = field(default="")

    def __post_init__(self) -> None:
        if not self.value_id:
            object.__setattr__(
                self, "value_id", stable_value_id(self.entity_id, self.property_id, self.value)
            )


class SilverStore(Protocol):
    def add(self, observation: Observation) -> None: ...

    def list_for_run(self, run_id: str) -> list[Observation]: ...


class MemorySilverStore:
    """Deterministic store for synthetic tests and isolated local runs."""

    def __init__(self) -> None:
        self._items: dict[str, Observation] = {}

    def add(self, observation: Observation) -> None:
        self._items[_observation_id(observation)] = observation

    def list_for_run(self, run_id: str) -> list[Observation]:
        return sorted(
            (item for item in self._items.values() if item.run_id == run_id),
            key=_observation_id,
        )


class PostgresSilverStore:
    """Silver observations in Postgres; duplicates retain their evidence and step."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.getenv("SILVER_DATABASE_URL", "")
        if not self.dsn:
            raise ValueError("SILVER_DATABASE_URL is required for Postgres silver storage")
        import psycopg

        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS silver_observations (
                    observation_id text PRIMARY KEY,
                    run_id text NOT NULL,
                    payload jsonb NOT NULL
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS silver_observations_run_idx "
                "ON silver_observations (run_id)"
            )

    def add(self, observation: Observation) -> None:
        import psycopg

        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                "INSERT INTO silver_observations (observation_id, run_id, payload) "
                "VALUES (%s, %s, %s::jsonb) ON CONFLICT (observation_id) DO NOTHING",
                (_observation_id(observation), observation.run_id, _canonical(asdict(observation))),
            )

    def list_for_run(self, run_id: str) -> list[Observation]:
        import psycopg

        with psycopg.connect(self.dsn) as connection:
            rows = connection.execute(
                "SELECT payload FROM silver_observations WHERE run_id = %s ORDER BY observation_id",
                (run_id,),
            ).fetchall()
        return [Observation(**row[0]) for row in rows]


def silver_store_from_env() -> SilverStore:
    return PostgresSilverStore() if os.getenv("SILVER_DATABASE_URL") else MemorySilverStore()


def _observation_id(observation: Observation) -> str:
    return hashlib.sha256(_canonical(asdict(observation)).encode()).hexdigest()


def _evidence_validator() -> Draft202012Validator:
    common = json.loads((SCHEMA_ROOT / "common.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(
        {"$defs": common["$defs"], "$ref": "#/$defs/evidence"},
        format_checker=FormatChecker(),
    )


def _ontology_declarations(ontology: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    classes = {item["id"]: item for item in ontology["classes"]}
    properties = {item["id"]: item for item in ontology["properties"]}
    if len(classes) != len(ontology["classes"]) or len(properties) != len(ontology["properties"]):
        raise ValueError("ontology contains duplicate class or property IDs")
    if ontology["primary_class"] not in classes:
        raise ValueError("ontology primary class is unknown")
    for prop in properties.values():
        if prop["domain"] not in classes:
            raise ValueError(f"unknown ontology property domain: {prop['domain']}")
    for class_id, entity_class in classes.items():
        for key in ("title_property", "identifier_property"):
            property_id = entity_class[key]
            if property_id not in properties or properties[property_id]["domain"] != class_id:
                raise ValueError(f"ontology {key} is outside class {class_id}")
    return classes, properties


def _datatype(value: object, datatype: str) -> URIRef:
    name = datatype.rsplit("#", 1)[-1].rsplit("/", 1)[-1].rsplit(":", 1)[-1].strip().lower()
    if value is None or isinstance(value, (dict, list)):
        raise TypeError("observation value must be a non-null scalar")
    if name in {"string", "text", "normalizedstring", "token"}:
        if not isinstance(value, str):
            raise TypeError("ontology string property requires a string")
        return XSD.string
    if name in {"boolean", "bool"}:
        if not isinstance(value, bool):
            raise TypeError("ontology boolean property requires a boolean")
        return XSD.boolean
    if name in {"integer", "int", "long", "nonnegativeinteger"}:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("ontology integer property requires an integer")
        if name == "nonnegativeinteger" and value < 0:
            raise ValueError("ontology nonnegative integer cannot be negative")
        return XSD.integer
    if name in {"number", "decimal", "float", "double"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("ontology number property requires a number")
        if not math.isfinite(value):
            raise ValueError("ontology number must be finite")
        return XSD.decimal if name == "decimal" else XSD.double
    if name == "date":
        if not isinstance(value, str):
            raise TypeError("ontology date property requires an ISO date string")
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError as exc:
            raise ValueError("SHACL: date property requires an ISO date") from exc
        return XSD.date
    if name in {"datetime", "date-time"}:
        if not isinstance(value, str):
            raise TypeError("ontology dateTime property requires an ISO timestamp")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("SHACL: dateTime property requires an ISO timestamp") from exc
        if parsed.tzinfo is None:
            raise ValueError("SHACL: dateTime requires a timezone")
        return XSD.dateTime
    if name in {"uri", "url", "anyuri"}:
        if not isinstance(value, str) or not urlparse(value).scheme:
            raise ValueError("SHACL: URI property requires an absolute URI")
        return XSD.anyURI
    raise ValueError(f"unsupported ontology datatype: {datatype}")


def _shacl_error(observation: Observation, shapes: Graph, datatype: URIRef) -> str | None:
    if not shapes:
        return None
    data = Graph()
    subject = URIRef(f"urn:ontofill:entity:{observation.entity_id}")
    data.add((subject, RDF.type, ONTO[observation.entity_class]))
    data.add(
        (subject, ONTO[observation.property_id], Literal(observation.value, datatype=datatype))
    )
    conforms, _, report = shacl_validate(data, shacl_graph=shapes, inference="none")
    return None if conforms else f"SHACL: {report}"


@dataclass
class Refinement:
    entities: list[dict]
    rejected: list[dict[str, str]]


def refine_observations(
    observations: list[Observation],
    *,
    ontology: dict,
    generated_by: dict[str, str],
    shapes_ttl: str | Path | None = None,
) -> Refinement:
    """Validate observed values against ontology; preserve missing and conflicts."""
    run_provenance = validate_generated_by(generated_by)
    classes, properties = _ontology_declarations(ontology)
    evidence_validator = _evidence_validator()
    shapes = Graph()
    if shapes_ttl is not None:
        shapes.parse(str(shapes_ttl), format="turtle")
        # Required properties are materialized as missing in gold; validate each
        # observed value independently against all other ontology constraints.
        sh_min_count = URIRef("http://www.w3.org/ns/shacl#minCount")
        for triple in list(shapes.triples((None, sh_min_count, None))):
            shapes.remove(triple)
    by_entity: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    rejected: list[dict[str, str]] = []
    for observation in observations:
        try:
            observation_provenance = validate_run_provenance(
                observation.run_id, observation.generated_by
            )
            if observation_provenance["backend"] != run_provenance["backend"]:
                raise ValueError("observation inference backend differs from run")
            if observation.entity_class not in classes:
                raise ValueError("entity class is absent from ontology")
            if not observation.entity_id.startswith(f"{observation.entity_class}:"):
                raise ValueError("entity ID must start with its ontology class ID")
            prop = properties.get(observation.property_id)
            if prop is None or prop["domain"] != observation.entity_class:
                raise ValueError("property is absent from this ontology class")
            datatype = _datatype(observation.value, prop["datatype"])
            if (
                isinstance(observation.confidence, bool)
                or not isinstance(observation.confidence, (int, float))
                or not math.isfinite(observation.confidence)
                or not 0 <= observation.confidence <= 1
            ):
                raise ValueError("confidence must be between 0 and 1")
            if observation.value_id != stable_value_id(
                observation.entity_id, observation.property_id, observation.value
            ):
                raise ValueError("value_id does not match the observed value")
            error = next(evidence_validator.iter_errors(observation.evidence), None)
            if error:
                raise ValueError(f"evidence contract: {error.message}")
            shacl_error = _shacl_error(observation, shapes, datatype)
            if shacl_error:
                raise ValueError(shacl_error)
        except (KeyError, TypeError, ValueError) as exc:
            rejected.append({"value_id": observation.value_id, "reason": str(exc)})
            continue
        by_entity[(observation.entity_class, observation.entity_id)].append(observation)

    entities: list[dict] = []
    for (entity_class, entity_id), candidates in sorted(by_entity.items()):
        values: dict[str, dict] = {}
        for property_id, prop in sorted(properties.items()):
            if prop["domain"] != entity_class:
                continue
            property_candidates = [item for item in candidates if item.property_id == property_id]
            if not property_candidates:
                values[property_id] = _missing(run_provenance)
                continue
            grouped: dict[str, list[Observation]] = defaultdict(list)
            for item in property_candidates:
                grouped[_canonical(item.value)].append(item)
            chosen = min(
                property_candidates,
                key=lambda item: (-item.confidence, _canonical(item.value), item.value_id),
            )
            evidence = sorted(
                {
                    _canonical(item.evidence): item.evidence
                    for item in grouped[_canonical(chosen.value)]
                }.values(),
                key=_canonical,
            )
            values[property_id] = {
                "value_id": chosen.value_id,
                "value": chosen.value,
                "confidence": chosen.confidence,
                "status": "gold" if len(grouped) == 1 else "conflict",
                "evidence": evidence,
                "generated_by": validate_generated_by(chosen.generated_by),
            }
        entities.append(
            {
                "id": entity_id,
                "class": entity_class,
                "classified_as": sorted(
                    {node for item in candidates for node in item.classified_as}
                ),
                "properties": values,
                "links": [],
                "flags": [],
                "generated_by": run_provenance.copy(),
            }
        )
    return Refinement(entities=entities, rejected=rejected)


def _missing(generated_by: dict[str, str]) -> dict:
    return {
        "value": None,
        "confidence": 0,
        "status": "missing",
        "evidence": [],
        "generated_by": generated_by.copy(),
    }
