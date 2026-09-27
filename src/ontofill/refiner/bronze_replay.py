"""Rebuild ontology values from trace-referenced bronze files without browsing."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from ontofill_scrape.models import ParsedFile, ParsedRow

from ontofill.lake import FileLake, S3Lake
from ontofill.refiner.core import Observation
from ontofill.refiner.provenance import observation_source_metadata
from ontofill.sandbox.parse import ParseExecutor, SandboxParseError, parse_bronze
from ontofill.sandbox.profile_tables import profile_to_parsed_file

_BRONZE_KEY = re.compile(r"sha256:[0-9a-f]{64}\Z")


@dataclass
class BronzeReplayResult:
    observations: list[Observation]
    trace_steps: list[dict]
    parse_trace_steps: list[dict] = field(default_factory=list)
    parse_job_records: list[dict] = field(default_factory=list)


def _read_case_json(case_dir: Path, relative_path: str) -> dict | None:
    root = case_dir.resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _safe_trace_context(step: dict, run_id: str) -> tuple[str, str, str] | None:
    source_id = step.get("source_id")
    objective_id = step.get("objective_id")
    tdd_path = step.get("tdd_path")
    if step.get("run_id") != run_id or step.get("phase") != 5:
        return None
    if not all(isinstance(value, str) and value for value in (source_id, objective_id, tdd_path)):
        return None
    expected_tdd = f"04-local/{source_id}__{objective_id}/tdd.json"
    if tdd_path != expected_tdd:
        return None
    return source_id, objective_id, tdd_path


def _trace_url(step: dict) -> str | None:
    observed = step.get("observed")
    url = observed.get("url") if isinstance(observed, dict) else None
    if not isinstance(url, str):
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return url


def _metadata_matches_trace(metadata: dict, step: dict) -> bool:
    if not isinstance(metadata, dict):
        return False
    observed = step.get("observed")
    if not isinstance(observed, dict):
        return False
    expected = {
        "url": observed.get("url"),
        "source_id": step.get("source_id"),
        "step_id": step.get("step_id"),
        "captured_at": step.get("ts"),
    }
    return all(
        isinstance(value, str) and value and metadata.get(key) == value
        for key, value in expected.items()
    )


def _replay_safe(trace: Sequence[dict], index: int, source_id: str, objective_id: str) -> bool:
    for prior in trace[:index]:
        if (prior.get("source_id"), prior.get("objective_id")) != (source_id, objective_id):
            continue
        if prior.get("event") in {"quarantine", "hard_stop", "limit_kill"}:
            return False
        if prior.get("event") == "action_gate":
            gate = prior.get("gate") or {}
            if gate.get("outcome") != "allowed":
                return False
    return True


def _screenshot_key(
    trace: Sequence[dict],
    index: int,
    source_id: str,
    objective_id: str,
    tdd_path: str,
    lake: FileLake | S3Lake,
) -> str | None:
    for step in reversed(trace[:index]):
        if (
            step.get("source_id"),
            step.get("objective_id"),
            step.get("tdd_path"),
        ) != (source_id, objective_id, tdd_path):
            continue
        executed = step.get("executed") or {}
        key = step.get("screenshot_key") or executed.get("screenshot_key")
        observed = step.get("observed")
        evaluated = step.get("evaluated")
        if (
            step.get("run_id") != trace[index].get("run_id")
            or step.get("phase") != 5
            or not isinstance(observed, dict)
            or observed.get("status") != 200
            or not isinstance(evaluated, dict)
            or evaluated.get("status") != "captured"
            or not isinstance(key, str)
            or not _BRONZE_KEY.fullmatch(key)
            or not lake.exists(key)
        ):
            continue
        try:
            metadata = lake.read_metadata(key)
        except (OSError, ValueError, KeyError):
            continue
        if _metadata_matches_trace(metadata, step):
            return key
    return None


def _file_format(content_type: str, url: str) -> str | None:
    mime = content_type.split(";", 1)[0].strip().casefold()
    if mime in {"text/html", "application/xhtml+xml"}:
        return None
    if mime == "application/pdf":
        return "pdf"
    if mime == "text/csv" or mime == "application/csv":
        return "csv"
    if mime in {
        "application/json",
        "text/json",
        "application/ld+json",
        "application/vnd.api+json",
    }:
        return "json"
    if mime in {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel.sheet.macroenabled.12",
    }:
        return "xlsx"
    suffix = urlsplit(url).path.rsplit(".", 1)[-1].casefold()
    return {
        "csv": "csv",
        "json": "json",
        "xlsx": "xlsx",
        "xlsm": "xlsm",
        "pdf": "pdf",
    }.get(suffix)


def _table(parsed: ParsedFile) -> tuple[str | None, tuple[str, ...], list[ParsedRow]] | None:
    grouped: dict[str | None, list[ParsedRow]] = defaultdict(list)
    for row in parsed.rows:
        grouped[row.sheet].append(row)
    choices: list[tuple[str | None, tuple[str, ...], list[ParsedRow]]] = []
    for sheet, rows in grouped.items():
        if len(rows) < 2:
            continue
        headers = tuple(str(value or "").strip() for value in rows[0].values)
        if any(headers):
            choices.append((sheet, headers, rows[1:]))
    return max(choices, key=lambda item: len(item[2]), default=None)


def _label_key(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def _coerce(value: object, datatype: str) -> str | int | float | bool | None:
    if value is None or value == "":
        return None
    kind = datatype.rsplit("#", 1)[-1].rsplit("/", 1)[-1].rsplit(":", 1)[-1].casefold()
    if kind in {"boolean", "bool"}:
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().casefold()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
        raise ValueError("value is not a boolean")
    if kind in {"integer", "int", "long", "nonnegativeinteger"}:
        return int(value)
    if kind in {"number", "decimal", "float", "double"}:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("number must be finite")
        return number
    if kind == "date":
        return date.fromisoformat(str(value).strip()).isoformat()
    if kind in {"datetime", "datetime-stamp", "dateTime".casefold()}:
        return datetime.fromisoformat(str(value).strip()).isoformat()
    return str(value).strip()


def _column_properties(
    macro: dict,
    headers: tuple[str, ...],
    ontology: dict,
    class_id: str,
    allowed_properties: set[str],
) -> list[tuple[str, str, int]] | None:
    properties = {
        item["id"]: item
        for item in ontology.get("properties", [])
        if item.get("domain") == class_id and item.get("id") in allowed_properties
    }
    if not isinstance(macro.get("columns"), list):
        return None
    mapped: list[tuple[str, str, int]] = []
    mapped_headers: set[str] = set()
    mapped_properties: set[str] = set()
    reserved_headers: set[str] = set()
    for column in macro["columns"]:
        if not isinstance(column, dict):
            return None
        header = column.get("header")
        property_id = column.get("property_id")
        if isinstance(header, str):
            reserved_headers.add(header)
        if header not in headers or property_id not in properties:
            continue
        if header in mapped_headers or property_id in mapped_properties:
            return None
        mapped.append((property_id, header, headers.index(header)))
        mapped_headers.add(header)
        mapped_properties.add(property_id)

    headers_by_key: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for index, header in enumerate(headers):
        if header and header not in reserved_headers:
            headers_by_key[_label_key(header)].append((header, index))
    properties_by_key: dict[str, set[str]] = defaultdict(set)
    for property_id, property_item in properties.items():
        if property_id in mapped_properties:
            continue
        for alias in {property_id, property_item.get("label", "")}:
            key = _label_key(alias)
            if key:
                properties_by_key[key].add(property_id)
    for key, property_ids in properties_by_key.items():
        header_matches = headers_by_key.get(key, [])
        if len(property_ids) != 1 or len(header_matches) != 1:
            continue
        property_id = next(iter(property_ids))
        header, index = header_matches[0]
        if header in mapped_headers:
            continue
        mapped.append((property_id, header, index))
        mapped_headers.add(header)
    return mapped


def _entity_id(class_id: str, identifier: object) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", str(identifier)).casefold().split())
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:24]
    return f"{class_id}:{digest}"


def _replay_file(
    *,
    case_dir: Path,
    lake: FileLake | S3Lake,
    run_id: str,
    trace: Sequence[dict],
    index: int,
    step: dict,
    ontology: dict,
    provenance: dict,
    objectives: dict[tuple[str, str], dict],
    historical_ontology_version: str,
    parse_executor: ParseExecutor | None,
    parse_trace_steps: list[dict],
    parse_job_records: list[dict],
) -> tuple[list[Observation], dict] | None:
    context = _safe_trace_context(step, run_id)
    if context is None:
        return None
    source_id, objective_id, tdd_path = context
    if not _replay_safe(trace, index, source_id, objective_id):
        return None
    observed = step.get("observed") or {}
    requested = step.get("requested") or {}
    evaluated = step.get("evaluated") or {}
    executed = step.get("executed") or {}
    key = executed.get("bronze_key")
    if (
        requested.get("fetch") != "bytes"
        or observed.get("status") != 200
        or evaluated.get("status") != "captured"
        or not isinstance(key, str)
        or not _BRONZE_KEY.fullmatch(key)
        or not lake.exists(key)
    ):
        return None
    file_url = _trace_url(step)
    if file_url is None:
        return None
    try:
        metadata = lake.read_metadata(key)
    except (OSError, ValueError, KeyError):
        return None
    if not _metadata_matches_trace(metadata, step):
        return None
    format_name = _file_format(str(metadata.get("content_type", "")), file_url)
    if format_name is None:
        return None
    try:
        parse_result = parse_bronze(
            lake,
            key,
            format=format_name,
            max_rows=500 if format_name == "pdf" else 10_000,
            base_url=file_url,
            run_id=run_id,
            source_id=source_id,
            objective_id=objective_id,
            tdd_path=tdd_path,
            generated_by=provenance,
            executor=parse_executor,
        )
    except SandboxParseError as exc:
        parse_trace_steps.extend(exc.trace)
        parse_job_records.append(exc.job_record)
        return None
    parse_trace_steps.extend(parse_result.trace)
    parse_job_records.append(parse_result.job_record)
    parsed = parse_result.as_parsed_file()
    pdf_pages: dict[tuple[str, int], int] = {}
    if parsed.truncated:
        return None
    if format_name == "pdf":
        adapted = profile_to_parsed_file(parse_result)
        if adapted is None:
            return None
        parsed, pdf_pages = adapted
    table = _table(parsed)
    if table is None:
        return None
    sheet, headers, rows = table

    macro_step = next(
        (
            candidate
            for candidate in trace[index + 1 :]
            if candidate.get("parent_step_id") == step.get("step_id")
            and candidate.get("requested", {}).get("tool") == "column.map"
            and candidate.get("source_id") == source_id
            and candidate.get("objective_id") == objective_id
        ),
        None,
    )
    if macro_step is None:
        return None
    macro_path = (macro_step.get("executed") or {}).get("macro_path")
    if not isinstance(macro_path, str) or not macro_path:
        return None
    macro = _read_case_json(case_dir, macro_path)
    if macro is None or macro.get("source_id") != source_id:
        return None
    class_id = macro.get("class_id")
    classes = {item.get("id"): item for item in ontology.get("classes", [])}
    entity_class = classes.get(class_id)
    if entity_class is None:
        return None

    objective = objectives.get((source_id, objective_id))
    if objective is None or not isinstance(objective.get("source_type"), str):
        return None
    tdd = _read_case_json(case_dir, tdd_path)
    if (
        tdd is None
        or tdd.get("source_id") != source_id
        or tdd.get("objective_id") != objective_id
        or tdd.get("ontology_version") != historical_ontology_version
        or not isinstance(tdd.get("local_prd_path"), str)
        or tdd.get("local_prd_path") != f"04-local/{source_id}__{objective_id}/local-prd.json"
    ):
        return None
    local_prd = _read_case_json(case_dir, tdd["local_prd_path"])
    if (
        local_prd is None
        or local_prd.get("source_id") != source_id
        or local_prd.get("objective_id") != objective_id
        or local_prd.get("global_prd_path") != "01-scope/prd.json"
        or not isinstance(objective.get("target_fields"), list)
        or not isinstance(tdd.get("target_fields"), list)
        or not isinstance(local_prd.get("target_fields"), list)
        or any(
            not isinstance(item, str)
            for fields in (
                objective.get("target_fields", []),
                tdd.get("target_fields", []),
                local_prd.get("target_fields", []),
            )
            for item in fields
        )
    ):
        return None
    historical_targets = (
        set(objective["target_fields"])
        | set(tdd["target_fields"])
        | set(local_prd["target_fields"])
    )
    allowed_properties = (
        set(objective["target_fields"])
        & set(tdd["target_fields"])
        & set(local_prd["target_fields"])
    )
    ontology_only_properties = {
        item["id"]
        for item in ontology.get("properties", [])
        if item.get("domain") == class_id and item.get("id") not in historical_targets
    }
    if historical_ontology_version != ontology.get("version"):
        allowed_properties.update(ontology_only_properties)
    columns = _column_properties(macro, headers, ontology, class_id, allowed_properties)
    if not columns:
        return None
    properties = {item["id"]: item for item in ontology.get("properties", [])}
    identifier_property = entity_class.get("identifier_property")
    title_property = entity_class.get("title_property")
    if not {identifier_property, title_property} & {item[0] for item in columns}:
        return None
    screenshot = _screenshot_key(trace, index, source_id, objective_id, tdd_path, lake)
    if screenshot is None:
        return None
    captured_at = step.get("ts")
    try:
        datetime.fromisoformat(captured_at)
    except (TypeError, ValueError):
        return None

    planned: list[tuple[str, str, object, str]] = []
    for row in rows:
        values: dict[str, tuple[object, str]] = {}
        for property_id, header, column_index in columns:
            if column_index >= len(row.values):
                continue
            try:
                value = _coerce(row.values[column_index], properties[property_id]["datatype"])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if value is not None:
                values[property_id] = (value, header)
        identity = values.get(identifier_property) or values.get(title_property)
        if identity is None or not str(identity[0]).strip():
            continue
        entity_id = _entity_id(class_id, identity[0])
        for property_id, (value, header) in values.items():
            pdf_page = pdf_pages.get((str(sheet), row.row_number)) if sheet else None
            selector = (
                f"{sheet}:page={pdf_page}:row={row.row_number}:{header}"
                if pdf_page is not None
                else f"{sheet or 'table'}:{row.row_number}:{header}"
            )
            planned.append((entity_id, property_id, value, selector))
    if not planned:
        return None

    mapping = sorted({(property_id, header) for property_id, header, _ in columns})
    signature = {
        "run_id": run_id,
        "capture_step_id": step["step_id"],
        "bronze_key": key,
        "ontology_version": ontology.get("version"),
        "mapping": mapping,
        "values": planned,
    }
    replay_digest = hashlib.sha256(
        json.dumps(signature, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()[:32]
    replay_step_id = f"step:bronze-replay-{replay_digest}"
    observations: list[Observation] = []
    value_ids: set[str] = set()
    for entity_id, property_id, value, selector in planned:
        evidence = {
            "url": file_url,
            "bronze_key": key,
            "selector": selector,
            "screenshot_key": screenshot,
            "step_id": replay_step_id,
            "captured_at": captured_at,
            "source_id": source_id,
            "source_type": objective["source_type"],
            "format": parsed.format,
        }
        item = Observation(
            run_id=run_id,
            entity_id=entity_id,
            entity_class=class_id,
            property_id=property_id,
            value=value,
            evidence=evidence,
            step_id=replay_step_id,
            generated_by=provenance,
            **observation_source_metadata(objective),
        )
        observations.append(item)
        value_ids.add(item.value_id)
    replay_step = {
        "step_id": replay_step_id,
        "run_id": run_id,
        "phase": 5,
        "source_id": source_id,
        "objective_id": objective_id,
        "tdd_path": tdd_path,
        "mode": "D0",
        "observed": {
            "bronze_key": key,
            "format": parsed.format,
            "rows": len(rows),
            "replayed_properties": sorted({item[1] for item in planned}),
            "ontology_only_properties": sorted(
                {item[1] for item in planned} & ontology_only_properties
            ),
        },
        "requested": {"tool": "bronze.replay", "ontology_version": ontology.get("version")},
        "executed": {
            "bronze_key": key,
            "capture_ontology_version": historical_ontology_version,
            "observations": len(observations),
        },
        "evaluated": {"status": "replayed"},
        "parent_step_id": step["step_id"],
        "value_ids": sorted(value_ids),
        "ts": datetime.now(UTC).isoformat(),
        "generated_by": provenance,
    }
    return observations, replay_step


def replay_bronze_observations(
    *,
    case_dir: Path,
    lake: FileLake | S3Lake,
    run_id: str,
    trace: Sequence[dict],
    ontology: dict,
    provenance: dict,
    parse_executor: ParseExecutor | None = None,
) -> BronzeReplayResult:
    """Reparse completed file captures already linked from this run's trace."""
    objective_doc = _read_case_json(case_dir, "03-fanout/objectives.json")
    if (
        objective_doc is None
        or not isinstance(objective_doc.get("ontology_version"), str)
        or not isinstance(objective_doc.get("objectives"), list)
    ):
        return BronzeReplayResult([], [])
    objectives = {
        (item.get("source_id"), item.get("id")): item
        for item in objective_doc["objectives"]
        if isinstance(item, dict)
    }
    observations: list[Observation] = []
    trace_steps: list[dict] = []
    parse_trace_steps: list[dict] = []
    parse_job_records: list[dict] = []
    for index, step in enumerate(trace):
        if not isinstance(step, dict):
            continue
        replayed = _replay_file(
            case_dir=case_dir,
            lake=lake,
            run_id=run_id,
            trace=trace,
            index=index,
            step=step,
            ontology=ontology,
            provenance=provenance,
            objectives=objectives,
            historical_ontology_version=objective_doc["ontology_version"],
            parse_executor=parse_executor,
            parse_trace_steps=parse_trace_steps,
            parse_job_records=parse_job_records,
        )
        if replayed is None:
            continue
        items, trace_step = replayed
        observations.extend(items)
        trace_steps.append(trace_step)
    return BronzeReplayResult(observations, trace_steps, parse_trace_steps, parse_job_records)
