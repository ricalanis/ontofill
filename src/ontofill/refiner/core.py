"""Evidence-preserving silver observations and deterministic gold refinement."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol

from jsonschema import Draft202012Validator, FormatChecker
from pyshacl import validate as shacl_validate
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

CORE_FIELDS = (
    "legal_name",
    "tax_id",
    "address",
    "founding_date",
    "tax_list_status",
    "sanction_status",
)
ONTO = Namespace("https://ontofill.dev/ontology/")
SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "schemas"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_value_id(supplier_id: str, field_name: str, value: str) -> str:
    """The same observed value has the same ID across sources and repeat runs."""
    digest = hashlib.sha256(_canonical([supplier_id, field_name, value]).encode()).hexdigest()
    return f"val:{digest[:24]}"


@dataclass(frozen=True)
class Observation:
    run_id: str
    supplier_id: str
    field: str
    value: str
    evidence: dict[str, str]
    step_id: str
    confidence: float = 1.0
    classified_as: tuple[str, ...] = ()
    value_id: str = field(default="")

    def __post_init__(self) -> None:
        if not self.value_id:
            object.__setattr__(
                self, "value_id", stable_value_id(self.supplier_id, self.field, self.value)
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
    """Silver observations in Postgres; duplicates remain distinct by evidence and step."""

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


def _supplier_validator() -> Draft202012Validator:
    from referencing import Registry, Resource

    schemas = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in SCHEMA_ROOT.glob("*.schema.json")
    }
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )
    return Draft202012Validator(
        schemas["supplier.schema.json"], registry=registry, format_checker=FormatChecker()
    )


def _shapes() -> Graph:
    graph = Graph()
    for field_name in CORE_FIELDS:
        predicate = ONTO[field_name]
        shape = URIRef(f"urn:ontofill:shape:{field_name}")
        property_shape = URIRef(f"urn:ontofill:shape:{field_name}:property")
        sh = Namespace("http://www.w3.org/ns/shacl#")
        graph.add((shape, RDF.type, sh.NodeShape))
        graph.add((shape, sh.targetSubjectsOf, predicate))
        graph.add((shape, sh.property, property_shape))
        graph.add((property_shape, sh.path, predicate))
        graph.add((property_shape, sh.minLength, Literal(1)))
        graph.add((property_shape, sh.datatype, XSD.string))
    return graph


def _shacl_error(observation: Observation, shapes: Graph) -> str | None:
    data = Graph()
    subject = URIRef(f"urn:ontofill:supplier:{observation.supplier_id.removeprefix('sup:')}")
    data.add((subject, RDF.type, ONTO.Supplier))
    predicate = ONTO[observation.field]
    if observation.field == "founding_date":
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", observation.value):
                raise ValueError("invalid lexical date")
            date.fromisoformat(observation.value)
        except ValueError:
            return "SHACL: founding_date must be an ISO date"
    literal = Literal(observation.value)
    data.add((subject, predicate, literal))
    conforms, _, report = shacl_validate(data, shacl_graph=shapes, inference="none")
    return None if conforms else str(report)


@dataclass
class Refinement:
    suppliers: list[dict]
    rejected: list[dict[str, str]]


def refine_observations(
    observations: list[Observation], *, shapes_ttl: str | Path | None = None
) -> Refinement:
    """Refine observed values only; failed SHACL/provenance creates no gold value."""
    shapes = _shapes()
    if shapes_ttl is not None:
        external = Graph().parse(str(shapes_ttl), format="turtle")
        # Required properties become explicit `missing` fields in gold. Validate
        # properties that were actually observed against ontology constraints.
        sh_min_count = URIRef("http://www.w3.org/ns/shacl#minCount")
        for triple in list(external.triples((None, sh_min_count, None))):
            external.remove(triple)
        shapes += external
    schema = _supplier_validator()
    by_supplier: dict[str, list[Observation]] = defaultdict(list)
    rejected: list[dict[str, str]] = []
    for observation in observations:
        try:
            if observation.field not in CORE_FIELDS:
                raise ValueError("field is outside the approved core schema")
            if not observation.supplier_id.startswith("sup:"):
                raise ValueError("supplier_id must start with sup:")
            if not isinstance(observation.value, str):
                raise TypeError("observation value must be a string")
            if not 0 <= observation.confidence <= 1:
                raise ValueError("confidence must be between 0 and 1")
            if observation.value_id != stable_value_id(
                observation.supplier_id, observation.field, observation.value
            ):
                raise ValueError("value_id does not match the observed value")
            evidence_error = next(
                schema.iter_errors(
                    {
                        "id": observation.supplier_id,
                        "classified_as": [],
                        "fields": {
                            name: (
                                {
                                    "value_id": observation.value_id,
                                    "value": observation.value,
                                    "confidence": observation.confidence,
                                    "status": "gold",
                                    "evidence": [observation.evidence],
                                }
                                if name == observation.field
                                else _missing()
                            )
                            for name in CORE_FIELDS
                        },
                        "flags": [],
                        "links": [],
                        "contract_ids": [],
                    }
                ),
                None,
            )
            if evidence_error:
                raise ValueError(f"contract schema: {evidence_error.message}")
            shacl_error = _shacl_error(observation, shapes)
            if shacl_error:
                raise ValueError(f"SHACL: {shacl_error}")
        except (TypeError, ValueError) as exc:
            rejected.append({"value_id": observation.value_id, "reason": str(exc)})
            continue
        by_supplier[observation.supplier_id].append(observation)

    suppliers: list[dict] = []
    for supplier_id, candidates in sorted(by_supplier.items()):
        fields: dict[str, dict] = {}
        for field_name in CORE_FIELDS:
            field_candidates = [item for item in candidates if item.field == field_name]
            if not field_candidates:
                fields[field_name] = _missing()
                continue
            grouped: dict[str, list[Observation]] = defaultdict(list)
            for candidate in field_candidates:
                grouped[candidate.value].append(candidate)
            chosen = min(
                field_candidates,
                key=lambda item: (-item.confidence, item.value, item.value_id),
            )
            evidence = sorted(
                {
                    _canonical(item.evidence): item.evidence for item in grouped[chosen.value]
                }.values(),
                key=_canonical,
            )
            fields[field_name] = {
                "value_id": chosen.value_id,
                "value": chosen.value,
                "confidence": chosen.confidence,
                "status": "gold" if len(grouped) == 1 else "conflict",
                "evidence": evidence,
            }
        record = {
            "id": supplier_id,
            "classified_as": sorted({node for item in candidates for node in item.classified_as}),
            "fields": fields,
            "flags": [],
            "links": [],
            "contract_ids": [],
        }
        schema.validate(record)
        suppliers.append(record)
    return Refinement(suppliers=suppliers, rejected=rejected)


def _missing() -> dict:
    return {"value": None, "confidence": 0, "status": "missing", "evidence": []}
