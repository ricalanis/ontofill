"""Sandbox proof records, enforced limits and append-only job feed."""

from __future__ import annotations

import ipaddress
import json
import re
import threading
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from ontofill.lake import FileLake, S3Lake

_REPO_ROOT = Path(__file__).resolve().parents[3]
_VALUE_ID = re.compile(r"val:[A-Za-z0-9][A-Za-z0-9._:-]*\Z")
_MAX_NAVIGATION_ATTEMPTS = 16
_MAX_EGRESS_EVENTS = 32
_MAX_DOCUMENT_BYTES = 24 * 1024 * 1024
_ERROR_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:access[-_]?token|api[-_]?key|secret|password|credential|private[-_]?key|"
    r"access[-_]?key|authorization|token)[\"']?\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|bearer\s+[^\s,;]+|[^\s,;]+)"
)
_BEARER_CREDENTIAL = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_EGRESS_HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\Z")
_EGRESS_REASON = {
    "domain_allowed",
    "domain_not_allowed",
    "dns_failed",
    "address_rejected",
    "write_method_blocked",
    "invalid_request",
}
_JOBS_LOCK = threading.Lock()


def _validator() -> Draft202012Validator:
    schema_dir = _REPO_ROOT / "schemas"
    jobs = json.loads((schema_dir / "jobs.schema.json").read_text(encoding="utf-8"))
    common = json.loads((schema_dir / "common.schema.json").read_text(encoding="utf-8"))
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in (jobs, common)
    )
    return Draft202012Validator(jobs, registry=registry, format_checker=FormatChecker())


def validate_job_record(record: Mapping[str, Any]) -> None:
    _validator().validate(dict(record))
    started = datetime.fromisoformat(record["started_at"])
    ended = datetime.fromisoformat(record["ended_at"])
    if started > ended:
        raise ValueError("job ended_at precedes started_at")
    secrets = record["checkpoints"]["secrets"]
    if not secrets.get("not_run"):
        expected_ok = (
            secrets["env_keys_found"] == 0
            and secrets["files_with_keys"] == 0
            and secrets["metadata_ip"] == "BLOCKED"
            and secrets["mesh"] == "BLOCKED"
        )
        if secrets["ok"] != expected_ok:
            raise ValueError("secret checkpoint ok disagrees with measured probes")
    if record["usage"]["steps"] > record["limits"]["max_steps"]:
        raise ValueError("job steps exceed the recorded maximum")


def outcome_for_http_status(http_status: object) -> dict[str, Any] | None:
    """Describe an observed HTTP result separately from sandbox proof checkpoints."""
    if type(http_status) is not int or not 100 <= http_status <= 599:
        return None
    if http_status in {401, 403}:
        status = "refused"
    elif http_status == 429:
        status = "rate_limited"
    elif http_status >= 400:
        status = "http_error"
    else:
        status = "completed"
    outcome: dict[str, Any] = {"status": status, "http_status": http_status}
    if http_status >= 400:
        outcome["reason"] = f"http_{http_status}"
    return outcome


def _valid_http_status(value: object) -> int | None:
    if type(value) is int and 100 <= value <= 599:
        return value
    return None


def _safe_error_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    trailing = ""
    while raw and raw[-1] in ".,;)]}":
        trailing = raw[-1] + trailing
        raw = raw[:-1]
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        if not hostname:
            return "<url>" + trailing
        host = f"[{hostname}]" if ":" in hostname else hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
        safe = f"{parsed.scheme.lower()}://{host}{port}{parsed.path}"
    except ValueError:
        return "<url>" + trailing
    return safe + trailing


def _safe_error_message(value: object) -> str:
    raw = str(value)
    text = raw.splitlines()[0] if raw.splitlines() else "Navigation failed"
    text = "".join(
        character if character >= " " and character != "\x7f" else " " for character in text
    )
    text = _ERROR_URL.sub(_safe_error_url, text)
    text = _SECRET_ASSIGNMENT.sub(r"\1<redacted>", text)
    text = _BEARER_CREDENTIAL.sub("Bearer <redacted>", text)
    return " ".join(text.split())[:512] or "Navigation failed"


