"""Synthetic six-checkpoint proof records and lake append behavior."""

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
    names = [
        "dispatch_result",
        "host_check",
        "pod_identity",
        "isolation_probe",
        "secrets",
        "teardown",
    ]
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
        "limits": {"memory_mb": 1024, "cpus": 1, "pids": 256, "timeout_s": 90, "max_steps": 32},
        "usage": {"peak_memory_mb": 48, "wall_s": 2, "steps": 2},
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
            "secrets": {
                "ok": True,
                "env_keys_found": 0,
                "files_with_keys": 0,
                "metadata_ip": "BLOCKED",
                "mesh": "BLOCKED",
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
    assert first["checkpoints"]["secrets"]["ok"]
    assert first["outcome"] == {"status": "completed", "http_status": 200}
    assert first["limits"]["memory_mb"] == 1024
    assert first["usage"]["steps"] == 2
    key = append_job_record(lake, "synthetic-case", first)
    assert key == "runs/synthetic-case/mock-synthetic/jobs.jsonl"
    second = build_job_record(_capture_result(), job_id="job:synthetic", value_ids=["val:one"])
    append_job_record(lake, "synthetic-case", second)
    lines = [json.loads(line) for line in lake.read_key(key).splitlines()]
    latest = {row["job_id"]: row for row in lines}
    assert len(lines) == 2
    assert latest["job:synthetic"]["checkpoints"]["task"]["value_ids"] == ["val:one"]


def test_site_refusal_is_an_outcome_not_a_failed_sandbox_checkpoint() -> None:
    result = _capture_result()
    result["status"] = 403
    result["proof"]["dispatch_result"]["status"] = 403
    result["navigation_attempts"] = [{"http_status": 403, "elapsed_ms": 17, "error": None}]
    result["egress_events"] = [
        {
            "host": "example.invalid",
            "method": "CONNECT",
            "decision": "allow",
            "reason": "domain_allowed",
        }
    ]
    record = build_job_record(result)

    assert record["outcome"] == {
        "status": "refused",
        "reason": "http_403",
        "http_status": 403,
        "navigation_attempts": result["navigation_attempts"],
        "egress_events": result["egress_events"],
    }
    assert record["checkpoints"]["task"]["ok"] is True
    assert all(
        probe["result"] == "BLOCKED" for probe in record["checkpoints"]["isolation"]["probes"]
    )
    assert record["checkpoints"]["teardown"]["ok"] is True
    validate_job_record(record)


def test_navigation_error_is_distinct_from_an_http_response_and_schema_bounded() -> None:
    result = _capture_result()
    result["status"] = None
    result["navigation_error"] = "PlaywrightError"
    result["navigation_attempts"] = [
        {
            "http_status": 302,
            "elapsed_ms": 33,
            "error": {
                "type": "PlaywrightError",
                "message": "net::ERR_BLOCKED_BY_CLIENT",
            },
        }
    ]
    result["egress_events"] = [
        {
            "host": "identity.example.invalid",
            "method": "CONNECT",
            "decision": "block",
            "reason": "domain_not_allowed",
        }
    ]
    record = build_job_record(result)

    assert record["outcome"] == {
        "status": "navigation_error",
        "reason": "PlaywrightError",
        "http_status": 302,
        "navigation_attempts": result["navigation_attempts"],
        "egress_events": result["egress_events"],
    }
    _validate("jobs", record)

    record["outcome"]["navigation_attempts"][0]["elapsed_ms"] = 120001
    with pytest.raises(ValidationError):
        _validate("jobs", record)


def test_navigation_error_without_http_response_still_has_a_job_outcome() -> None:
    result = _capture_result()
    result["status"] = None
    result["navigation_error"] = "PlaywrightError"
    result["navigation_attempts"] = [
        {
            "http_status": None,
            "elapsed_ms": 12,
            "error": {"type": "PlaywrightError", "message": "net::ERR_NAME_NOT_RESOLVED"},
        }
    ]
    record = build_job_record(result)
    assert record["outcome"]["status"] == "navigation_error"
    assert "http_status" not in record["outcome"]
    assert record["outcome"]["navigation_attempts"][0]["http_status"] is None


def test_jobs_outcome_is_optional_for_existing_rows() -> None:
    record = build_job_record(_capture_result())
    record.pop("outcome")
    validate_job_record(record)


def test_jobs_record_rejects_incomplete_proof_and_bad_time() -> None:
    result = _capture_result()
    result["trace"].pop()
    with pytest.raises(ValueError, match="six proof checkpoints"):
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


def test_job_rejects_false_secret_claim_and_over_budget_steps() -> None:
    record = build_job_record(_capture_result())
    record["checkpoints"]["secrets"]["metadata_ip"] = "ALLOWED"
    with pytest.raises(ValueError, match="secret checkpoint"):
        validate_job_record(record)
    record["checkpoints"]["secrets"]["metadata_ip"] = "BLOCKED"
    record["usage"]["steps"] = record["limits"]["max_steps"] + 1
    with pytest.raises(ValueError, match="steps exceed"):
        validate_job_record(record)


def test_early_limit_job_marks_unrun_probes_without_fake_success() -> None:
    result = _capture_result()
    result["failure_reason"] = "timeout"
    result["requested"] = {"url": "https://example.invalid/public"}
    result["usage"] = {"peak_memory_mb": 0, "wall_s": 90, "steps": 0}
    result["trace"] = [
        {
            **result["trace"][0],
            "event": "limit_kill",
            "evaluated": {"status": "hard_stop", "reason": "timeout"},
        },
        result["trace"][-1],
    ]
    result["proof"] = {
        "host_check": {
            "docker_host": "synthetic-host",
            "runtime": "runc",
            "runtime_available": True,
        },
        "pod_identity": None,
        "isolation_probe": None,
        "secrets": None,
        "teardown": result["proof"]["teardown"],
    }
    record = build_job_record(result)
    _validate("jobs", record)
    assert record["checkpoints"]["host"] == {"ok": False, "not_run": True}
    assert record["checkpoints"]["where"] == {"ok": False, "not_run": True}
    assert record["checkpoints"]["isolation"] == {"probes": [], "not_run": True}
    assert record["checkpoints"]["secrets"] == {"ok": False, "not_run": True}
    assert record["checkpoints"]["teardown"]["ok"]
