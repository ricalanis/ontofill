"""Sandbox proof records, enforced limits and append-only job feed."""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from ontofill.lake import FileLake, S3Lake

_REPO_ROOT = Path(__file__).resolve().parents[3]
_VALUE_ID = re.compile(r"val:[A-Za-z0-9][A-Za-z0-9._:-]*\Z")
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
    http_outcome = outcome_for_http_status(capture_result.get("status"))
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
