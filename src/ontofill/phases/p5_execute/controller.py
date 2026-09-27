"""Run an S1 TDD through the browser controller and preserve its capture trail."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from ontofill_scrape import Evidence, emit_observation
from ontofill_scrape import Observation as ToolObservation

from ontofill.browser_agent import (
    BrowserAgentClient,
    BrowserTraceBridge,
    ObservationQuarantined,
    screen_is_cleared,
)
from ontofill.lake import FileLake, S3Lake
from ontofill.lake.storage import BRONZE_KEY
from ontofill.refiner import Observation, SilverStore
from ontofill.runfeed import RunFeed
from ontofill.sandbox import append_job_record, validate_job_record

Coerce = Callable[[object, str], str | int | float | bool | None]
_SENSITIVE_QUERY = re.compile(
    r"(?:^|[-_])(?:token|secret|signature|credential|password|authorization|auth|api[-_]?key|"
    r"access[-_]?key|key|session|jwt)(?:$|[-_])",
    re.IGNORECASE,
)


@dataclass
class ControllerResult:
    observations: list[Observation]
    trace: list[dict]


def _shared_root(argument: Path | None, primary: str, fallback: str) -> Path:
    configured = argument
    if configured is None:
        value = os.getenv(primary) or os.getenv(fallback)
        if not value:
            raise RuntimeError(f"set {primary} to a shared absolute controller directory")
        configured = Path(value).expanduser()
    if not configured.is_absolute():
        raise ValueError(f"{primary} must be an absolute shared directory")
    return configured.resolve()


def _safe_url(value: object, allowed_domains: list[str]) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or any(_SENSITIVE_QUERY.search(name) for name, _ in parse_qsl(parsed.query))
    ):
        return None
    if not any(
        host == domain.lower().lstrip(".") or host.endswith("." + domain.lower().lstrip("."))
        for domain in allowed_domains
    ):
        return None
    return value


def _controller_goal(tdd: dict, ontology: dict) -> str:
    properties = {item["id"]: item for item in ontology["properties"]}
    fields = [
        f"{property_id}: {properties[property_id]['label']} — {properties[property_id]['description']}"
        for property_id in tdd["target_fields"]
        if property_id in properties
    ]
    return (
        "Read this public source using only the allowed domains. Collect one complete record, or as many distinct "
        "records as the page clearly exposes up to the TDD target volume. For each requested ontology property, "
        "call extract with the property ID as its output key and the selector containing its literal value. "
        "Do not infer missing values. Stop if a login wall or captcha appears, and never submit, register, pay, "
        "delete, or send. Requested properties (key: meaning): "
        + "; ".join(fields)
        + f". Approved validation rules: {tdd.get('validation_rules', [])}."
    )


def _rows(extracted: object) -> list[dict]:
    if isinstance(extracted, list):
        return [item for item in extracted if isinstance(item, dict)]
    if isinstance(extracted, dict):
        return [extracted]
    return []


def _read_controller_steps(steps_path: Path) -> list[dict]:
    if not steps_path.exists():
        return []
    steps: list[dict] = []
    for line in steps_path.read_bytes().splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        step = json.loads(line)
        if not isinstance(step, dict):
            raise TypeError("browser step must be an object")
        steps.append(step)
    return steps


def _safe_screenshot_keys(steps: list[dict]) -> tuple[set[str], str | None]:
    if not steps:
        return set(), "trace_missing"
    safe: set[str] = set()
    for step in steps:
        if step.get("event") == "quarantine":
            return set(), "quarantined"
        if step.get("event") in {"hard_stop", "limit_kill"}:
            return set(), step["event"]
        if step.get("event") == "action_gate" and (step.get("gate") or {}).get("outcome") in {
            "pending_approval",
            "denied",
        }:
            return set(), "action_blocked"
        screenshot_key = step.get("screenshot_key")
        if (
            isinstance(screenshot_key, str)
            and BRONZE_KEY.fullmatch(screenshot_key)
            and screen_is_cleared(step.get("screen"))
        ):
            safe.add(screenshot_key)
    return safe, None


def _valid_capture_time(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _is_mirrored_screenshot(lake: FileLake | S3Lake, key: str) -> bool:
    if not BRONZE_KEY.fullmatch(key) or not lake.exists(key):
        return False
    expected_digest = key.removeprefix("sha256:")
    return hashlib.sha256(lake.read_key(key)).hexdigest() == expected_digest


def _cell_job_id(cell_id: object) -> str:
    if not isinstance(cell_id, str):
        raise TypeError("browser session open omitted its cell ID")
    match = re.fullmatch(r"cell:([A-Za-z0-9][A-Za-z0-9_.-]*)", cell_id)
    if match is None:
        raise ValueError("browser controller returned an invalid cell ID")
    return f"job:{match.group(1)}"


def _extract_steps(steps: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_id = {
        step["step_id"]: step
        for step in steps
        if isinstance(step.get("step_id"), str) and step["step_id"]
    }
    extracts = {
        step_id: step
        for step_id, step in by_id.items()
        if (step.get("requested") or {}).get("tool") == "extract"
    }
    return by_id, extracts


def _quarantine_ancestor(extract_step: dict, by_id: dict[str, dict], screenshot_key: str) -> bool:
    parent_id = extract_step.get("parent_step_id")
    seen = {extract_step.get("step_id")}
    while parent_id is not None:
        if parent_id in seen:
            return True
        seen.add(parent_id)
        parent = by_id.get(parent_id)
        if parent is None:
            return True
        if parent.get("event") == "quarantine" and parent.get("screenshot_key") == screenshot_key:
            return True
        parent_id = parent.get("parent_step_id")
    return False


def _trace_step(
    *,
    run_id: str,
    source_id: str,
    objective_id: str,
    tdd_path: str,
    provenance: dict,
    status: str,
    session_id: str,
    row_count: int,
    parent_step_id: str,
) -> dict:
    return {
        "step_id": f"step:{uuid.uuid4().hex}",
        "run_id": run_id,
        "phase": 5,
        "source_id": source_id,
        "objective_id": objective_id,
        "tdd_path": tdd_path,
        "mode": "S1",
        "observed": {"controller_session": session_id},
        "requested": {"tool": "browser_agent.session.act"},
        "executed": {"tool": "browser_agent.session.act"},
        "evaluated": {"status": status, "rows": row_count},
        "parent_step_id": parent_step_id,
        "value_ids": [],
        "ts": datetime.now(UTC).isoformat(),
        "generated_by": provenance,
    }


def _dispatch_step(
    *,
    step_id: str,
    run_id: str,
    source_id: str,
    objective_id: str,
    tdd_path: str,
    target_fields: list[str],
    provenance: dict,
) -> dict:
    return {
        "step_id": step_id,
        "run_id": run_id,
        "phase": 5,
        "source_id": source_id,
        "objective_id": objective_id,
        "tdd_path": tdd_path,
        "mode": "S1",
        "observed": {"target_fields": target_fields},
        "requested": {"tool": "browser_agent.session.open"},
        "executed": {"tool": "browser_agent.session.open"},
        "evaluated": {"status": "started"},
        "parent_step_id": None,
        "value_ids": [],
        "ts": datetime.now(UTC).isoformat(),
        "generated_by": provenance,
    }


def _emit_rows(
    *,
    rows: list[dict],
    target_fields: list[str],
    ontology: dict,
    allowed_domains: list[str],
    lake: FileLake | S3Lake,
    store: SilverStore,
    run_id: str,
    source_id: str,
    source_type: str,
    objective_id: str,
    tdd_path: str,
    provenance: dict,
    coerce: Coerce,
    safe_screenshots: set[str],
    controller_steps: list[dict],
    target_volume: int,
) -> tuple[list[Observation], list[dict]]:
    classes = {item["id"]: item for item in ontology["classes"]}
    properties = {item["id"]: item for item in ontology["properties"]}
    targets = set(target_fields)
    controller_by_id, extract_steps = _extract_steps(controller_steps)
    emitted: list[Observation] = []
    trace: list[dict] = []
    emitted_entities: set[str] = set()

    for row in rows[:target_volume]:
        by_class: dict[str, dict[str, tuple[object, str, str, str, str, str]]] = defaultdict(dict)
        for property_id, cell in row.items():
            if (
                property_id not in targets
                or property_id not in properties
                or not isinstance(cell, dict)
            ):
                continue
            prop = properties[property_id]
            entity_class = prop["domain"]
            if entity_class not in classes:
                continue
            raw_value = cell.get("value")
            if raw_value is None or raw_value == "":
                continue
            selector = cell.get("selector")
            url = _safe_url(cell.get("url"), allowed_domains)
            screenshot_key = cell.get("screenshot_key")
            extract_step_id = cell.get("step_id")
            captured_at = cell.get("captured_at")
            if (
                not isinstance(selector, str)
                or not selector.strip()
                or len(selector) > 2048
                or url is None
                or not isinstance(screenshot_key, str)
                or not BRONZE_KEY.fullmatch(screenshot_key)
                or screenshot_key not in safe_screenshots
                or not _is_mirrored_screenshot(lake, screenshot_key)
                or not isinstance(extract_step_id, str)
                or not extract_step_id
                or not _valid_capture_time(captured_at)
            ):
                continue
            extract_step = extract_steps.get(extract_step_id)
            if extract_step is None or extract_step.get("screenshot_key") != screenshot_key:
                continue
            if captured_at != extract_step.get("ts"):
                continue
            observed_url = (extract_step.get("observed") or {}).get("url")
            if observed_url != url:
                continue
            requested_fields = (extract_step.get("requested") or {}).get("args", {}).get("fields")
            if not isinstance(requested_fields, dict) or property_id not in requested_fields:
                continue
            traced_value = ((extract_step.get("executed") or {}).get("values") or {}).get(
                property_id
            )
            if (
                not isinstance(traced_value, dict)
                or traced_value.get("value") != raw_value
                or traced_value.get("selector") != selector
                or _quarantine_ancestor(extract_step, controller_by_id, screenshot_key)
            ):
                continue
            try:
                value = coerce(raw_value, prop["datatype"])
            except (TypeError, ValueError):
                continue
            if value is not None:
                by_class[entity_class][property_id] = (
                    value,
                    selector.strip(),
                    url,
                    screenshot_key,
                    extract_step_id,
                    captured_at,
                )

        for entity_class, values in by_class.items():
            class_info = classes[entity_class]
            identity_property = class_info["identifier_property"]
            identity = values.get(identity_property) or values.get(class_info["title_property"])
            if identity is None:
                continue
            entity_id = f"{entity_class}:{hashlib.sha256(str(identity[0]).casefold().encode()).hexdigest()[:24]}"
            if entity_id in emitted_entities:
                continue
            emitted_entities.add(entity_id)
            for property_id, (
                value,
                selector,
                url,
                screenshot_key,
                extract_step_id,
                captured_at,
            ) in values.items():
                step_id = f"step:{uuid.uuid4().hex}"
                timestamp = datetime.now(UTC).isoformat()
                evidence = {
                    "url": url,
                    "bronze_key": screenshot_key,
                    "selector": selector,
                    "screenshot_key": screenshot_key,
                    "step_id": extract_step_id,
                    "captured_at": captured_at,
                    "source_id": source_id,
                    "source_type": source_type,
                    "format": "html",
                }
                item = Observation(
                    run_id=run_id,
                    entity_id=entity_id,
                    entity_class=entity_class,
                    property_id=property_id,
                    value=value,
                    evidence=evidence,
                    step_id=step_id,
                    generated_by=provenance,
                )
                tool_item = ToolObservation(
                    entity_id,
                    property_id,
                    value,
                    Evidence(url, screenshot_key, selector, captured_at, source_id, screenshot_key),
                    1.0,
                )
                emit_observation(
                    tool_item,
                    set(properties),
                    validate=lambda record, expected=value: record["value"] == expected,
                    write=lambda _record, observation=item: store.add(observation),
                )
                emitted.append(item)
                trace.append(
                    {
                        "step_id": step_id,
                        "run_id": run_id,
                        "phase": 5,
                        "source_id": source_id,
                        "objective_id": objective_id,
                        "tdd_path": tdd_path,
                        "mode": "S1",
                        "observed": {
                            "property_id": property_id,
                            "extract_step_id": extract_step_id,
                        },
                        "requested": {"tool": "emit.observation", "property_id": property_id},
                        "executed": {"tool": "emit.observation", "count": 1},
                        "evaluated": {"status": "ok", "literal_controller_value": True},
                        "parent_step_id": extract_step_id,
                        "value_ids": [item.value_id],
                        "ts": timestamp,
                        "generated_by": provenance,
                    }
                )
    return emitted, trace


def execute_controller(
    *,
    case_dir: Path,
    objective: dict,
    ontology: dict,
    tdd: dict,
    lake: FileLake | S3Lake,
    run_id: str,
    store: SilverStore,
    provenance: dict,
    feed: RunFeed,
    tdd_path: str,
    coerce: Coerce,
    client: BrowserAgentClient | None = None,
    steps_root: Path | None = None,
    captures_root: Path | None = None,
) -> ControllerResult:
    """Run one screened controller goal, mirror its captures, then emit only evidenced target properties."""
    controller = client or BrowserAgentClient.from_env(feed=feed)
    step_root = _shared_root(
        steps_root,
        "ONTOFILL_BROWSER_AGENT_STEPS_ROOT",
        "BA_STEPS_DIR",
    )
    capture_root = _shared_root(
        captures_root,
        "ONTOFILL_BROWSER_AGENT_CAPTURES_ROOT",
        "BA_CAPTURES_DIR",
    )
    source_id = objective["source_id"]
    objective_id = objective["id"]
    domains = list(tdd["allowed_domains"])
    controller_tdd = {
        **tdd,
        "run_id": run_id,
        "source_id": source_id,
        "objective_id": objective_id,
        "tdd_path": tdd_path,
        "job_id": f"job:{run_id}:{source_id}:{objective_id}",
        "case_dir": str(case_dir.resolve()),
        "start_url": objective["source_url"],
    }
    budget = float(tdd.get("budget_usd", 0.5))
    limits = {
        "max_steps": 30,
        "timeout_s": 900,
        "max_attempts": 3,
        "budget_usd": budget,
        "ttl_s": 900,
        "memory_mb": 1024,
        "cpus": 1.0,
        "pids": 256,
    }
    dispatch_id = f"step:{uuid.uuid4().hex}"
    feed.append_step(
        _dispatch_step(
            step_id=dispatch_id,
            run_id=run_id,
            source_id=source_id,
            objective_id=objective_id,
            tdd_path=tdd_path,
            target_fields=list(tdd["target_fields"]),
            provenance=provenance,
        )
    )
    opened = controller.session_open(controller_tdd, domains, limits)
    session_id = opened["session_id"]
    opened_cell_id = opened.get("cell_id")
    steps_path = step_root / f"{session_id}.jsonl"
    bridge: BrowserTraceBridge | None = None
    close_result: dict | None = None
    safe_result: dict | None = None
    result_status = "not_achieved"
    observe_blocked = False
    try:
        returned_path = opened.get("steps_path")
        if not isinstance(returned_path, str) or Path(returned_path).name != steps_path.name:
            raise ValueError("browser controller returned an unexpected session trace name")
        context = {
            "run_id": run_id,
            "source_id": source_id,
            "objective_id": objective_id,
            "tdd_path": tdd_path,
        }
        bridge = BrowserTraceBridge(
            session_id=session_id,
            steps_path=steps_path,
            steps_root=step_root,
            captures_root=capture_root,
            feed=feed,
            lake=lake,
            parent_step_id=dispatch_id,
            expected_context=context,
        )
        bridge.drain()
        act_result = controller.session_act(
            session_id,
            goal=_controller_goal(tdd, ontology),
        )
        result_status = str(act_result.get("status", "not_achieved"))
        bridge.drain()
        try:
            observed = controller.session_observe(session_id)
        except ObservationQuarantined as exc:
            observe_blocked = exc.screen_status != "missing"
        else:
            bridge.drain()
            observe_blocked = "screen" in observed and not screen_is_cleared(observed.get("screen"))
    finally:
        # Record a cleanup failure only after attempting close, trace drain, and job persistence.
        close_error: Exception | None = None
        try:
            close_result = controller.session_close(session_id)
        except Exception as exc:  # noqa: BLE001
            close_error = exc
        try:
            if bridge is not None:
                bridge.drain()
        except Exception as exc:  # noqa: BLE001
            if close_error is None:
                close_error = exc
        if close_result is not None:
            try:
                if close_result.get("closed") is not True:
                    raise RuntimeError("browser controller did not confirm session close")
                cell = close_result.get("cell")
                if not isinstance(cell, dict):
                    raise TypeError("browser controller close omitted its cell record")
                if cell.get("cell_id") != opened_cell_id:
                    raise ValueError("browser controller closed a different cell")
                if cell.get("released") is not True:
                    raise RuntimeError("browser controller did not confirm cell release")
                teardown = cell.get("teardown")
                if not isinstance(teardown, dict):
                    raise TypeError("browser controller close omitted teardown details")
                teardown_job = teardown.get("job_record")
                if not isinstance(teardown_job, dict):
                    raise TypeError("browser controller close omitted its teardown job record")
                expected_job_id = _cell_job_id(opened_cell_id)
                if teardown_job.get("job_id") != expected_job_id:
                    raise ValueError("browser teardown job does not match the released cell")
                checkpoints = teardown_job.get("checkpoints")
                if not isinstance(checkpoints, dict) or set(checkpoints) != {
                    "host",
                    "task",
                    "where",
                    "isolation",
                    "secrets",
                    "teardown",
                }:
                    raise ValueError("browser teardown job omitted its six proof checkpoints")
                validate_job_record(teardown_job)
                if teardown_job.get("run_id") not in {"run:unattached", run_id}:
                    raise ValueError("browser teardown job belongs to another run")
                if teardown_job.get("source_id") not in {"source:browser-cell", source_id}:
                    raise ValueError("browser teardown job belongs to another source")
                # PA's current cells endpoint emits unattached attribution; bind only those fields
                # to this validated session while preserving the substrate's measured proof.
                attributed_job = {
                    **teardown_job,
                    "run_id": run_id,
                    "source_id": source_id,
                    "generated_by": provenance,
                }
                append_job_record(lake, feed.case_id, attributed_job)
            except Exception as exc:  # noqa: BLE001
                if close_error is None:
                    close_error = exc
        elif close_error is None:
            close_error = RuntimeError("browser controller close returned no result")
        if close_error is not None:
            raise close_error

    controller_steps = _read_controller_steps(steps_path)
    safe_screenshots, trace_block = _safe_screenshot_keys(controller_steps)
    if result_status == "achieved" and not observe_blocked and trace_block is None:
        safe_result = act_result
    elif trace_block is not None:
        result_status = trace_block
    elif observe_blocked:
        result_status = "quarantined"

    extracted_rows = _rows((safe_result or {}).get("extracted"))
    observations, extraction_trace = _emit_rows(
        rows=extracted_rows,
        target_fields=tdd["target_fields"],
        ontology=ontology,
        allowed_domains=domains,
        lake=lake,
        store=store,
        run_id=run_id,
        source_id=source_id,
        source_type=objective.get("source_type", "web"),
        objective_id=objective_id,
        tdd_path=tdd_path,
        provenance=provenance,
        coerce=coerce,
        safe_screenshots=safe_screenshots,
        controller_steps=controller_steps,
        target_volume=tdd.get("target_volume", 300),
    )
    summary = _trace_step(
        run_id=run_id,
        source_id=source_id,
        objective_id=objective_id,
        tdd_path=tdd_path,
        provenance=provenance,
        status="ok" if observations else result_status,
        session_id=session_id,
        row_count=len({item.entity_id for item in observations}),
        parent_step_id=dispatch_id,
    )
    return ControllerResult(observations, [summary, *extraction_trace])
