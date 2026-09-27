"""Validated, atomic-pointer generic gold exports into the shared lake layout."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from ontofill.refiner.core import (
    SCHEMA_ROOT,
    _canonical,
    _datatype,
    _evaluate_rule,
    _ontology_declarations,
    _relation_pairs,
    _signal_definitions,
    stable_value_id,
)
from ontofill.refiner.core import (
    taxonomy_levels as _taxonomy_levels,
)
from ontofill.refiner.provenance import validate_generated_by, validate_run_provenance


class GoldLake(Protocol):
    def write_key(self, key: str, data: bytes) -> None: ...

    def exists(self, key: str) -> bool: ...


def _validators() -> dict[str, Draft202012Validator]:
    schemas = {
        path.name.removesuffix(".schema.json"): json.loads(path.read_text(encoding="utf-8"))
        for path in SCHEMA_ROOT.glob("*.schema.json")
    }
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )
    return {
        name: Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
        for name, schema in schemas.items()
    }


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _jsonl_bytes(records: Sequence[dict]) -> bytes:
    return b"".join(_json_bytes(record) for record in records)


def _check_evidence(lake: GoldLake, evidence: list[dict]) -> None:
    for item in evidence:
        for key in ("bronze_key", "screenshot_key"):
            if not lake.exists(item[key]):
                raise ValueError(f"evidence object absent from bronze: {item[key]}")


def _read_case_json(
    case_dir: Path, relative_path: str, validator: Draft202012Validator | None
) -> dict:
    root = case_dir.resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"case artifact escapes case directory: {relative_path}")
    if not path.is_file():
        raise ValueError(f"missing case lineage artifact: {relative_path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if validator:
        validator.validate(document)
    return document


def _check_lineage(
    case_dir: Path,
    entities: Sequence[dict],
    trace: Sequence[dict],
    ontology: dict,
    dod_queries: dict,
    validators: dict[str, Draft202012Validator],
    generated_by: dict[str, str],
) -> None:
    by_value: dict[str, list[dict]] = {}
    for step in trace:
        for value_id in step["value_ids"]:
            by_value.setdefault(value_id, []).append(step)
    objective_doc = _read_case_json(case_dir, "03-fanout/objectives.json", validators["objectives"])
    ontology_doc = _read_case_json(case_dir, "02-ontology/ontology.json", validators["ontology"])
    query_doc = _read_case_json(case_dir, "02-ontology/dod-queries.json", validators["dod-queries"])
    if _canonical(ontology_doc) != _canonical(ontology):
        raise ValueError("ontology argument differs from approved case artifact")
    if _canonical(query_doc) != _canonical(dod_queries):
        raise ValueError("DoD queries differ from approved case artifact")
    if objective_doc["ontology_version"] != ontology_doc["version"]:
        raise ValueError("objectives ontology version does not match ontology artifact")
    if query_doc.get("ontology_version", ontology_doc["version"]) != ontology_doc["version"]:
        raise ValueError("DoD query ontology version does not match ontology artifact")
    prd_path = objective_doc["prd_path"]
    global_prd = _read_case_json(case_dir, prd_path, validators["global-prd"])
    approved_criteria = {item["id"]: item for item in global_prd["definition_of_done"]}
    if {query["criterion_id"] for query in query_doc["queries"]} != set(approved_criteria):
        raise ValueError("DoD queries do not cover every approved PRD criterion")
    for query in query_doc["queries"]:
        criterion = approved_criteria.get(query["criterion_id"])
        if criterion is None or (query["target"], query["operator"]) != (
            criterion["target"],
            criterion["operator"],
        ):
            raise ValueError("DoD query differs from approved PRD criterion")
    for artifact in (objective_doc, ontology_doc, query_doc, global_prd):
        if validate_generated_by(artifact["generated_by"])["backend"] != generated_by["backend"]:
            raise ValueError("case lineage artifact inference backend differs from run")
    if query_doc.get("prd_path", prd_path) != prd_path:
        raise ValueError("DoD query PRD path differs from approved objectives")
    brief_path = global_prd["brief_path"]
    brief_file = (case_dir.resolve() / brief_path).resolve()
    if not brief_file.is_relative_to(case_dir.resolve()) or not brief_file.is_file():
        raise ValueError(f"missing or unsafe brief lineage artifact: {brief_path}")
    objectives = {item["id"]: item for item in objective_doc["objectives"]}
    tdd_cache: dict[str, tuple[dict, dict]] = {}
    for entity in entities:
        for property_id, value in entity["properties"].items():
            if value["status"] == "missing":
                continue
            value_id = value["value_id"]
            steps = by_value.get(value_id, [])
            if not steps:
                raise ValueError(f"untraceable value {value_id} on {entity['id']}.{property_id}")
            for evidence in value["evidence"]:
                matching = [
                    step
                    for step in steps
                    if step["phase"] == 5
                    and step["source_id"] == evidence["source_id"]
                    and step["objective_id"]
                    and step["tdd_path"]
                ]
                if not matching:
                    raise ValueError(
                        f"value {value_id} lacks source/objective/TDD lineage "
                        f"for {evidence['source_id']}"
                    )
                for step in matching:
                    tdd_path = step["tdd_path"]
                    expected_path = f"04-local/{step['source_id']}__{step['objective_id']}/tdd.json"
                    if tdd_path != expected_path:
                        raise ValueError(f"unexpected TDD lineage path: {tdd_path}")
                    if tdd_path not in tdd_cache:
                        tdd = _read_case_json(case_dir, tdd_path, validators["tdd"])
                        local_prd = _read_case_json(
                            case_dir, tdd["local_prd_path"], validators["local-prd"]
                        )
                        for artifact in (tdd, local_prd):
                            if (
                                validate_generated_by(artifact["generated_by"])["backend"]
                                != generated_by["backend"]
                            ):
                                raise ValueError(
                                    "case lineage artifact inference backend differs from run"
                                )
                        tdd_cache[tdd_path] = (tdd, local_prd)
                    tdd, local_prd = tdd_cache[tdd_path]
                    objective = objectives.get(step["objective_id"])
                    if objective is None:
                        raise ValueError(f"unknown lineage objective: {step['objective_id']}")
                    if (
                        objective["source_id"] != step["source_id"]
                        or tdd["source_id"] != step["source_id"]
                        or local_prd["source_id"] != step["source_id"]
                        or tdd["objective_id"] != step["objective_id"]
                        or local_prd["objective_id"] != step["objective_id"]
                        or tdd["ontology_version"] != objective_doc["ontology_version"]
                        or tdd["source_url"] != objective["source_url"]
                        or local_prd["global_prd_path"] != prd_path
                        or tdd["local_prd_path"]
                        != f"04-local/{step['source_id']}__{step['objective_id']}/local-prd.json"
                    ):
                        raise ValueError(f"broken case lineage for value {value_id}")
                    if any(
                        property_id not in artifact["target_fields"]
                        for artifact in (objective, tdd, local_prd)
                    ):
                        raise ValueError(
                            f"property {property_id} is outside its approved objective/TDD"
                        )
                    requirement_ids = {item["id"] for item in global_prd["requirements"]}
                    if not set(local_prd["global_requirement_ids"]).issubset(requirement_ids):
                        raise ValueError(f"local PRD has unknown global requirement for {value_id}")
                    hostname = urlparse(evidence["url"]).hostname or ""
                    if not any(
                        hostname == domain or hostname.endswith(f".{domain}")
                        for domain in tdd["allowed_domains"]
                    ):
                        raise ValueError(f"evidence URL is outside TDD domains for {value_id}")


def _validate_entities(entities: Sequence[dict], ontology: dict, lake: GoldLake) -> None:
    classes, properties = _ontology_declarations(ontology)
    relations, rules = _signal_definitions(ontology, classes, properties)
    by_id = {entity["id"]: entity for entity in entities}
    source_classes = {item["id"] for item in ontology.get("source_classes", [])}
    if len(by_id) != len(entities):
        raise ValueError("duplicate entity ID")
    for entity in entities:
        entity_class = entity["class"]
        if entity_class not in classes or not entity["id"].startswith(f"{entity_class}:"):
            raise ValueError("entity ID/class is absent from ontology")
        expected = {key for key, prop in properties.items() if prop["domain"] == entity_class}
        if set(entity["properties"]) != expected:
            raise ValueError("entity properties do not match ontology class domain")
        value_ids = set()
        for property_id, value in entity["properties"].items():
            if value["status"] != "missing":
                _datatype(value["value"], properties[property_id]["datatype"])
                if value["value_id"] != stable_value_id(entity["id"], property_id, value["value"]):
                    raise ValueError("gold value_id does not match typed value")
                value_ids.add(value["value_id"])
            _check_evidence(lake, value["evidence"])
            if source_classes and any(
                item["source_type"] not in source_classes for item in value["evidence"]
            ):
                raise ValueError("evidence source class is absent from ontology")
        link_keys = {
            (link["property"], link["target"], link["via_value_id"]) for link in entity["links"]
        }
        if len(link_keys) != len(entity["links"]):
            raise ValueError("duplicate gold relation link")
        for link in entity["links"]:
            relation = relations.get(link["property"])
            if (
                relation is None
                or relation["domain"] != entity_class
                or link["target"] not in by_id
                or link["via_value_id"] not in value_ids
            ):
                raise ValueError("entity link is not backed by an ontology relation and value")
            target_class = by_id[link["target"]]["class"]
            if target_class != relation["range"]:
                raise ValueError("entity link target class differs from ontology relation")
            if relation.get("match") is not None:
                match = relation["match"]
                source_field = entity["properties"][match["domain_property"]]
                target_field = by_id[link["target"]]["properties"][match["range_property"]]
                if (
                    source_field["status"] != "gold"
                    or not source_field["evidence"]
                    or source_field["value_id"] != link["via_value_id"]
                    or target_field["status"] != "gold"
                    or not target_field["evidence"]
                    or source_field["value"] != target_field["value"]
                ):
                    raise ValueError("entity link does not satisfy its typed relation match")
        for flag in entity["flags"]:
            rule = rules.get(flag["rule_id"])
            if (
                rule is None
                or rule.get("predicate") is None
                or not set(flag["evidence_value_ids"]).issubset(value_ids)
            ):
                raise ValueError("entity flag lacks ontology rule or evidence value")
    for relation in relations.values():
        if relation.get("match") is None:
            continue
        expected = _relation_pairs(entities, relation)
        actual = {
            (entity["id"], link["target"], link["via_value_id"])
            for entity in entities
            for link in entity["links"]
            if link["property"] == relation["id"]
        }
        if actual != expected:
            raise ValueError("gold links do not match the ontology's typed relation predicate")
    for entity in entities:
        actual_flags = {
            (flag["rule_id"], flag["label"], tuple(flag["evidence_value_ids"]))
            for flag in entity["flags"]
        }
        expected_flags = set()
        for rule in rules.values():
            if rule.get("predicate") is None:
                continue
            evidence_value_ids = _evaluate_rule(entity, rule, properties)
            if evidence_value_ids is not None:
                expected_flags.add((rule["id"], rule["label"], tuple(evidence_value_ids)))
        if actual_flags != expected_flags or len(actual_flags) != len(entity["flags"]):
            raise ValueError("gold flags do not match evidence-backed typed rule predicates")


def _matches(entity: dict, query: dict) -> bool:
    class_id = query.get("class_id", query.get("class"))
    if class_id and entity["class"] != class_id:
        return False
    for condition in query.get("conditions", []):
        prop = entity["properties"].get(condition["property"])
        if prop is None:
            return False
        present = prop["status"] == "gold" and bool(prop["evidence"])
        if not present:
            return False
        if condition["operator"] == "eq" and _canonical(prop["value"]) != _canonical(
            condition["value"]
        ):
            return False
        if condition["operator"] == "ne" and _canonical(prop["value"]) == _canonical(
            condition["value"]
        ):
            return False
    return True


def _query_actual(entities: Sequence[dict], query: dict) -> int:
    selected = [entity for entity in entities if _matches(entity, query)]
    aggregate = query["aggregate"]
    property_ids = query.get("properties", [])
    if aggregate == "count_entities":
        return len(selected)
    if aggregate == "count_entities_with_relation":
        relation_id = query["relation_id"]
        return sum(
            any(link["property"] == relation_id for link in entity.get("links", []))
            for entity in selected
        )
    if aggregate == "count_entities_with_properties":
        return sum(
            all(
                (field := entity["properties"].get(property_id, {})).get("status") == "gold"
                and bool(field.get("evidence"))
                for property_id in property_ids
            )
            for entity in selected
        )
    if aggregate == "entities_meeting_completeness":
        dod_properties = query.get("_dod_properties")
        if dod_properties is None:
            raise ValueError("completeness query needs ontology DoD properties")
        if not dod_properties:
            return 0
        return sum(
            sum(
                (field := entity["properties"].get(property_id, {})).get("status") == "gold"
                and bool(field.get("evidence"))
                for property_id in dod_properties
            )
            / len(dod_properties)
            >= query["min_ratio"]
            for entity in selected
        )
    fields = [
        value
        for entity in selected
        for property_id, value in entity["properties"].items()
        if (property_ids == "dod" or not property_ids or property_id in property_ids)
        and value["status"] == "gold"
    ]
    if aggregate == "count_distinct_source_classes":
        return len({ev["source_type"] for value in fields for ev in value["evidence"]})
    if aggregate == "count_values_without_evidence":
        return sum(not value["evidence"] for value in fields)
    raise ValueError(f"unsupported DoD aggregate: {aggregate}")


def _compare(actual: int, target: float, operator: str) -> bool:
    operations = {
        ">=": actual >= target,
        ">": actual > target,
        "<=": actual <= target,
        "<": actual < target,
        "==": actual == target,
        "=": actual == target,
        "!=": actual != target,
    }
    if operator not in operations:
        raise ValueError(f"unsupported DoD operator: {operator}")
    return operations[operator]


def _metrics(
    run_id: str,
    entities: Sequence[dict],
    ontology: dict,
    dod_queries: dict,
    trace: Sequence[dict],
    taxonomy_levels: Mapping[str, Sequence[Sequence[str]]],
    jobs: dict | None,
    generated_by: dict[str, str],
    preview: bool,
    decisions_by_backend: dict[str, int] | None,
    coverage_basis: str = "none",
) -> dict:
    classes, properties = _ontology_declarations(ontology)
    relations, _ = _signal_definitions(ontology, classes, properties)
    by_class = {
        class_id: [entity for entity in entities if entity["class"] == class_id]
        for class_id in classes
    }
    entities_total = {class_id: len(group) for class_id, group in by_class.items()}
    entities_meeting_dod = {}
    per_property_completeness = {}
    thresholds = {
        query["class"]: query["min_ratio"]
        for query in dod_queries["queries"]
        if query["aggregate"] == "entities_meeting_completeness"
    }
    for class_id, group in by_class.items():
        own = {key: prop for key, prop in properties.items() if prop["domain"] == class_id}
        dod_properties = [key for key, prop in own.items() if prop.get("dod")]
        min_ratio = thresholds.get(class_id, 0.8)
        entities_meeting_dod[class_id] = sum(
            bool(dod_properties)
            and sum(
                (field := entity["properties"].get(key, {})).get("status") == "gold"
                and bool(field.get("evidence"))
                for key in dod_properties
            )
            / len(dod_properties)
            >= min_ratio
            for entity in group
        )
        per_property_completeness[class_id] = {
            key: (
                sum(entity["properties"][key]["status"] == "gold" for entity in group) / len(group)
                if group
                else 0.0
            )
            for key in own
        }
    gold_values = [
        value
        for entity in entities
        for value in entity["properties"].values()
        if value["status"] == "gold"
    ]
    covered = {node for entity in entities for node in entity["classified_as"]}
    classified = coverage_basis == "classification"
    level_ratio = {
        taxonomy: [
            (len(covered.intersection(level)) / len(set(level)) if level else 0.0)
            if classified
            else None
            for level in levels
        ]
        for taxonomy, levels in taxonomy_levels.items()
    }
    taxonomy_metrics = []
    for taxonomy, levels in taxonomy_levels.items():
        nodes = {node for level in levels for node in level}
        soundness = next(
            (
                item.get("soundness")
                for item in ontology.get("taxonomies", [])
                if item["factor_id"] == taxonomy
            ),
            None,
        )
        taxonomy_metrics.append(
            {
                "factor_id": taxonomy,
                "nodes": len(nodes),
                "soundness": soundness,
                "soundness_basis": "critic",
                "coverage": len(covered.intersection(nodes)) / len(nodes)
                if classified and nodes
                else None,
                "coverage_basis": coverage_basis,
            }
        )
    mode_count = Counter(step["mode"] for step in trace if step.get("event") != "loop")
    loops = [
        {
            "phase": step["loop"]["phase"],
            "iterations": step["loop"]["iteration"],
            "stop_reason": step["loop"]["stop_reason"],
            "usd": step["evaluated"]["usd"],
        }
        for step in trace
        if step.get("event") == "loop"
        and step.get("loop", {}).get("role") == "decide"
        and "stop_reason" in step["loop"]
    ]
    completed_jobs = {
        step["tdd_path"]
        for step in trace
        if step["phase"] == 5
        and step["tdd_path"]
        and isinstance(step["evaluated"], dict)
        and step["evaluated"].get("status") == "ok"
    }
    dod = []
    criterion_ids = set()
    for query in dod_queries["queries"]:
        criterion_id = query["criterion_id"]
        if criterion_id in criterion_ids:
            raise ValueError(f"duplicate DoD criterion ID: {criterion_id}")
        criterion_ids.add(criterion_id)
        class_id = query.get("class_id", query.get("class"))
        if class_id and class_id not in classes:
            raise ValueError(f"DoD query has unknown class: {class_id}")
        if (
            query["aggregate"] == "entities_meeting_completeness"
            and class_id != ontology["primary_class"]
        ):
            raise ValueError("completeness query must use the primary class")
        listed = query.get("properties", [])
        listed = [] if listed == "dod" else listed
        validation_class_id = class_id
        if query["aggregate"] == "count_entities_with_relation":
            relation = relations.get(query["relation_id"])
            if relation is None:
                raise ValueError(f"DoD query has unknown relation: {query['relation_id']}")
            if class_id is not None and class_id != relation["domain"]:
                raise ValueError("relation count class must equal the relation domain")
            validation_class_id = relation["domain"]
        for property_id in [
            *listed,
            *(c["property"] for c in query.get("conditions", [])),
        ]:
            if property_id not in properties:
                raise ValueError(f"DoD query has unknown property: {property_id}")
            if validation_class_id and properties[property_id]["domain"] != validation_class_id:
                raise ValueError(f"DoD property {property_id} is outside query class")
        evaluation_query = query
        if query["aggregate"] == "entities_meeting_completeness":
            evaluation_query = {
                **query,
                "_dod_properties": [
                    key
                    for key, prop in properties.items()
                    if prop["domain"] == class_id and prop.get("dod")
                ],
            }
        actual = _query_actual(entities, evaluation_query)
        readable = query["aggregate"]
        arguments = []
        for key in ("class_id", "class", "relation_id", "properties", "min_ratio", "conditions"):
            if query.get(key):
                arguments.append(f"{key}={_canonical(query[key])}")
        if arguments:
            readable += f"({', '.join(arguments)})"
        dod.append(
            {
                "criterion_id": criterion_id,
                "query": readable,
                "target": query["target"],
                "actual": actual,
                "met": generated_by["backend"] == "vultr"
                and not preview
                and _compare(actual, query["target"], query["operator"]),
            }
        )
    metrics = {
        "run_id": run_id,
        "entities_total": entities_total,
        "entities_meeting_dod": entities_meeting_dod,
        "per_property_completeness": per_property_completeness,
        "distinct_source_classes": len(
            {ev["source_type"] for value in gold_values for ev in value["evidence"]}
        ),
        "values_without_evidence": sum(not value["evidence"] for value in gold_values),
        "level_ratio_coverage": level_ratio,
        "taxonomy_metrics": taxonomy_metrics,
        "mode_counts": {mode: mode_count[mode] for mode in ("D0", "D1", "S1", "S2")},
        "jobs": jobs or {"ok": len(completed_jobs), "failed_by_reason": {}},
        "generated_by": generated_by.copy(),
        "inference_backend": generated_by["backend"],
        "preview": preview,
        "decisions_by_backend": decisions_by_backend or {},
        "loops": loops,
        "dod": dod,
    }
    return metrics


def export_run(
    lake: GoldLake,
    case_dir: Path,
    case_id: str,
    run_id: str,
    entities: Sequence[dict],
    *,
    ontology: dict,
    dod_queries: dict,
    trace: Sequence[dict] = (),
    generated_by: dict[str, str],
    preview: bool = False,
    decisions_by_backend: dict[str, int] | None = None,
    taxonomy_levels: Mapping[str, Sequence[Sequence[str]]] | None = None,
    taxonomy_classified: bool = False,
    jobs: dict | None = None,
) -> dict:
    """Write schema-valid generic gold, then move the latest pointer last."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", case_id) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*", run_id
    ):
        raise ValueError("case_id and run_id must be safe lake path segments")
    run_provenance = validate_run_provenance(run_id, generated_by)
    validators = _validators()
    validators["ontology"].validate(ontology)
    validators["dod-queries"].validate(dod_queries)
    sorted_entities = sorted(entities, key=lambda item: item["id"])
    sorted_trace = sorted(trace, key=lambda item: (item["ts"], item["step_id"]))
    for entity in sorted_entities:
        validators["entity"].validate(entity)
        for record in (entity, *entity["properties"].values()):
            if (
                validate_generated_by(record["generated_by"])["backend"]
                != run_provenance["backend"]
            ):
                raise ValueError("gold entity/value inference backend differs from run")
    for step in sorted_trace:
        validators["trace-step"].validate(step)
        if step["run_id"] != run_id:
            raise ValueError("trace contains another run_id")
        step_backend = validate_generated_by(step["generated_by"])["backend"]
        if step_backend != run_provenance["backend"] and not (
            run_provenance["backend"] == "vultr" and step_backend == "jev"
        ):
            raise ValueError("trace inference backend differs from run")
        if step_backend == "jev" and step["value_ids"]:
            raise ValueError("Jev trace cannot ground gold values")
    _validate_entities(sorted_entities, ontology, lake)
    _check_lineage(
        case_dir, sorted_entities, sorted_trace, ontology, dod_queries, validators, run_provenance
    )
    metrics = _metrics(
        run_id,
        sorted_entities,
        ontology,
        dod_queries,
        sorted_trace,
        taxonomy_levels if taxonomy_levels is not None else _taxonomy_levels(ontology),
        jobs,
        run_provenance,
        preview,
        decisions_by_backend,
        coverage_basis="classification" if taxonomy_classified else "none",
    )
    validators["metrics"].validate(metrics)
    prefix = f"gold/{case_id}/{run_id}"
    lake.write_key(f"{prefix}/entities.jsonl", _jsonl_bytes(sorted_entities))
    lake.write_key(f"{prefix}/ontology.json", _json_bytes(ontology))
    lake.write_key(f"{prefix}/trace.jsonl", _jsonl_bytes(sorted_trace))
    metrics_bytes = _json_bytes(metrics)
    lake.write_key(f"{prefix}/metrics.json", metrics_bytes)
    runs_dir = Path(case_dir) / "runs"
    metric_targets = [runs_dir / run_id / "metrics.json"]
    if run_provenance["backend"] == "vultr" and not preview:
        metric_targets.append(runs_dir / "latest" / "metrics.json")
    for target in metric_targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_bytes(metrics_bytes)
        temporary.replace(target)
    if run_provenance["backend"] == "vultr" and not preview:
        lake.write_key(f"gold/{case_id}/latest.json", _json_bytes({"run_id": run_id}))
    return metrics
