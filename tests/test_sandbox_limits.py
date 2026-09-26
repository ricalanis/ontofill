"""Budget enforcement and opt-in destructive containment proof."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from ontofill.sandbox import SandboxLimitExceeded, SandboxLimits
from ontofill.sandbox import capture as capture_module


def test_limits_validate_and_produce_all_docker_caps() -> None:
    budget = SandboxLimits(memory_mb=512, cpus=0.5, pids=64, timeout_s=12, max_steps=2)
    assert budget.as_dict() == {
        "memory_mb": 512,
        "cpus": 0.5,
        "pids": 64,
        "timeout_s": 12,
        "max_steps": 2,
    }
    assert budget.docker_args() == (
        "--memory",
        "512m",
        "--memory-swap",
        "512m",
        "--cpus",
        "0.5",
        "--pids-limit",
        "64",
    )
    assert SandboxLimits.from_value(budget.as_dict()) == budget
    for invalid in (
        {**budget.as_dict(), "memory_mb": 128.5},
        {**budget.as_dict(), "cpus": 0},
        {**budget.as_dict(), "pids": True},
        {**budget.as_dict(), "timeout_s": 0},
        {**budget.as_dict(), "max_steps": 0},
    ):
        with pytest.raises(ValueError):
            SandboxLimits.from_value(invalid)


def test_docker_deadline_maps_to_timeout_and_memory_to_oom(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ONTOFILL_SANDBOX_DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)

    def timed_out(*_args, **_kwargs):
        raise capture_module.DockerTimeout("synthetic timeout")

    monkeypatch.setattr(capture_module, "_docker", timed_out)
    with pytest.raises(SandboxLimitExceeded, match="timeout"):
        capture_module._run_agent_pod("synthetic-pod", tmp_path, "synthetic-image")

    def oom(*args, **_kwargs):
        if args[0] == "inspect":
            return subprocess.CompletedProcess(args, 0, "true\n", "")
        return subprocess.CompletedProcess(args, 137, "", "synthetic OOM")

    monkeypatch.setattr(capture_module, "_docker", oom)
    with pytest.raises(SandboxLimitExceeded, match="memory"):
        capture_module._run_agent_pod("synthetic-pod", tmp_path, "synthetic-image")


def test_secret_proof_does_not_accept_claimed_zero_when_network_is_open() -> None:
    probes = {
        "env_keys_found": 0,
        "files_with_keys": 0,
        "metadata_ip": "BLOCKED",
        "mesh": "ALLOWED",
    }
    assert not capture_module._secret_proof(probes)["ok"]


def test_mesh_probe_target_must_be_netbird_address(monkeypatch) -> None:
    monkeypatch.setenv("ONTOFILL_CONTROL_NETBIRD_IP", "100.64.0.2")
    assert capture_module._mesh_probe_ip() == "100.64.0.2"
    monkeypatch.setenv("ONTOFILL_CONTROL_NETBIRD_IP", "169.254.169.254")
    with pytest.raises(ValueError, match="100.64.0.0/10"):
        capture_module._mesh_probe_ip()


def test_proxy_rejects_metadata_mesh_and_rebound_addresses(monkeypatch) -> None:
    path = Path(__file__).resolve().parents[1] / "sandbox/egress/proxy.py"
    spec = importlib.util.spec_from_file_location("sandbox_egress_proxy", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for address in (
        "169.254.169.254",
        "100.64.0.1",
        "10.42.0.8",
        "172.17.0.1",
        "192.168.50.2",
        "127.0.0.1",
        "::ffff:169.254.169.254",
    ):
        assert module._forbidden_ip(address)
    assert not module._forbidden_ip("93.184.215.14")

    def rebound(*_args, **_kwargs):
        return [
            (2, 1, 6, "", ("93.184.215.14", 443)),
            (2, 1, 6, "", ("169.254.169.254", 443)),
        ]

    monkeypatch.setattr(module.socket, "getaddrinfo", rebound)
    assert module._resolved_address("allowed.example", 443) is None

    monkeypatch.setattr(module, "ALLOWED", frozenset({"host.docker.internal"}))
    monkeypatch.setattr(module.Path, "read_text", lambda *_args, **_kwargs: "172.17.0.1 host.docker.internal\n")
    monkeypatch.setattr(
        module.socket, "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("172.17.0.1", 80))],
    )
    assert module._resolved_address("host.docker.internal", 80) == "172.17.0.1"
    assert module._resolved_address("other.example", 80) is None


@pytest.mark.skipif(
    os.environ.get("ONTOFILL_RUN_CONTAINMENT") != "1",
    reason="set ONTOFILL_RUN_CONTAINMENT=1 to run disposable runsc containment proof",
)
def test_on_demand_destructive_loop_is_killed_inside_runsc(tmp_path: Path) -> None:
    runtime = subprocess.run(
        ["docker", "info", "--format", "{{json .Runtimes}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    if '"runsc"' not in runtime.stdout:
        pytest.skip("runsc is unavailable")
    sentinel = tmp_path / "host-untouched"
    sentinel.write_text("host remains")
    script = (
        Path(__file__).resolve().parents[1] / "sandbox/fixtures/destructive_loop.py"
    ).read_text()
    victim = f"ontofill-containment-victim-{uuid.uuid4().hex[:10]}"
    sibling = f"ontofill-containment-sibling-{uuid.uuid4().hex[:10]}"
    base = [
        "docker",
        "run",
        "--runtime",
        "runsc",
        "--read-only",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "128m",
        "--cpus",
        "0.25",
        "--pids-limit",
        "32",
        "--user",
        "1000:1000",
    ]
    try:
        subprocess.run(
            [*base, "-d", "--name", sibling, "python:3.12-alpine", "sleep", "30"],
            capture_output=True,
            text=True,
            check=True,
        )
        with pytest.raises(subprocess.TimeoutExpired):
            subprocess.run(
                [*base, "--name", victim, "python:3.12-alpine", "python", "-c", script],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        state = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", sibling],
            capture_output=True,
            text=True,
            check=True,
        )
        assert state.stdout.strip() == "true"
        assert sentinel.read_text() == "host remains"
    finally:
        subprocess.run(["docker", "rm", "-f", victim, sibling], capture_output=True, check=False)
