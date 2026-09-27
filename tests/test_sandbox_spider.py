from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path

import pytest

from ontofill.lake import FileLake
from ontofill.sandbox import build_job_record, capture_url
from ontofill.sandbox.capture import _spider_settings


def _proof_inputs() -> dict:
    return {
        "pod_identity": {
            "hostname": "synthetic-spider-pod",
            "uname": {"system": "Linux", "release": "synthetic", "machine": "x86_64"},
            "cpu_virtualization_flags": [],
            "dev_kvm_present": False,
        },
        "isolation_probes": {
            "network": {"host": "blocked.invalid", "blocked": True, "status": 403},
            "writes": {
                "outside_pod": {"blocked": True},
                "outside_writable_mount": {"blocked": True},
            },
        },
        "secret_probes": {
            "env_keys_found": 0,
            "files_with_keys": 0,
            "metadata_ip": "BLOCKED",
            "mesh": "BLOCKED",
        },
    }


def _spider_result() -> dict:
    page_url = "https://catalog.example.invalid/"
    return {
        **_proof_inputs(),
        "url": page_url,
        "redirect_chain": [page_url],
        "status": 200,
        "crawl": {
            "attempted_pages": 1,
            "fetched_pages": 1,
            "max_depth": 2,
            "page_cap": 30,
            "delay_seconds": 1.0,
            "redirects": 0,
            "max_redirects_total": 100,
            "stop_reason": "queue_exhausted",
        },
        "pages": [
            {
                "requested_url": page_url,
                "final_url": page_url,
                "redirect_chain": [page_url],
                "depth": 0,
                "status": 200,
                "content_type": "text/html",
                "method": "GET",
                "link_kind": "navigate",
                "parent_url": None,
                "blocked_reason": None,
                "file_name": "page-0000.html",
            }
        ],
        "robots": [
            {
                "origin": "https://catalog.example.invalid",
                "url": "https://catalog.example.invalid/robots.txt",
                "http_status": 200,
                "decision": "allow",
                "crawl_delay_seconds": None,
                "file_name": "robots-0000.txt",
            }
        ],
        "edges": [],
        "request_count": 2,
        "steps": 2,
        "peak_memory_mb": 48.0,
    }


def _mock_spider_runtime(monkeypatch, result: dict) -> None:
    module = importlib.import_module("ontofill.sandbox.capture")
    events = [
        {"host": "blocked.invalid", "decision": "block"},
        {"host": "catalog.example.invalid", "decision": "allow"},
    ]
    monkeypatch.setattr(module, "_images", lambda: ("agent-image", "egress-image"))
    monkeypatch.setattr(
        module,
        "_host_check",
        lambda: (
            {
                "docker_host": "synthetic-host",
                "operating_system": "Linux",
                "architecture": "x86_64",
                "runtime": "runsc",
                "runtime_available": True,
            },
            ["--runtime", "runsc"],
        ),
    )
    monkeypatch.setattr(module, "_denied_probe_host", lambda domains: "blocked.invalid")
    monkeypatch.setattr(module, "_wait_proxy", lambda name: None)
    monkeypatch.setattr(module, "_container_network_ip", lambda name, network: "172.25.0.2")
    monkeypatch.setattr(module, "_events", lambda name: events)
    monkeypatch.setattr(
        module,
        "_cleanup_and_verify",
        lambda proxy, pod, network: {
            "pod_gone": True,
            "proxy_gone": True,
            "network_removed": True,
            "verified": True,
        },
    )
    monkeypatch.setattr(
        module,
        "_docker",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )

    def fake_agent(name, output: Path, *args, limits=None):
        (output / "page-0000.html").write_bytes(b"<html><body>Record title</body></html>")
        (output / "robots-0000.txt").write_bytes(b"User-agent: *\nAllow: /\n")
        (output / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return subprocess.CompletedProcess(["docker", "run", name], 0, "", "")

    monkeypatch.setattr(module, "_run_agent_pod", fake_agent)


def test_spider_settings_enforce_ratified_depth_and_page_caps() -> None:
    options = {
        "max_depth": 0,
        "page_cap": 50,
        "delay_seconds": 1.0,
        "max_redirects": 5,
        "max_redirects_total": 100,
        "max_response_bytes": 512 * 1024,
    }
    assert _spider_settings(options) == options
    with pytest.raises(ValueError, match="page_cap"):
        _spider_settings({**options, "page_cap": 51})


def test_spider_capture_persists_page_and_robots_bytes_with_one_six_checkpoint_job(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ONTOFILL_SANDBOX_RUNTIME", "runsc")
    result = _spider_result()
    _mock_spider_runtime(monkeypatch, result)
    lake = FileLake(tmp_path / "lake")
    job_id = "job:0123456789abcdef0123456789abcdef"
    streamed: list[dict] = []
    captured = capture_url(
        "https://catalog.example.invalid/",
        allowed_domains=["example.invalid"],
        lake=lake,
        run_id="synthetic-run",
        source_id="source-synthetic",
        objective_id="objective-synthetic",
        tdd_path="03-fanout/surface-map/source-synthetic/site-graph.json",
        phase=3,
        generated_by={
            "backend": "recorded",
            "model": "synthetic-test",
            "at": "2026-09-26T12:00:00+00:00",
        },
        redirect_domain="example.invalid",
        spider_options={
            "max_depth": 2,
            "page_cap": 30,
            "delay_seconds": 1.0,
            "max_redirects": 5,
            "max_redirects_total": 100,
            "max_response_bytes": 512 * 1024,
        },
        job_id=job_id,
        on_trace=streamed.append,
    )

    assert captured["job_id"] == job_id
    assert len(captured["trace"]) == 6
    assert {row["evaluated"]["proof_checkpoint"] for row in captured["trace"]} == {
        "dispatch_result",
        "host_check",
        "pod_identity",
        "isolation_probe",
        "secrets",
        "teardown",
    }
    assert len(captured["pages"]) == 1
    assert len(captured["robots"]) == 1
    page = captured["pages"][0]
    robot = captured["robots"][0]
    assert page["bronze_key"] == lake.put_bytes(
        b"<html><body>Record title</body></html>", {"content_type": "text/html"}
    )
    assert robot["bronze_key"] == lake.put_bytes(
        b"User-agent: *\nAllow: /\n", {"content_type": "text/plain"}
    )
    assert page["trace_step_id"].startswith("step:")
    assert page["job_id"] == job_id
    assert captured["page_trace"][-1]["executed"]["bronze_key"] == page["bronze_key"]
    assert captured["page_trace"][-1]["requested"]["method"] == "GET"
    write_steps = [
        step
        for step in streamed
        if step.get("executed", {}).get("bronze_key") in {page["bronze_key"], robot["bronze_key"]}
    ]
    assert len(write_steps) == 2
    assert {step["executed"]["bronze_key"] for step in write_steps} == {
        page["bronze_key"],
        robot["bronze_key"],
    }
    assert all(step["source_id"] == "source-synthetic" for step in write_steps)
    assert all(step["executed"]["job_id"] == job_id for step in write_steps)
    assert [step["step_id"] for step in write_steps] == [
        step["step_id"] for step in captured["page_trace"]
    ]

    job_record = build_job_record(captured)
    assert job_record["job_id"] == job_id
    assert job_record["checkpoints"]["task"]["ok"] is True
