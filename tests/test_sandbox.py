"""A synthetic site proves Docker containment, bronze capture, and trace output."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from ontofill.lake import FileLake
from ontofill.sandbox import (
    CaptureBlocked,
    SandboxLimitExceeded,
    SandboxLimits,
    build_job_record,
    capture_url,
    fetch_url,
)
from ontofill.sandbox.capture import _allowed_host, _limit_row


def validate_trace_rows(rows: list[dict]) -> None:
    schema_dir = Path(__file__).resolve().parents[1] / "schemas"
    schemas = [json.loads(path.read_text()) for path in schema_dir.glob("*.schema.json")]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas
    )
    trace_schema = next(
        schema for schema in schemas if schema["title"] == "Execution trace JSONL step"
    )
    validator = Draft202012Validator(
        trace_schema, registry=registry, format_checker=FormatChecker()
    )
    for row in rows:
        validator.validate(row)


def _mock_capture_runtime(
    monkeypatch,
    *,
    target: str,
    final_url: str,
    events: list[dict] | None = None,
    redirect_chain: list[str] | None = None,
    navigation_error: str | None = None,
    limit_reason: str | None = None,
) -> list[dict]:
    module = importlib.import_module("ontofill.sandbox.capture")
    events = events or [{"host": "blocked.invalid", "decision": "block"}]
    monkeypatch.setattr(module, "_images", lambda: ("agent-image", "egress-image"))
    monkeypatch.setattr(
        module,
        "_host_check",
        lambda: ({"runtime": "runsc", "runtime_available": True}, []),
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

    def fake_agent(name, output, *args, limits=None):
        (output / "result.json").write_text(
            json.dumps(
                {
                    "url": final_url,
                    "status": 200,
                    "redirect_chain": redirect_chain
                    or ([target, final_url] if target != final_url else [target]),
                    **({"navigation_error": navigation_error} if navigation_error else {}),
                    **({"limit_reason": limit_reason} if limit_reason else {}),
                    "pod_identity": {
                        "hostname": "synthetic-pod",
                        "uname": {"system": "Linux"},
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
                    "steps": 2,
                    "peak_memory_mb": 50,
                }
            ),
            encoding="utf-8",
        )
        (output / "page.html").write_text("<html><body>synthetic</body></html>", encoding="utf-8")
        (output / "a11y.txt").write_text("synthetic accessibility", encoding="utf-8")
        (output / "screenshot.png").write_bytes(b"synthetic screenshot")
        return subprocess.CompletedProcess(["docker", "run", name], 0, "", "")

    monkeypatch.setattr(module, "_run_agent_pod", fake_agent)
    return events


class SyntheticPage(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/hostile":
            body = (
                Path(__file__).resolve().parents[1] / "sandbox/fixtures/hostile.html"
            ).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/data.csv":
            body = b"name\nProveedor Ejemplo 01\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = (
            b"<!doctype html><html><body><h1>Proveedor Ejemplo 01</h1>"
            b'<img src="http://blocked.invalid/private.png">'
            b"<script>fetch('/submit', {method: 'POST', body: 'x'}).catch(() => {})</script>"
            b"</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def synthetic_server():
    server = ThreadingHTTPServer(("0.0.0.0", 0), SyntheticPage)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://host.docker.internal:{server.server_port}/"
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()


@pytest.fixture(scope="module")
def docker_ready():
    if not shutil.which("docker"):
        pytest.skip("Docker CLI is unavailable")
    if subprocess.run(["docker", "info"], capture_output=True, check=False).returncode:
        pytest.skip("Docker daemon is unavailable")


def test_allowlist_matches_only_full_hosts() -> None:
    assert _allowed_host("https://source.example.invalid/path", ["example.invalid"])
    assert not _allowed_host("https://example.invalid.evil.invalid/", ["example.invalid"])
    assert not _allowed_host("file:///etc/passwd", ["example.invalid"])


def test_cross_domain_redirect_is_blocked_with_sandbox_proof(tmp_path, monkeypatch) -> None:
    target = "https://dept.example.test/branches"
    redirected = "https://identity.unknown.test/landing"
    chain = [
        target,
        "https://auth.dept.example.test/start",
        redirected,
    ]
    events = _mock_capture_runtime(
        monkeypatch,
        target=target,
        final_url=target,
        redirect_chain=chain,
        navigation_error="PlaywrightError",
        events=[
            {"host": "blocked.invalid", "decision": "block"},
            {"host": "identity.unknown.test", "decision": "block"},
        ],
    )

    with pytest.raises(CaptureBlocked) as raised:
        capture_url(
            target,
            allowed_domains=["dept.example.test"],
            lake=FileLake(tmp_path / "lake"),
            run_id="synthetic-run",
            source_id="synthetic-source",
            objective_id=None,
            tdd_path="04-local/synthetic-tdd.json",
            redirect_domain="example.test",
        )

    assert raised.value.result is not None
    dispatch = raised.value.result["proof"]["dispatch_result"]
    assert raised.value.result["proof"]["dispatch_result"]["redirect_chain"] == [
        target,
        "https://auth.dept.example.test/start",
        redirected,
    ]
    assert raised.value.result["redirect_chain"] == chain
    assert raised.value.result["url"] == redirected
    assert raised.value.result["allowed_domains"] == ["dept.example.test"]
    assert dispatch["allowed_domains"] == ["dept.example.test"]
    assert dispatch["reason"] == "redirect_outside_allowlist"
    assert raised.value.result["egress_events"] == events
    assert any(
        event["host"] == "identity.unknown.test" and event["decision"] == "block"
        for event in raised.value.result["egress_events"]
    )
    assert raised.value.result["proof"]["isolation_probe"]["blocked"] is True
    assert raised.value.result["proof"]["secrets"]["ok"] is True
    assert len(raised.value.trace) == 6
    assert {row["evaluated"].get("proof_checkpoint") for row in raised.value.trace} == {
        "dispatch_result",
        "host_check",
        "pod_identity",
        "isolation_probe",
        "secrets",
        "teardown",
    }
    assert raised.value.trace[0]["requested"]["url"] == target
    assert raised.value.trace[0]["evaluated"]["reason"] == "redirect_outside_allowlist"
    assert raised.value.trace[0]["evaluated"]["redirect_chain"] == chain
    assert raised.value.trace[0]["evaluated"]["allowed_domains"] == ["dept.example.test"]
    assert raised.value.trace[-1]["evaluated"]["proof_checkpoint"] == "teardown"
    validate_trace_rows(raised.value.trace)


def test_allowlisted_cross_domain_redirect_is_captured_and_logged(tmp_path, monkeypatch) -> None:
    target = "https://dept.example.test/branches"
    redirected = "https://catalog.other.test/libraries"
    _mock_capture_runtime(monkeypatch, target=target, final_url=redirected)

    result = capture_url(
        target,
        allowed_domains=["catalog.other.test", "dept.example.test"],
        lake=FileLake(tmp_path / "lake"),
        run_id="synthetic-run",
        source_id="synthetic-source",
        objective_id=None,
        tdd_path="03-fanout/discovery-loop.json",
        phase=3,
        redirect_domain="example.test",
    )

    assert result["url"] == redirected
    assert result["proof"]["dispatch_result"]["redirect_chain"] == [target, redirected]
    assert result["proof"]["dispatch_result"]["allowed_domains"] == [
        "catalog.other.test",
        "dept.example.test",
    ]
    assert "redirect_domain" not in result["trace"][0]["requested"]


def test_capture_limit_trace_retains_requested_url_and_policy(tmp_path, monkeypatch) -> None:
    target = "https://dept.example.test/branches"
    _mock_capture_runtime(
        monkeypatch,
        target=target,
        final_url=target,
        limit_reason="max_steps",
    )

    with pytest.raises(SandboxLimitExceeded) as raised:
        capture_url(
            target,
            allowed_domains=["dept.example.test"],
            lake=FileLake(tmp_path / "lake"),
            run_id="synthetic-run",
            source_id="synthetic-source",
            objective_id=None,
            tdd_path="04-local/synthetic-tdd.json",
        )

    assert raised.value.result is not None
    assert raised.value.result["requested"]["url"] == target
    assert raised.value.result["requested"]["allowed_domains"] == ["dept.example.test"]
    limit = raised.value.result["trace"][0]
    assert limit["requested"]["url"] == target
    assert limit["requested"]["allowed_domains"] == ["dept.example.test"]
    assert limit["evaluated"]["reason"] == "max_steps"
    assert limit["evaluated"]["redirect_chain"] == [target]
    assert limit["evaluated"]["allowed_domains"] == ["dept.example.test"]


def test_max_links_limit_row_retains_requested_url_and_policy() -> None:
    target = "https://catalog.example.test/records"
    row = _limit_row(
        parent_step_id="step:synthetic-parent",
        run_id="synthetic-run",
        phase=3,
        source_id="synthetic-source",
        objective_id=None,
        tdd_path="03-fanout/discovery-loop.json",
        mode="S1",
        generated_by={
            "backend": "recorded",
            "model": "sandbox-test",
            "at": datetime.now(UTC).isoformat(),
        },
        reason="max_links_exceeded",
        url=target,
        allowed_domains=["catalog.example.test"],
    )

    assert row["requested"]["url"] == target
    assert row["requested"]["allowed_domains"] == ["catalog.example.test"]
    assert row["evaluated"]["reason"] == "max_links_exceeded"
    assert row["evaluated"]["redirect_chain"] == [target]


def test_legacy_exact_subdomain_allowlist_without_redirect_boundary_still_captures(
    tmp_path, monkeypatch
) -> None:
    target = "https://www.example.com/branches"
    _mock_capture_runtime(monkeypatch, target=target, final_url=target)

    result = capture_url(
        target,
        allowed_domains=["www.example.com"],
        lake=FileLake(tmp_path / "lake"),
        run_id="synthetic-run",
        source_id="synthetic-source",
        objective_id=None,
        tdd_path="04-local/synthetic-tdd.json",
    )

    assert result["status"] == 200
    assert result["proof"]["dispatch_result"]["redirect_chain"] == [target]


def test_disallowed_target_fails_before_browser(tmp_path) -> None:
    target = "http://blocked.invalid/private"
    with pytest.raises(CaptureBlocked) as raised:
        capture_url(
            target,
            allowed_domains=["example.invalid"],
            lake=FileLake(tmp_path),
            run_id="synthetic-run",
            source_id="synthetic-source",
            objective_id="synthetic-objective",
            tdd_path="04-local/synthetic-tdd.json",
        )
    assert raised.value.trace[0]["evaluated"]["status"] == "blocked"
    assert raised.value.trace[0]["evaluated"]["reason"] == "domain_not_allowed"
    assert raised.value.trace[0]["evaluated"]["redirect_chain"] == [target]
    assert raised.value.trace[0]["evaluated"]["allowed_domains"] == ["example.invalid"]
    assert raised.value.trace[0]["requested"]["url"] == target
    assert raised.value.trace[0]["executed"]["network_request"] is False
    assert raised.value.result is not None
    assert raised.value.result["url"] == target
    assert raised.value.result["allowed_domains"] == ["example.invalid"]
    validate_trace_rows(raised.value.trace)


def test_live_docker_capture_and_egress_gate(
    docker_ready, synthetic_server, tmp_path, monkeypatch
) -> None:
    lake = FileLake(tmp_path)
    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "synthetic-do-not-use")
    result = capture_url(
        synthetic_server,
        allowed_domains=["host.docker.internal"],
        lake=lake,
        run_id="synthetic-run",
        source_id="synthetic-source",
        objective_id="synthetic-objective",
        tdd_path="04-local/synthetic-tdd.json",
    )

    assert "Proveedor Ejemplo 01" in result["html"]
    assert result["status"] == 200
    for name, content_type in (
        ("html_key", "text/html"),
        ("a11y_key", "text/plain"),
        ("screenshot_key", "image/png"),
    ):
        key = result[name]
        assert key.startswith("sha256:")
        assert lake.exists(key)
        metadata = lake.read_metadata(key)
        assert metadata["content_type"] == content_type
        assert metadata["source_id"] == "synthetic-source"
        assert metadata["step_id"] == result["trace"][0]["step_id"]

    assert b"Proveedor Ejemplo 01" in lake.read_key(result["a11y_key"])
    assert lake.read_key(result["screenshot_key"]).startswith(b"\x89PNG")
    assert any(event["decision"] == "allow" for event in result["egress_events"])
    assert any(
        event["decision"] == "block" and event["host"] == "blocked.invalid"
        for event in result["egress_events"]
    )
    assert not any(event["method"] == "POST" for event in result["egress_events"])

    trace = result["trace"]
    assert len(trace) == 6
    validate_trace_rows(trace)
    assert trace[0]["mode"] == "S1"
    assert all(trace[0][field] for field in ("observed", "requested", "executed", "evaluated"))
    assert trace[0]["executed"]["html_key"] == result["html_key"]
    assert {row["evaluated"]["proof_checkpoint"] for row in trace} == {
        "dispatch_result",
        "host_check",
        "pod_identity",
        "isolation_probe",
        "secrets",
        "teardown",
    }
    assert all(row["generated_by"]["backend"] == "recorded" for row in trace)
    proof = result["proof"]
    assert proof["host_check"]["runtime"] in {"runc", "runsc"}
    assert isinstance(proof["host_check"]["dev_kvm_present"], bool)
    assert proof["pod_identity"]["hostname"]
    assert proof["pod_identity"]["uname"]["system"] == "Linux"
    assert proof["isolation_probe"]["network"]["blocked"]
    assert proof["isolation_probe"]["network"]["proxy_logged_block"]
    assert proof["isolation_probe"]["write_outside_pod"]["blocked"]
    assert proof["isolation_probe"]["write_outside_writable_mount"]["blocked"]
    assert proof["secrets"] == {
        "ok": True,
        "env_keys_found": 0,
        "files_with_keys": 0,
        "metadata_ip": "BLOCKED",
        "mesh": "BLOCKED",
    }
    assert result["limits"]["memory_mb"] == 1024
    assert result["usage"]["steps"] == 2
    assert proof["teardown"] == {
        "pod_gone": True,
        "proxy_gone": True,
        "network_removed": True,
        "verified": True,
    }


def test_live_docker_step_limit_stops_pod_and_proves_teardown(
    docker_ready, synthetic_server, tmp_path
) -> None:
    with pytest.raises(SandboxLimitExceeded) as raised:
        capture_url(
            synthetic_server,
            allowed_domains=["host.docker.internal"],
            lake=FileLake(tmp_path),
            run_id="synthetic-run",
            source_id="synthetic-source",
            objective_id="synthetic-objective",
            tdd_path="04-local/synthetic-tdd.json",
            limits=SandboxLimits(max_steps=1),
        )
    assert raised.value.reason == "max_steps"
    assert raised.value.trace[0]["event"] == "limit_kill"
    assert raised.value.trace[0]["requested"]["url"] == synthetic_server
    assert raised.value.trace[0]["evaluated"] == {
        "status": "hard_stop",
        "reason": "max_steps",
        "redirect_chain": [synthetic_server],
        "allowed_domains": ["host.docker.internal"],
    }
    assert raised.value.trace[-1]["evaluated"]["proof_checkpoint"] == "teardown"
    assert raised.value.trace[-1]["evaluated"]["status"] == "verified"
    failed = build_job_record(raised.value.result)
    assert failed["failure_reason"] == "max_steps"
    assert failed["checkpoints"]["task"]["ok"] is False
    assert failed["checkpoints"]["secrets"]["ok"] is True
    assert failed["usage"]["steps"] == 1


def test_live_docker_file_fetch(docker_ready, synthetic_server, tmp_path) -> None:
    lake = FileLake(tmp_path)
    provenance = {
        "backend": "vultr",
        "model": "synthetic-dispatch",
        "at": datetime.now(UTC).isoformat(),
    }
    result = fetch_url(
        synthetic_server + "data.csv",
        allowed_domains=["host.docker.internal"],
        lake=lake,
        run_id="synthetic-run",
        source_id="synthetic-source",
        objective_id="synthetic-objective",
        tdd_path="04-local/synthetic-tdd.json",
        generated_by=provenance,
    )
    assert result["bytes"] == b"name\nProveedor Ejemplo 01\n"
    assert result["content_type"] == "text/csv"
    assert lake.read_key(result["bronze_key"]) == result["bytes"]
    assert lake.read_metadata(result["bronze_key"])["content_type"] == "text/csv"
    assert result["trace"][0]["mode"] == "D0"
    assert any(event["decision"] == "allow" for event in result["egress_events"])
    assert len(result["trace"]) == 6
    validate_trace_rows(result["trace"])
    assert all(row["generated_by"] == provenance for row in result["trace"])
    assert result["proof"]["isolation_probe"]["blocked"]
    assert result["proof"]["teardown"]["verified"]


@pytest.mark.skipif(
    os.environ.get("ONTOFILL_RUN_CONTAINMENT") != "1",
    reason="set ONTOFILL_RUN_CONTAINMENT=1 for hostile-page containment proof",
)
def test_on_demand_hostile_page_keeps_metadata_and_other_hosts_blocked(
    docker_ready, synthetic_server, tmp_path
) -> None:
    result = capture_url(
        synthetic_server + "hostile",
        allowed_domains=["host.docker.internal"],
        lake=FileLake(tmp_path),
        run_id="synthetic-run",
        source_id="synthetic-source",
        objective_id="synthetic-objective",
        tdd_path="04-local/synthetic-tdd.json",
    )
    assert "Ignore the prior task" in result["html"]
    assert result["proof"]["secrets"]["metadata_ip"] == "BLOCKED"
    assert result["proof"]["secrets"]["mesh"] == "BLOCKED"
    assert any(
        event["decision"] == "block" and event["host"] in {"169.254.169.254", "blocked.invalid"}
        for event in result["egress_events"]
    )
