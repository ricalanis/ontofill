"""Validated, atomic-pointer gold exports into the shared lake layout."""

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

from ontofill.refiner.core import CORE_FIELDS, SCHEMA_ROOT


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
    suppliers: Sequence[dict],
    trace: Sequence[dict],
    validators: dict[str, Draft202012Validator],
) -> None:
    by_value: dict[str, list[dict]] = {}
    for step in trace:
        for value_id in step["value_ids"]:
            by_value.setdefault(value_id, []).append(step)
    objective_doc = _read_case_json(case_dir, "03-fanout/objectives.json", validators["objectives"])
    ontology_doc = _read_case_json(case_dir, "02-ontology/ontology.json", None)
    if objective_doc["ontology_version"] != ontology_doc.get("version"):
        raise ValueError("objectives ontology version does not match ontology artifact")
    prd_path = objective_doc["prd_path"]
    global_prd = _read_case_json(case_dir, prd_path, validators["global-prd"])
    brief_path = global_prd["brief_path"]
    brief_file = (case_dir.resolve() / brief_path).resolve()
    if not brief_file.is_relative_to(case_dir.resolve()) or not brief_file.is_file():
        raise ValueError(f"missing or unsafe brief lineage artifact: {brief_path}")
    objectives = {item["id"]: item for item in objective_doc["objectives"]}
    tdd_cache: dict[str, tuple[dict, dict]] = {}
    for supplier in suppliers:
        for field_name, field in supplier["fields"].items():
            if field["status"] == "missing":
                continue
            value_id = field["value_id"]
            steps = by_value.get(value_id, [])
            if not steps:
                raise ValueError(f"untraceable value {value_id} on {supplier['id']}.{field_name}")
            for evidence in field["evidence"]:
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
                        field_name not in artifact["target_fields"]
                        for artifact in (objective, tdd, local_prd)
                    ):
                        raise ValueError(
                            f"field {field_name} is outside its approved objective/TDD"
                        )
                    requirement_ids = {item["id"] for item in global_prd["requirements"]}
                    if not set(local_prd["global_requirement_ids"]).issubset(requirement_ids):
                        raise ValueError(f"local PRD has unknown global requirement for {value_id}")
                    if urlparse(evidence["url"]).hostname not in tdd["allowed_domains"]:
                        raise ValueError(f"evidence URL is outside TDD domains for {value_id}")


def _metrics(
    run_id: str,
    suppliers: Sequence[dict],
    trace: Sequence[dict],
    taxonomy_levels: Mapping[str, Sequence[Sequence[str]]],
    jobs: dict | None,
) -> dict:
    total = len(suppliers)
    per_field = {
        name: (
            sum(supplier["fields"][name]["status"] == "gold" for supplier in suppliers) / total
            if total
            else 0.0
        )
        for name in CORE_FIELDS
    }
    source_types = {
        evidence["source_type"]
        for supplier in suppliers
        for field in supplier["fields"].values()
        if field["status"] == "gold"
        for evidence in field["evidence"]
    }
    covered = {node for supplier in suppliers for node in supplier["classified_as"]}
    level_ratio = {
        taxonomy: [
            len(covered.intersection(level)) / len(set(level)) if level else 0.0 for level in levels
        ]
        for taxonomy, levels in taxonomy_levels.items()
    }
    mode_count = Counter(step["mode"] for step in trace)
    completed_jobs = {
        step["tdd_path"]
        for step in trace
        if step["phase"] == 5
        and step["tdd_path"]
        and isinstance(step["evaluated"], dict)
        and step["evaluated"].get("status") == "ok"
    }
    return {
        "run_id": run_id,
        "suppliers_total": total,
        "suppliers_at_80pct_core": sum(
            sum(supplier["fields"][name]["status"] == "gold" for name in CORE_FIELDS) >= 5
            for supplier in suppliers
        ),
        "per_field_completeness": per_field,
        "distinct_source_types": len(source_types),
        "gold_values_without_evidence": sum(
            not field["evidence"]
            for supplier in suppliers
            for field in supplier["fields"].values()
            if field["status"] == "gold"
        ),
        "level_ratio_coverage": level_ratio,
        "mode_counts": {mode: mode_count[mode] for mode in ("D0", "D1", "S1", "S2")},
        "jobs": jobs or {"ok": len(completed_jobs), "failed_by_reason": {}},
    }


def export_run(
    lake: GoldLake,
    case_dir: Path,
    case_id: str,
    run_id: str,
    suppliers: Sequence[dict],
    *,
    contracts: Sequence[dict] = (),
    trace: Sequence[dict] = (),
    taxonomy_levels: Mapping[str, Sequence[Sequence[str]]] | None = None,
    jobs: dict | None = None,
) -> dict:
    """Write an entire schema-valid run, then move latest.json as the final step."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", case_id) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*", run_id
    ):
        raise ValueError("case_id and run_id must be safe lake path segments")
    validators = _validators()
    sorted_suppliers = sorted(suppliers, key=lambda item: item["id"])
    sorted_contracts = sorted(contracts, key=lambda item: item["id"])
    sorted_trace = sorted(trace, key=lambda item: (item["ts"], item["step_id"]))
    for name, records in (
        ("supplier", sorted_suppliers),
        ("contract", sorted_contracts),
        ("trace-step", sorted_trace),
    ):
        for record in records:
            validators[name].validate(record)
    if any(step["run_id"] != run_id for step in sorted_trace):
        raise ValueError("trace contains another run_id")
    _check_lineage(case_dir, sorted_suppliers, sorted_trace, validators)
    supplier_ids = {supplier["id"] for supplier in sorted_suppliers}
    for supplier in sorted_suppliers:
        for field in supplier["fields"].values():
            _check_evidence(lake, field["evidence"])
    for contract in sorted_contracts:
        if not set(contract["supplier_ids"]).issubset(supplier_ids):
            raise ValueError(f"contract {contract['id']} refers to an unknown supplier")
        _check_evidence(lake, contract["evidence"])
    metrics = _metrics(run_id, sorted_suppliers, sorted_trace, taxonomy_levels or {}, jobs)
    validators["metrics"].validate(metrics)
    prefix = f"gold/{case_id}/{run_id}"
    lake.write_key(f"{prefix}/suppliers.jsonl", _jsonl_bytes(sorted_suppliers))
    lake.write_key(f"{prefix}/contracts.jsonl", _jsonl_bytes(sorted_contracts))
    lake.write_key(f"{prefix}/trace.jsonl", _jsonl_bytes(sorted_trace))
    metrics_bytes = _json_bytes(metrics)
    lake.write_key(f"{prefix}/metrics.json", metrics_bytes)
    runs_dir = Path(case_dir) / "runs"
    for target in (runs_dir / run_id / "metrics.json", runs_dir / "latest" / "metrics.json"):
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_bytes(metrics_bytes)
        temporary.replace(target)
    lake.write_key(f"gold/{case_id}/latest.json", _json_bytes({"run_id": run_id}))
    return metrics