def _safe_error_type(value: object) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_.-]", "", str(value))[:128]
    return candidate or "NavigationError"


def normalize_navigation_attempts(
    value: object,
    *,
    legacy_error: object = None,
    fallback_http_status: object = None,
) -> list[dict[str, Any]]:
    """Bound and sanitize untrusted browser-attempt receipts before persistence."""
    raw_attempts = value if isinstance(value, list) else []
    attempts: list[dict[str, Any]] = []
    for item in raw_attempts[:_MAX_NAVIGATION_ATTEMPTS]:
        if not isinstance(item, Mapping):
            continue
        status = _valid_http_status(item.get("http_status"))
        raw_elapsed = item.get("elapsed_ms")
        elapsed = raw_elapsed if type(raw_elapsed) is int else 0
        elapsed = min(max(elapsed, 0), 120_000)
        raw_error = item.get("error")
        error = None
        if raw_error is not None:
            if isinstance(raw_error, Mapping):
                error_type = raw_error.get("type", "NavigationError")
                message = raw_error.get("message", error_type)
            else:
                error_type = type(raw_error).__name__
                message = raw_error
            error = {
                "type": _safe_error_type(error_type),
                "message": _safe_error_message(message),
            }
        attempts.append({"http_status": status, "elapsed_ms": elapsed, "error": error})

    if not attempts and legacy_error:
        status = _valid_http_status(fallback_http_status)
        error_type = _safe_error_type(legacy_error)
        attempts.append(
            {
                "http_status": status,
                "elapsed_ms": 0,
                "error": {"type": error_type, "message": _safe_error_message(legacy_error)},
            }
        )
    return attempts


def normalize_egress_events(value: object) -> list[dict[str, str]]:
    """Keep the most recent bounded proxy decisions without URL or IP literals."""
    if not isinstance(value, list):
        return []
    events: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        decision = item.get("decision")
        if not isinstance(decision, str) or decision not in {"allow", "block"}:
            continue
        raw_host = item.get("host")
        if not isinstance(raw_host, str):
            continue
        host = raw_host.strip().lower().rstrip(".")
        if not host or len(host) > 253 or any(mark in host for mark in ("/", "?", "#", "@")):
            continue
        try:
            ipaddress.ip_address(host.strip("[]"))
            host = "ip-address"
        except ValueError:
            if not _EGRESS_HOST.fullmatch(host):
                continue
        raw_method = item.get("method")
        method = re.sub(r"[^A-Za-z]", "", str(raw_method).upper())[:16] or "OTHER"
        reason = item.get("reason")
        if reason == "dns_unresolved":
            reason = "dns_failed"
        if not isinstance(reason, str) or reason not in _EGRESS_REASON:
            reason = "domain_allowed" if decision == "allow" else "domain_not_allowed"
        events.append({"host": host, "method": method, "decision": decision, "reason": reason})
    return events[-_MAX_EGRESS_EVENTS:]


