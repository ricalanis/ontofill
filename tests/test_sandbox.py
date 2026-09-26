"""A synthetic site proves Docker containment, bronze capture, and trace output."""

from __future__ import annotations

import json
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
from ontofill.sandbox import CaptureBlocked, capture_url, fetch_url
from ontofill.sandbox.capture import _allowed_host


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


class SyntheticPage(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
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


def test_disallowed_target_fails_before_browser(tmp_path) -> None:
    with pytest.raises(CaptureBlocked) as raised:
        capture_url(
            "http://blocked.invalid/private",
            allowed_domains=["example.invalid"],
            lake=FileLake(tmp_path),
            run_id="synthetic-run",
            source_id="synthetic-source",
            objective_id="synthetic-objective",
            tdd_path="04-local/synthetic-tdd.json",
        )
    assert raised.value.trace[0]["evaluated"]["status"] == "blocked"
    assert raised.value.trace[0]["executed"]["network_request"] is False
    validate_trace_rows(raised.value.trace)


def test_live_docker_capture_and_egress_gate(docker_ready, synthetic_server, tmp_path) -> None:
    lake = FileLake(tmp_path)
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
    assert len(trace) == 5
    validate_trace_rows(trace)
    assert trace[0]["mode"] == "S1"
    assert all(trace[0][field] for field in ("observed", "requested", "executed", "evaluated"))
    assert trace[0]["executed"]["html_key"] == result["html_key"]
    assert {row["evaluated"]["proof_checkpoint"] for row in trace} == {
        "dispatch_result",
        "host_check",
        "pod_identity",
        "isolation_probe",
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
    assert proof["teardown"] == {
        "pod_gone": True,
        "proxy_gone": True,
        "network_removed": True,
        "verified": True,
    }


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
    assert len(result["trace"]) == 5
    validate_trace_rows(result["trace"])
    assert all(row["generated_by"] == provenance for row in result["trace"])
    assert result["proof"]["isolation_probe"]["blocked"]
    assert result["proof"]["teardown"]["verified"]
