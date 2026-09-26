"""Synthetic v0.4 proof records and lake append behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

from ontofill.lake import FileLake
from ontofill.sandbox import append_job_record, build_job_record, validate_job_record

PROVENANCE = {"backend": "recorded", "model": "synthetic-test", "at": "2026-09-26T14:00:00Z"}


def _schemas() -> tuple[dict[str, dict], Registry]:
    directory = Path(__file__).resolve().parents[1] / "schemas"
    schemas = {
        path.stem.removesuffix(".schema"): json.loads(path.read_text())
        for path in directory.glob("*.schema.json")
    }
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )
    return schemas, registry


def _validate(name: str, document: dict) -> None:
    schemas, registry = _schemas()
    Draft202012Validator(schemas[name], registry=registry, format_checker=FormatChecker()).validate(
        document
    )


def _capture_result() -> dict:
    names = ["dispatch_result", "host_check", "pod_identity", "isolation_probe", "teardown"]
    trace = [
        {
            "step_id": "step:synthetic",
            "run_id": "mock-synthetic",
            "source_id": "source:synthetic",
            "requested": {
                "url": "https://example.invalid/public",
                "allowed_domains": ["example.invalid"],
            },
            "evaluated": {"status": "captured", "proof_checkpoint": name},
            "generated_by": PROVENANCE,
        }
        for name in names
    ]
    return {
        "trace": trace,
        "status": 200,
        "started_at": "2026-09-26T14:00:00Z",
        "ended_at": "2026-09-26T14:00:02Z",
        "proof": {
            "dispatch_result": {"url": "https://example.invalid/public", "status": 200},
            "host_check": {
                "docker_host": "synthetic-host",
                "runtime": "runc",
                "runtime_available": True,
                "cpu_virtualization_flags": [],
                "dev_kvm_present": False,
            },
            "pod_identity": {
                "hostname": "synthetic-pod",
                "uname": {"system": "Linux", "release": "synthetic", "machine": "aarch64"},
            },
            "isolation_probe": {
                "network": {"host": "blocked.invalid", "blocked": True, "proxy_logged_block": True},
                "write_outside_pod": {"path": "/host/proof", "blocked": True},
                "write_outside_writable_mount": {"path": "/etc/proof", "blocked": True},
            },
            "teardown": {
                "pod_gone": True,
                "proxy_gone": True,
                "network_removed": True,
                "verified": True,
            },
        },
    }


def test_jobs_record_and_last_line_wins(tmp_path: Path) -> None:
    lake = FileLake(tmp_path)
    first = build_job_record(_capture_result(), job_id="job:synthetic")
    _validate("jobs", first)
    assert first["checkpoints"]["host"]["runtime"] == "runc"
    assert {probe["result"] for probe in first["checkpoints"]["isolation"]["probes"]} == {"BLOCKED"}
    key = append_job_record(lake, "synthetic-case", first)
    assert key == "runs/synthetic-case/mock-synthetic/jobs.jsonl"
    second = build_job_record(_capture_result(), job_id="job:synthetic", value_ids=["val:one"])
    append_job_record(lake, "synthetic-case", second)
    lines = [json.loads(line) for line in lake.read_key(key).splitlines()]
    latest = {row["job_id"]: row for row in lines}
    assert len(lines) == 2
    assert latest["job:synthetic"]["checkpoints"]["task"]["value_ids"] == ["val:one"]


def test_jobs_record_rejects_incomplete_proof_and_bad_time() -> None:
    result = _capture_result()
    result["trace"].pop()
    with pytest.raises(ValueError, match="five proof checkpoints"):
        build_job_record(result)
    result = _capture_result()
    result["ended_at"] = "2026-09-26T13:59:59Z"
    with pytest.raises(ValueError, match="precedes"):
        build_job_record(result)


def test_optional_live_url_and_trace_event() -> None:
    status = {
        "run_id": "mock-synthetic",
        "state": "running",
        "phase": 5,
        "checkpoint_pending": None,
        "updated_at": "2026-09-26T14:00:00Z",
        "sources": [],
        "metrics": {},
        "generated_by": PROVENANCE,
        "live_view_url": "https://example.invalid/live/synthetic",
    }
    _validate("run-status", status)
    status["live_view_url"] = "file:///private/path"
    with pytest.raises(ValidationError):
        _validate("run-status", status)

    trace = {
        "step_id": "step:synthetic",
        "run_id": "mock-synthetic",
        "phase": 5,
        "source_id": "source:synthetic",
        "objective_id": None,
        "tdd_path": "04-local/synthetic-tdd.json",
        "mode": "S1",
        "event": "escalation",
        "observed": {"page": "synthetic"},
        "requested": {"action": "read"},
        "executed": {"action": "read"},
        "evaluated": {"status": "ok"},
        "parent_step_id": None,
        "value_ids": [],
        "ts": "2026-09-26T14:00:00Z",
        "generated_by": PROVENANCE,
    }
    _validate("trace-step", trace)
    trace["event"] = "unknown"
    with pytest.raises(ValidationError):
        _validate("trace-step", trace)


def test_writer_validates_record(tmp_path: Path) -> None:
    record = build_job_record(_capture_result())
    record["checkpoints"]["isolation"]["probes"][0]["result"] = "FAILED"
    with pytest.raises(ValidationError):
        validate_job_record(record)
    with pytest.raises(ValidationError):
        append_job_record(FileLake(tmp_path), "synthetic-case", record)
