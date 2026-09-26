"""Run a disposable Playwright pod behind a TDD domain allowlist proxy."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from ontofill.lake import FileLake, S3Lake

_DOMAIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\Z")
_IMAGE_LOCK = threading.Lock()
_REPO_ROOT = Path(__file__).resolve().parents[3]


class CaptureError(RuntimeError):
    """Browser or proxy execution failed."""

    def __init__(self, message: str, trace: list[dict] | None = None) -> None:
        super().__init__(message)
        self.trace = trace or []


class CaptureBlocked(CaptureError):
    """A URL was outside the TDD allowlist before browser execution."""

    def __init__(self, message: str, trace: list[dict]) -> None:
        super().__init__(message)
        self.trace = trace


def _docker(*args: str, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CaptureError(f"Docker command failed: {args[0]}") from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1500:]
        raise CaptureError(f"Docker {args[0]} failed: {detail}")
    return result


def _images() -> tuple[str, str]:
    assets = Path(os.environ.get("ONTOFILL_SANDBOX_ASSETS", _REPO_ROOT / "sandbox"))

    def image_name(directory: str, override: str, base_name: str) -> str:
        if os.environ.get(override):
            return os.environ[override]
        asset_dir = assets / directory
        digest = hashlib.sha256(
            b"".join(path.read_bytes() for path in sorted(asset_dir.iterdir()) if path.is_file())
        ).hexdigest()[:12]
        return f"{base_name}:{digest}"

    agent = image_name("agent-pod", "ONTOFILL_AGENT_IMAGE", "ontofill-agent-pod")
    egress = image_name("egress", "ONTOFILL_EGRESS_IMAGE", "ontofill-egress")
    with _IMAGE_LOCK:
        for image, directory in ((egress, "egress"), (agent, "agent-pod")):
            if _docker("image", "inspect", image, check=False).returncode:
                _docker("build", "-t", image, str(assets / directory), timeout=900)
    return agent, egress


def _allowed_host(url: str, domains: list[str]) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _domains(allowed_domains: list[str]) -> list[str]:
    normalized = sorted({domain.lower().rstrip(".") for domain in allowed_domains})
    if not normalized or any(
        not _DOMAIN.fullmatch(domain) or ".." in domain for domain in normalized
    ):
        raise ValueError("allowed_domains must contain plain DNS names")
    return normalized


def _trace(
    *,
    step_id: str,
    run_id: str,
    phase: int,
    source_id: str,
    objective_id: str | None,
    tdd_path: str,
    observed: dict,
    requested: dict,
    executed: dict,
    evaluated: dict,
    ts: str,
    mode: str = "S1",
    generated_by: dict | None = None,
    parent_step_id: str | None = None,
) -> dict:
    return {
        "step_id": step_id,
        "run_id": run_id,
        "phase": phase,
        "source_id": source_id,
        "objective_id": objective_id,
        "tdd_path": tdd_path,
        "mode": mode,
        "observed": observed,
        "requested": requested,
        "executed": executed,
        "evaluated": evaluated,
        "parent_step_id": parent_step_id,
        "value_ids": [],
        "ts": ts,
        "generated_by": generated_by or _provenance(None),
    }


def _provenance(generated_by: dict | None) -> dict:
    if generated_by is None:
        return {"backend": "recorded", "model": "sandbox-test", "at": datetime.now(UTC).isoformat()}
    if set(generated_by) != {"backend", "model", "at"}:
        raise ValueError("generated_by must contain only backend, model and at")
    if generated_by.get("backend") not in {"recorded", "vultr"} or not generated_by.get("model"):
        raise ValueError("generated_by needs backend=recorded|vultr and model")
    try:
        at = datetime.fromisoformat(generated_by["at"])
    except (TypeError, ValueError) as exc:
        raise ValueError("generated_by needs an ISO-8601 at timestamp") from exc
    if at.tzinfo is None:
        raise ValueError("generated_by.at needs a timezone")
    return dict(generated_by)


def _host_check() -> tuple[dict, list[str]]:
    info = json.loads(_docker("info", "--format", "{{json .}}").stdout)
    selected = os.environ.get("ONTOFILL_SANDBOX_RUNTIME")
    runtime = selected or info.get("DefaultRuntime", "unknown")
    host = {
        "docker_host": info.get("Name", "unknown"),
        "operating_system": info.get("OperatingSystem", "unknown"),
        "architecture": info.get("Architecture", "unknown"),
        "runtime": runtime,
        "runtime_available": runtime in info.get("Runtimes", {}),
    }
    if selected and not host["runtime_available"]:
        raise CaptureError(f"requested sandbox runtime {selected} is unavailable")
    return host, (["--runtime", selected] if selected else [])


def _denied_probe_host(domains: list[str]) -> str:
    for suffix in ("invalid", "test", uuid.uuid4().hex):
        host = f"ontofill-proof-denied-{uuid.uuid4().hex[:8]}.{suffix}"
        if not _allowed_host("http://" + host, domains):
            return host
    raise CaptureError("could not select a non-allowlisted proof domain")


def _isolation_proof(probes: dict, events: list[dict]) -> dict:
    network = probes.get("network", {})
    writes = probes.get("writes", {})
    proxy_blocked = any(
        event.get("decision") == "block" and event.get("host") == network.get("host")
        for event in events
    )
    return {
        "network": {**network, "proxy_logged_block": proxy_blocked},
        "write_outside_pod": writes.get("outside_pod", {}),
        "write_outside_writable_mount": writes.get("outside_writable_mount", {}),
        "blocked": bool(
            network.get("blocked")
            and proxy_blocked
            and writes.get("outside_pod", {}).get("blocked")
            and writes.get("outside_writable_mount", {}).get("blocked")
        ),
    }


def _proof_rows(
    *,
    step_id: str,
    run_id: str,
    phase: int,
    source_id: str,
    objective_id: str | None,
    tdd_path: str,
    mode: str,
    generated_by: dict,
    host: dict,
    pod: dict,
    isolation: dict,
) -> list[dict]:
    details = (
        ("host_check", host),
        ("pod_identity", pod),
        ("isolation_probe", isolation),
    )
    return [
        _trace(
            step_id=f"step:{uuid.uuid4().hex}",
            run_id=run_id,
            phase=phase,
            source_id=source_id,
            objective_id=objective_id,
            tdd_path=tdd_path,
            observed={"checkpoint": checkpoint},
            requested={"proof_checkpoint": checkpoint},
            executed=detail,
            evaluated={
                "proof_checkpoint": checkpoint,
                "status": "verified"
                if checkpoint != "isolation_probe"
                else ("blocked" if detail["blocked"] else "failed"),
            },
            ts=datetime.now(UTC).isoformat(),
            mode=mode,
            generated_by=generated_by,
            parent_step_id=step_id,
        )
        for checkpoint, detail in details
    ]


def _cleanup_and_verify(proxy_name: str, pod_name: str, network: str) -> dict:
    _docker("rm", "-f", proxy_name, check=False)
    _docker("network", "rm", network, check=False)
    result = {
        "pod_gone": _docker("inspect", pod_name, check=False).returncode != 0,
        "proxy_gone": _docker("inspect", proxy_name, check=False).returncode != 0,
        "network_removed": _docker("network", "inspect", network, check=False).returncode != 0,
    }
    result["verified"] = all(result.values())
    return result


def _teardown_row(
    *,
    parent_step_id: str,
    run_id: str,
    phase: int,
    source_id: str,
    objective_id: str | None,
    tdd_path: str,
    mode: str,
    generated_by: dict,
    teardown: dict,
) -> dict:
    return _trace(
        step_id=f"step:{uuid.uuid4().hex}",
        run_id=run_id,
        phase=phase,
        source_id=source_id,
        objective_id=objective_id,
        tdd_path=tdd_path,
        observed={"checkpoint": "teardown"},
        requested={"pod_and_network_absent": True},
        executed=teardown,
        evaluated={
            "proof_checkpoint": "teardown",
            "status": "verified" if teardown["verified"] else "failed",
        },
        ts=datetime.now(UTC).isoformat(),
        mode=mode,
        generated_by=generated_by,
        parent_step_id=parent_step_id,
    )


def _wait_proxy(name: str) -> None:
    probe = "import socket; socket.create_connection(('127.0.0.1', 8888), 1).close()"
    for _ in range(40):
        if not _docker("exec", name, "python", "-c", probe, check=False, timeout=5).returncode:
            return
        time.sleep(0.25)
    raise CaptureError("egress proxy did not become ready")


def _events(name: str) -> list[dict]:
    result = _docker("logs", name, check=False)
    events = []
    for line in result.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def capture_url(
    url: str,
    *,
    allowed_domains: list[str],
    lake: FileLake | S3Lake,
    run_id: str,
    source_id: str,
    objective_id: str | None,
    tdd_path: str,
    phase: int = 5,
    generated_by: dict | None = None,
) -> dict:
    """Capture a public page; only the proxy container can leave the internal network."""
    domains = _domains(allowed_domains)
    step_id = f"step:{uuid.uuid4().hex}"
    timestamp = datetime.now(UTC).isoformat()
    provenance = _provenance(generated_by)
    request = {"url": url, "allowed_domains": domains, "capture": ["html", "a11y", "screenshot"]}
    if not _allowed_host(url, domains):
        row = _trace(
            step_id=step_id,
            run_id=run_id,
            phase=phase,
            source_id=source_id,
            objective_id=objective_id,
            tdd_path=tdd_path,
            observed={"url": url},
            requested=request,
            executed={"network_request": False},
            evaluated={"status": "blocked", "reason": "domain_not_allowed"},
            ts=timestamp,
            generated_by=provenance,
        )
        raise CaptureBlocked("URL domain is not allowed by the TDD", [row])

    agent_image, egress_image = _images()
    host, runtime_args = _host_check()
    denied_host = _denied_probe_host(domains)
    suffix = uuid.uuid4().hex[:12]
    network = f"ontofill-sandbox-{suffix}"
    proxy_name = f"ontofill-egress-{suffix}"
    browser_name = f"ontofill-browser-{suffix}"
    trace_rows: list[dict] | None = None
    proof: dict = {}
    _docker("network", "create", "--internal", network)
    try:
        _docker(
            "run",
            "-d",
            "--rm",
            "--name",
            proxy_name,
            "--network",
            network,
            "--network-alias",
            "egress",
            "--add-host",
            "host.docker.internal:host-gateway",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "128m",
            "-e",
            "ALLOWED_DOMAINS=" + ",".join(domains),
            egress_image,
        )
        _docker("network", "connect", "bridge", proxy_name)
        _wait_proxy(proxy_name)
        with tempfile.TemporaryDirectory(prefix="ontofill-capture-") as temp_dir:
            output = Path(temp_dir)
            output.chmod(0o777)
            browser = _docker(
                "run",
                *runtime_args,
                "--rm",
                "--name",
                browser_name,
                "--network",
                network,
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,size=512m",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "256",
                "--memory",
                "1g",
                "--shm-size",
                "256m",
                "-v",
                f"{output}:/out:rw",
                "-e",
                "CAPTURE_URL=" + url,
                "-e",
                "PROXY_URL=http://egress:8888",
                "-e",
                "PROBE_DENIED_HOST=" + denied_host,
                agent_image,
                timeout=90,
                check=False,
            )
            events = _events(proxy_name)
            if browser.returncode:
                detail = (browser.stderr or browser.stdout).strip()[-2000:]
                raise CaptureError(f"browser pod failed: {detail}; egress={events}")
            result = json.loads((output / "result.json").read_text(encoding="utf-8"))
            pod_identity = result["pod_identity"]
            host["cpu_virtualization_flags"] = pod_identity["cpu_virtualization_flags"]
            host["dev_kvm_present"] = pod_identity["dev_kvm_present"]
            isolation = _isolation_proof(result["isolation_probes"], events)
            if not isolation["blocked"]:
                raise CaptureError(f"sandbox isolation proof failed: {isolation}")
            final_url = result["url"]
            captured_at = datetime.now(UTC).isoformat()
            metadata = {
                "url": final_url,
                "captured_at": captured_at,
                "source_id": source_id,
                "step_id": step_id,
            }
            html_bytes = (output / "page.html").read_bytes()
            html_key = lake.put_bytes(html_bytes, {**metadata, "content_type": "text/html"})
            a11y_key = lake.put_bytes(
                (output / "a11y.txt").read_bytes(),
                {**metadata, "content_type": "text/plain"},
            )
            screenshot_key = lake.put_bytes(
                (output / "screenshot.png").read_bytes(),
                {**metadata, "content_type": "image/png"},
            )
            keys = {"html_key": html_key, "a11y_key": a11y_key, "screenshot_key": screenshot_key}
            row = _trace(
                step_id=step_id,
                run_id=run_id,
                phase=phase,
                source_id=source_id,
                objective_id=objective_id,
                tdd_path=tdd_path,
                observed={"url": final_url, "status": result["status"]},
                requested=request,
                executed={"network_request": True, **keys},
                evaluated={
                    "status": "captured",
                    "bronze_objects": 3,
                    "proof_checkpoint": "dispatch_result",
                },
                ts=captured_at,
                generated_by=provenance,
            )
            row["screenshot_key"] = screenshot_key
            trace_rows = [
                row,
                *_proof_rows(
                    step_id=step_id,
                    run_id=run_id,
                    phase=phase,
                    source_id=source_id,
                    objective_id=objective_id,
                    tdd_path=tdd_path,
                    mode="S1",
                    generated_by=provenance,
                    host=host,
                    pod=pod_identity,
                    isolation=isolation,
                ),
            ]
            proof = {
                "dispatch_result": {"url": final_url, "status": result["status"], **keys},
                "host_check": host,
                "pod_identity": pod_identity,
                "isolation_probe": isolation,
            }
            return {
                **keys,
                "html": html_bytes.decode("utf-8"),
                "url": final_url,
                "status": result["status"],
                "trace": trace_rows,
                "egress_events": events,
                "proof": proof,
            }
    finally:
        teardown = _cleanup_and_verify(proxy_name, browser_name, network)
        if trace_rows is not None:
            proof["teardown"] = teardown
            trace_rows.append(
                _teardown_row(
                    parent_step_id=step_id,
                    run_id=run_id,
                    phase=phase,
                    source_id=source_id,
                    objective_id=objective_id,
                    tdd_path=tdd_path,
                    mode="S1",
                    generated_by=provenance,
                    teardown=teardown,
                )
            )
        if not teardown["verified"]:
            raise CaptureError("sandbox teardown proof failed", trace_rows)


def fetch_url(
    url: str,
    *,
    allowed_domains: list[str],
    lake: FileLake | S3Lake,
    run_id: str,
    source_id: str,
    objective_id: str | None,
    tdd_path: str,
    phase: int = 5,
    generated_by: dict | None = None,
) -> dict:
    """Fetch a discovered file inside the same contained network and store raw bytes."""
    domains = _domains(allowed_domains)
    step_id = f"step:{uuid.uuid4().hex}"
    timestamp = datetime.now(UTC).isoformat()
    provenance = _provenance(generated_by)
    request = {"url": url, "allowed_domains": domains, "fetch": "bytes"}
    if not _allowed_host(url, domains):
        row = _trace(
            step_id=step_id,
            run_id=run_id,
            phase=phase,
            source_id=source_id,
            objective_id=objective_id,
            tdd_path=tdd_path,
            observed={"url": url},
            requested=request,
            executed={"network_request": False},
            evaluated={"status": "blocked", "reason": "domain_not_allowed"},
            ts=timestamp,
            mode="D0",
            generated_by=provenance,
        )
        raise CaptureBlocked("URL domain is not allowed by the TDD", [row])

    agent_image, egress_image = _images()
    host, runtime_args = _host_check()
    denied_host = _denied_probe_host(domains)
    suffix = uuid.uuid4().hex[:12]
    network = f"ontofill-sandbox-{suffix}"
    proxy_name = f"ontofill-egress-{suffix}"
    pod_name = f"ontofill-fetch-{suffix}"
    trace_rows: list[dict] | None = None
    proof: dict = {}
    _docker("network", "create", "--internal", network)
    try:
        _docker(
            "run",
            "-d",
            "--rm",
            "--name",
            proxy_name,
            "--network",
            network,
            "--network-alias",
            "egress",
            "--add-host",
            "host.docker.internal:host-gateway",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "128m",
            "-e",
            "ALLOWED_DOMAINS=" + ",".join(domains),
            egress_image,
        )
        _docker("network", "connect", "bridge", proxy_name)
        _wait_proxy(proxy_name)
        with tempfile.TemporaryDirectory(prefix="ontofill-fetch-") as temp_dir:
            output = Path(temp_dir)
            output.chmod(0o777)
            pod = _docker(
                "run",
                *runtime_args,
                "--rm",
                "--name",
                pod_name,
                "--network",
                network,
                "--read-only",
                "--tmpfs",
                "/tmp:rw,nosuid,size=512m",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "256",
                "--memory",
                "1g",
                "--shm-size",
                "256m",
                "-v",
                f"{output}:/out:rw",
                "-e",
                "CAPTURE_URL=" + url,
                "-e",
                "PROXY_URL=http://egress:8888",
                "-e",
                "CAPTURE_MODE=fetch",
                "-e",
                "PROBE_DENIED_HOST=" + denied_host,
                agent_image,
                timeout=90,
                check=False,
            )
            events = _events(proxy_name)
            if pod.returncode:
                detail = (pod.stderr or pod.stdout).strip()[-2000:]
                raise CaptureError(f"fetch pod failed: {detail}; egress={events}")
            result = json.loads((output / "result.json").read_text(encoding="utf-8"))
            pod_identity = result["pod_identity"]
            host["cpu_virtualization_flags"] = pod_identity["cpu_virtualization_flags"]
            host["dev_kvm_present"] = pod_identity["dev_kvm_present"]
            isolation = _isolation_proof(result["isolation_probes"], events)
            if not isolation["blocked"]:
                raise CaptureError(f"sandbox isolation proof failed: {isolation}")
            final_url = result["url"]
            if not _allowed_host(final_url, domains):
                raise CaptureError("fetch redirected outside the TDD allowlist")
            content = (output / "payload.bin").read_bytes()
            captured_at = datetime.now(UTC).isoformat()
            bronze_key = lake.put_bytes(
                content,
                {
                    "content_type": result["content_type"],
                    "url": final_url,
                    "captured_at": captured_at,
                    "source_id": source_id,
                    "step_id": step_id,
                },
            )
            row = _trace(
                step_id=step_id,
                run_id=run_id,
                phase=phase,
                source_id=source_id,
                objective_id=objective_id,
                tdd_path=tdd_path,
                observed={"url": final_url, "status": result["status"]},
                requested=request,
                executed={"network_request": True, "bronze_key": bronze_key},
                evaluated={
                    "status": "captured",
                    "bronze_objects": 1,
                    "proof_checkpoint": "dispatch_result",
                },
                ts=captured_at,
                mode="D0",
                generated_by=provenance,
            )
            trace_rows = [
                row,
                *_proof_rows(
                    step_id=step_id,
                    run_id=run_id,
                    phase=phase,
                    source_id=source_id,
                    objective_id=objective_id,
                    tdd_path=tdd_path,
                    mode="D0",
                    generated_by=provenance,
                    host=host,
                    pod=pod_identity,
                    isolation=isolation,
                ),
            ]
            proof = {
                "dispatch_result": {
                    "url": final_url,
                    "status": result["status"],
                    "bronze_key": bronze_key,
                },
                "host_check": host,
                "pod_identity": pod_identity,
                "isolation_probe": isolation,
            }
            return {
                "bytes": content,
                "content_type": result["content_type"],
                "bronze_key": bronze_key,
                "url": final_url,
                "status": result["status"],
                "trace": trace_rows,
                "egress_events": events,
                "proof": proof,
            }
    finally:
        teardown = _cleanup_and_verify(proxy_name, pod_name, network)
        if trace_rows is not None:
            proof["teardown"] = teardown
            trace_rows.append(
                _teardown_row(
                    parent_step_id=step_id,
                    run_id=run_id,
                    phase=phase,
                    source_id=source_id,
                    objective_id=objective_id,
                    tdd_path=tdd_path,
                    mode="D0",
                    generated_by=provenance,
                    teardown=teardown,
                )
            )
        if not teardown["verified"]:
            raise CaptureError("sandbox teardown proof failed", trace_rows)