def _diagnostic_outcome(capture_result: Mapping[str, Any]) -> dict[str, Any] | None:
    attempts = normalize_navigation_attempts(
        capture_result.get("navigation_attempts"),
        legacy_error=capture_result.get("navigation_error"),
        fallback_http_status=capture_result.get("status"),
    )
    events = normalize_egress_events(capture_result.get("egress_events"))
    raw_capture_reason = capture_result.get("capture_reason")
    capture_reason = (
        raw_capture_reason
        if isinstance(raw_capture_reason, str)
        and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", raw_capture_reason)
        else None
    )
    has_navigation_error = any(attempt["error"] is not None for attempt in attempts)
    is_saved_download = isinstance(capture_result.get("document_key"), str)
    if has_navigation_error and not is_saved_download:
        last_status = next(
            (attempt["http_status"] for attempt in reversed(attempts) if attempt["http_status"]),
            None,
        )
        error = next(attempt["error"] for attempt in reversed(attempts) if attempt["error"])
        outcome: dict[str, Any] = {
            "status": "navigation_error",
            "reason": capture_reason or error["type"],
        }
        if last_status is not None:
            outcome["http_status"] = last_status
    else:
        outcome = outcome_for_http_status(capture_result.get("status")) or {}
        if capture_reason and (not outcome or outcome.get("status") == "completed"):
            outcome = {"status": "navigation_error", "reason": capture_reason}
            status = _valid_http_status(capture_result.get("status"))
            if status is not None:
                outcome["http_status"] = status
    if not outcome:
        return None
    if capture_reason:
        outcome["capture_reason"] = capture_reason
    if attempts:
        outcome["navigation_attempts"] = attempts
    if events:
        outcome["egress_events"] = events
    document_key = capture_result.get("document_key")
    content_type = capture_result.get("document_content_type")
    size_bytes = capture_result.get("document_size_bytes")
    if (
        isinstance(document_key, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", document_key)
        and isinstance(content_type, str)
        and 0 < len(content_type) <= 128
        and type(size_bytes) is int
        and 1 <= size_bytes <= _MAX_DOCUMENT_BYTES
    ):
        outcome.update(
            {
                "artifact_kind": "download",
                "document_key": document_key,
                "document_content_type": content_type,
                "document_size_bytes": size_bytes,
            }
        )
    return outcome


def _probe(name: str, blocked: bool, detail: dict) -> dict:
    return {"probe": name, "result": "BLOCKED" if blocked else "ALLOWED", "detail": detail}


def _isolation_checkpoint(isolation: dict | None) -> dict:
    if isolation is None:
        return {"probes": [], "not_run": True}
    return {
        "probes": [
            _probe(
                "network_non_allowlisted",
                bool(
                    isolation["network"]["blocked"] and isolation["network"]["proxy_logged_block"]
                ),
                isolation["network"],
            ),
            _probe(
                "write_outside_pod",
                bool(isolation["write_outside_pod"]["blocked"]),
                isolation["write_outside_pod"],
            ),
            _probe(
                "write_outside_writable_mount",
                bool(isolation["write_outside_writable_mount"]["blocked"]),
                isolation["write_outside_writable_mount"],
            ),
        ]
    }


def _failed_job_record(
    capture_result: Mapping[str, Any], job_id: str | None, values: list[str]
) -> dict:
    trace = capture_result["trace"]
    if not trace or trace[0].get("event") != "limit_kill":
        raise ValueError("failed job requires a limit_kill trace")
    first = trace[0]
    proof = capture_result["proof"]
    host = proof.get("host_check")
    pod = proof.get("pod_identity")
    teardown = proof["teardown"]
    host_check = (
        {
            "ok": bool(host["runtime_available"]),
            "sandbox_host": host["docker_host"],
            "runtime": host["runtime"],
            "virt": {
                "cpu_virtualization_flags": host["cpu_virtualization_flags"],
                "dev_kvm_present": host["dev_kvm_present"],
            },
        }
        if host and "cpu_virtualization_flags" in host and "dev_kvm_present" in host
        else {"ok": False, "not_run": True}
    )
    where = (
        {
            "ok": bool(pod["hostname"] and pod["uname"].get("system") == "Linux"),
            "hostname": pod["hostname"],
            "uname": pod["uname"],
        }
        if pod
        else {"ok": False, "not_run": True}
    )
    record = {
        "job_id": job_id or capture_result.get("job_id") or f"job:{uuid.uuid4().hex}",
        "run_id": first["run_id"],
        "step_id": first["step_id"],
        "source_id": first["source_id"],
        "started_at": capture_result["started_at"],
        "ended_at": capture_result["ended_at"],
        "generated_by": first["generated_by"],
        "limits": capture_result["limits"],
        "usage": capture_result["usage"],
        "failure_reason": capture_result["failure_reason"],
        "outcome": {
            "status": "failed",
            "reason": capture_result["failure_reason"],
        },
        "checkpoints": {
            "host": host_check,
            "task": {
                "ok": False,
                "requested": capture_result["requested"],
                "result": {"reason": capture_result["failure_reason"]},
                "value_ids": values,
            },
            "where": where,
            "isolation": _isolation_checkpoint(proof.get("isolation_probe")),
            "secrets": proof.get("secrets") or {"ok": False, "not_run": True},
            "teardown": {"ok": bool(teardown["verified"]), "detail": teardown},
        },
    }
    failed_outcome = record["outcome"]
    attempts = normalize_navigation_attempts(
        capture_result.get("navigation_attempts"),
        legacy_error=capture_result.get("navigation_error"),
        fallback_http_status=capture_result.get("status"),
    )
    events = normalize_egress_events(capture_result.get("egress_events"))
    if attempts:
        failed_outcome["navigation_attempts"] = attempts
    if events:
        failed_outcome["egress_events"] = events
    validate_job_record(record)
    return record


def build_job_record(
    capture_result: Mapping[str, Any],
    *,
    job_id: str | None = None,
    value_ids: Iterable[str] = (),
) -> dict:
    """Collapse one completed capture/fetch proof into a jobs.jsonl record."""
    trace = capture_result["trace"]
    values = list(dict.fromkeys(value_ids))
    if any(not _VALUE_ID.fullmatch(value) for value in values):
        raise ValueError("value_ids must be val:<id>")
    if capture_result.get("failure_reason"):
        return _failed_job_record(capture_result, job_id, values)
    expected = {
        "dispatch_result",
        "host_check",
        "pod_identity",
        "isolation_probe",
        "secrets",
        "teardown",
    }
    if len(trace) != 6 or {row["evaluated"]["proof_checkpoint"] for row in trace} != expected:
        raise ValueError("capture result must contain six proof checkpoints")
    proof = capture_result["proof"]
    first = trace[0]
    host = proof["host_check"]
    pod = proof["pod_identity"]
    isolation = proof["isolation_probe"]
    secrets = proof["secrets"]
    teardown = proof["teardown"]
    http_outcome = _diagnostic_outcome(capture_result)
    record = {
        "job_id": job_id or capture_result.get("job_id") or f"job:{uuid.uuid4().hex}",
        "run_id": first["run_id"],
        "step_id": first["step_id"],
        "source_id": first["source_id"],
        "started_at": capture_result["started_at"],
        "ended_at": capture_result["ended_at"],
        "generated_by": first["generated_by"],
        "limits": capture_result["limits"],
        "usage": capture_result["usage"],
        "checkpoints": {
            "host": {
                "ok": bool(host["runtime_available"]),
                "sandbox_host": host["docker_host"],
                "runtime": host["runtime"],
                "virt": {
                    "cpu_virtualization_flags": host["cpu_virtualization_flags"],
                    "dev_kvm_present": host["dev_kvm_present"],
                },
            },
            "task": {
                "ok": first["evaluated"]["status"] == "captured" and http_outcome is not None,
                "requested": first["requested"],
                "result": proof["dispatch_result"],
                "value_ids": values,
            },
            "where": {
                "ok": bool(pod["hostname"] and pod["uname"].get("system") == "Linux"),
                "hostname": pod["hostname"],
                "uname": pod["uname"],
            },
            "isolation": _isolation_checkpoint(isolation),
            "secrets": secrets,
            "teardown": {
                "ok": bool(teardown["verified"]),
                "detail": teardown,
            },
        },
    }
    if http_outcome is not None:
        record["outcome"] = http_outcome
    validate_job_record(record)
    return record


def build_repair_job_record(
    execution: Mapping[str, Any] | object,
    *,
    context: Mapping[str, Any],
    request: Mapping[str, Any],
    result: Mapping[str, Any],
    limits: Mapping[str, Any],
    started_at: str,
    ended_at: str,
    failure_reason: str | None = None,
) -> dict:
    """Collapse one repair pod run into a six-checkpoint jobs.jsonl record.

    Mirrors the parse pod's proof so repair work is visible in the sandbox feed.
    Any missing probe yields an honest ``not_run``/false checkpoint rather than a
    fabricated pass.
    """
    proof = getattr(execution, "proof", None)
    if not isinstance(proof, dict):
        proof = execution.get("proof") if isinstance(execution, dict) else None
    proof = proof if isinstance(proof, dict) else {}
    usage = getattr(execution, "usage", None)
    if not isinstance(usage, dict):
        usage = execution.get("usage") if isinstance(execution, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    host = proof.get("host_check")
    pod = proof.get("pod")
    isolation = proof.get("isolation")
    secrets = proof.get("secrets")
    teardown = proof.get("teardown")
    host_checkpoint = (
        {
            "ok": bool(host.get("runtime_available") and host.get("runtime") == "runsc"),
            "sandbox_host": host.get("docker_host", "unknown"),
            "runtime": host.get("runtime", "unknown"),
            "virt": {
                "cpu_virtualization_flags": pod.get("cpu_virtualization_flags", []) if pod else [],
                "dev_kvm_present": bool(pod and pod.get("dev_kvm_present")),
            },
        }
        if isinstance(host, dict)
        else {"ok": False, "not_run": True}
    )
    where = (
        {
            "ok": bool(pod.get("hostname") and pod.get("uname", {}).get("system") == "Linux"),
            "hostname": pod.get("hostname", "unknown"),
            "uname": pod.get("uname", {}),
        }
        if isinstance(pod, dict)
        else {"ok": False, "not_run": True}
    )
    isolation_checkpoint = (
        {
            "probes": [
                {
                    "probe": str(item.get("probe", "unnamed")),
                    "result": "BLOCKED" if item.get("blocked") else "ALLOWED",
                    "detail": {
                        str(key): value
                        for key, value in item.items()
                        if key not in {"probe", "blocked"}
                        and isinstance(value, (str, int, float, bool))
                    },
                }
                for item in isolation.get("probes", [])
            ]
        }
        if isinstance(isolation, dict) and isolation.get("probes")
        else {"probes": [], "not_run": True}
    )
    secrets_checkpoint = (
        {
            "env_keys_found": int(secrets.get("env_keys_found", 0)),
            "files_with_keys": int(secrets.get("files_with_keys", 0)),
            "metadata_ip": secrets.get("metadata_ip", "ALLOWED"),
            "mesh": secrets.get("mesh", "ALLOWED"),
        }
        if isinstance(secrets, dict)
        else {"ok": False, "not_run": True}
    )
    if isinstance(secrets, dict):
        secrets_checkpoint["ok"] = bool(secrets.get("ok"))
    teardown_detail = (
        {
            "pod_gone": bool(teardown.get("pod_gone")),
            "proxy_gone": bool(teardown.get("proxy_gone")),
            "network_removed": bool(teardown.get("network_removed")),
            "verified": bool(teardown.get("verified")),
        }
        if isinstance(teardown, dict)
        else {"pod_gone": False, "proxy_gone": False, "network_removed": False, "verified": False}
    )
    record = {
        "job_id": context["job_id"],
        "run_id": context["run_id"],
        "step_id": context["step_id"],
        "source_id": context["source_id"],
        "started_at": started_at,
        "ended_at": ended_at,
        "generated_by": context["generated_by"],
        "limits": dict(limits),
        "usage": {
            "peak_memory_mb": max(0.0, float(usage.get("peak_memory_mb", 0.0))),
            "wall_s": max(0.0, float(usage.get("wall_s", 0.0))),
            "steps": max(0, int(usage.get("steps", 1))),
        },
        "checkpoints": {
            "host": host_checkpoint,
            "task": {
                "ok": bool(result.get("ok")),
                "requested": dict(request),
                "result": dict(result),
                "value_ids": [],
            },
            "where": where,
            "isolation": isolation_checkpoint,
            "secrets": secrets_checkpoint,
            "teardown": {"ok": teardown_detail["verified"], "detail": teardown_detail},
        },
        "outcome": {
            "status": "completed" if result.get("ok") else "failed",
            **({"reason": str(failure_reason)} if failure_reason else {}),
        },
    }
    if failure_reason in {"timeout", "memory", "pids", "max_steps"}:
        record["failure_reason"] = failure_reason
    validate_job_record(record)
    return record


def append_job_record(lake: FileLake | S3Lake, case_id: str, record: Mapping[str, Any]) -> str:
    """Append a verified record; consumers use the last line for each job_id."""
    validate_job_record(record)
    key = f"runs/{case_id}/{record['run_id']}/jobs.jsonl"
    line = (json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    with _JOBS_LOCK:
        existing = lake.read_key(key) if lake.exists(key) else b""
        if existing and not existing.endswith(b"\n"):
            raise ValueError("existing jobs.jsonl has an incomplete final line")
        lake.write_key(key, existing + line)
    return key
