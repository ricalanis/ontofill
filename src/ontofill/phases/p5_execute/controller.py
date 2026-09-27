"""Run an S1 TDD through the browser controller and preserve its capture trail."""

from __future__ import annotations

import hashlib
import json
import os
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

Coerce = Callable[[object, str], str | int | float | bool | None]


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
    sensitive_query_names = {
        "access_token",
        "api_key",
        "auth",
        "key",
        "password",
        "secret",
        "token",
    }
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or any(name.casefold() in sensitive_query_names for name, _ in parse_qsl(parsed.query))
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


def _safe_screenshot_keys(steps_path: Path) -> tuple[set[str], str | None]:
    if not steps_path.exists():
        return set(), "trace_missing"
    safe: set[str] = set()
    for line in steps_path.read_bytes().splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        step = json.loads(line)
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
) -> tuple[list[Observation], list[dict]]:
    classes = {item["id"]: item for item in ontology["classes"]}
    properties = {item["id"]: item for item in ontology["properties"]}
    targets = set(target_fields)
    emitted: list[Observation] = []
    trace: list[dict] = []

    for row in rows:
        by_class: dict[str, dict[str, tuple[object, str, str, str]]] = defaultdict(dict)
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
            if (
                not isinstance(selector, str)
                or not selector.strip()
                or len(selector) > 2048
                or url is None
                or not isinstance(screenshot_key, str)
                or not BRONZE_KEY.fullmatch(screenshot_key)
                or screenshot_key not in safe_screenshots
                or not lake.exists(screenshot_key)
            ):
                continue
            try:
                value = coerce(raw_value, prop["datatype"])
            except (TypeError, ValueError):
                continue
            if value is not None:
                by_class[entity_class][property_id] = (value, selector.strip(), url, screenshot_key)

        for entity_class, values in by_class.items():
            class_info = classes[entity_class]
            identity_property = class_info["identifier_property"]
            identity = values.get(identity_property) or values.get(class_info["title_property"])
            if identity is None:
                continue
            entity_id = f"{entity_class}:{hashlib.sha256(str(identity[0]).casefold().encode()).hexdigest()[:24]}"
            step_id = f"step:{uuid.uuid4().hex}"
            timestamp = datetime.now(UTC).isoformat()
            value_ids: list[str] = []
            for property_id, (value, selector, url, screenshot_key) in values.items():
                evidence = {
                    "url": url,
                    "bronze_key": screenshot_key,
                    "selector": selector,
                    "screenshot_key": screenshot_key,
                    "captured_at": timestamp,
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
                    Evidence(url, screenshot_key, selector, timestamp, source_id, screenshot_key),
                    1.0,
                )
                emit_observation(
                    tool_item,
                    set(properties),
                    validate=lambda record, expected=value: record["value"] == expected,
                    write=lambda _record, observation=item: store.add(observation),
                )
                emitted.append(item)
                value_ids.append(item.value_id)
            trace.append(
                {
                    "step_id": step_id,
                    "run_id": run_id,
                    "phase": 5,
                    "source_id": source_id,
                    "objective_id": objective_id,
                    "tdd_path": tdd_path,
                    "mode": "S1",
                    "observed": {"controller_extracted_properties": list(values)},
                    "requested": {"tool": "emit.observation", "properties": list(values)},
                    "executed": {"tool": "emit.observation", "count": len(value_ids)},
                    "evaluated": {"status": "ok", "literal_controller_values": True},
                    "parent_step_id": None,
                    "value_ids": value_ids,
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
    }
    opened = controller.session_open(controller_tdd, domains, limits)
    session_id = opened["session_id"]
    steps_path = step_root / f"{session_id}.jsonl"
    bridge: BrowserTraceBridge | None = None
    safe_result: dict | None = None
    result_status = "not_achieved"
    observe_blocked = False
    try:
        returned_path = opened.get("steps_path")
        if not isinstance(returned_path, str) or Path(returned_path).name != steps_path.name:
            raise ValueError("browser controller returned an unexpected session trace name")
        bridge = BrowserTraceBridge(
            session_id=session_id,
            steps_path=steps_path,
            steps_root=step_root,
            captures_root=capture_root,
            feed=feed,
            lake=lake,
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
        try:
            controller.session_close(session_id)
        finally:
            if bridge is not None:
                bridge.drain()

    safe_screenshots, trace_block = _safe_screenshot_keys(steps_path)
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
    )
    return ControllerResult(observations, [summary, *extraction_trace])
