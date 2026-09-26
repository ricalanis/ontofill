"""Contract v0.4 sandbox proof records and append-only job feed."""

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


def _probe(name: str, blocked: bool, detail: dict) -> dict:
    return {"probe": name, "result": "BLOCKED" if blocked else "ALLOWED", "detail": detail}


def build_job_record(
    capture_result: Mapping[str, Any],
    *,
    job_id: str | None = None,
    value_ids: Iterable[str] = (),
) -> dict:
    """Collapse one completed capture/fetch proof into a jobs.jsonl record."""
    trace = capture_result["trace"]
    expected = {"dispatch_result", "host_check", "pod_identity", "isolation_probe", "teardown"}
    if len(trace) != 5 or {row["evaluated"]["proof_checkpoint"] for row in trace} != expected:
        raise ValueError("capture result must contain five proof checkpoints")
    proof = capture_result["proof"]
    first = trace[0]
    host = proof["host_check"]
    pod = proof["pod_identity"]
    isolation = proof["isolation_probe"]
    teardown = proof["teardown"]
    values = list(dict.fromkeys(value_ids))
    if any(not _VALUE_ID.fullmatch(value) for value in values):
        raise ValueError("value_ids must be val:<id>")
    record = {
        "job_id": job_id or f"job:{uuid.uuid4().hex}",
        "run_id": first["run_id"],
        "step_id": first["step_id"],
        "source_id": first["source_id"],
        "started_at": capture_result["started_at"],
        "ended_at": capture_result["ended_at"],
        "generated_by": first["generated_by"],
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
                "ok": first["evaluated"]["status"] == "captured"
                and isinstance(capture_result["status"], int)
                and 200 <= capture_result["status"] < 300,
                "requested": first["requested"],
                "result": proof["dispatch_result"],
                "value_ids": values,
            },
            "where": {
                "ok": bool(pod["hostname"] and pod["uname"].get("system") == "Linux"),
                "hostname": pod["hostname"],
                "uname": pod["uname"],
            },
            "isolation": {
                "probes": [
                    _probe(
                        "network_non_allowlisted",
                        bool(
                            isolation["network"]["blocked"]
                            and isolation["network"]["proxy_logged_block"]
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
            },
            "teardown": {
                "ok": bool(teardown["verified"]),
                "detail": teardown,
            },
        },
    }
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
