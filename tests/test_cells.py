"""Synthetic browser-cell lifecycle checks; no Docker host is changed."""

from __future__ import annotations

import json
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from ontofill.lake import FileLake
from ontofill.sandbox import CellError, CellManager, SandboxLimits
from ontofill.sandbox import cells as cells_module
from ontofill.sandbox.cell_api import serve_cells


def _fake_docker(monkeypatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def fake(*args: str, **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:2] == ("info", "--format"):
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    {"Name": "synthetic", "ServerVersion": "28.5.1", "Runtimes": {"runsc": {}}}
                ),
                "",
            )
        if args[:2] == ("inspect", "--format"):
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"Running": True, "OOMKilled": False}), ""
            )
        if args[0] == "inspect" or args[:2] == ("network", "inspect"):
            return subprocess.CompletedProcess(args, 1, "", "not found")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(cells_module, "_docker", fake)
    monkeypatch.setattr(cells_module, "_images", lambda: ("agent:test", "egress:test"))
    monkeypatch.setattr(cells_module, "_wait_proxy", lambda _name: None)
    monkeypatch.setattr(
        cells_module, "_events", lambda _name: [{"decision": "block", "host": "denied.invalid"}]
    )
    monkeypatch.setattr(cells_module, "_denied_probe_host", lambda _domains: "denied.invalid")
    monkeypatch.setattr(cells_module, "_mesh_probe_ip", lambda: "100.64.0.2")
    monkeypatch.setattr(
        cells_module,
        "_published_port",
        lambda _name, internal_port=9222: 49152 if internal_port == 9222 else 49153,
    )
    monkeypatch.setattr(cells_module, "_open_tunnel", lambda port: (port, None))
    monkeypatch.setattr(
        cells_module, "_wait_cdp", lambda port: f"ws://127.0.0.1:{port}/devtools/browser/synthetic"
    )
    monkeypatch.setattr(
        cells_module,
        "_wait_preflight",
        lambda _name: {
            "pod_identity": {
                "hostname": "synthetic-hands",
                "uname": {"system": "Linux", "release": "synthetic", "machine": "x86_64"},
                "cpu_virtualization_flags": ["vmx"],
                "dev_kvm_present": False,
            },
            "isolation_probes": {
                "network": {"host": "denied.invalid", "blocked": True, "status": 403},
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
            "peak_memory_mb": 36.5,
        },
    )
    return calls


def test_native_cell_lifecycle_enforces_network_caps_and_six_proofs(
    monkeypatch, tmp_path: Path
) -> None:
    calls = _fake_docker(monkeypatch)
    lake = FileLake(tmp_path / "lake")
    trace: list[dict] = []
    manager = CellManager(
        lake=lake, case_id="synthetic", run_id="mock-synthetic", on_trace=trace.append
    )
    limits = SandboxLimits(memory_mb=512, cpus=0.5, pids=64, timeout_s=60, max_steps=2)
    opened = manager.create("native", ["Example.invalid"], limits)
    assert opened["cdp_url"] == "ws://127.0.0.1:49152/devtools/browser/synthetic"
    assert opened["brain_url"] is None and opened["live_view_port"] is None
    assert manager.record_step(opened["cell_id"]) == 1
    manager.report_task_result(opened["cell_id"], {"page_status": 200}, ok=True)
    closed = manager.destroy(opened["cell_id"])
    record = closed["job_record"]
    assert closed["state"] == "destroyed"
    assert record["checkpoints"]["task"]["ok"]
    assert record["checkpoints"]["secrets"]["ok"]
    assert record["checkpoints"]["teardown"]["ok"]
    assert len(record["checkpoints"]) == 6
    assert {row["evaluated"]["proof_checkpoint"] for row in trace} == {
        "host_check",
        "pod_identity",
        "isolation_probe",
        "secrets",
        "dispatch_result",
        "teardown",
    }
    assert record["usage"]["steps"] == 1
    assert record["limits"] == limits.as_dict()
    assert lake.exists("runs/synthetic/mock-synthetic/jobs.jsonl")
    assert any(call[:3] == ("network", "create", "--internal") for call in calls)
    proxy_run = next(call for call in calls if call[:2] == ("run", "-d") and "egress:test" in call)
    hands_run = next(call for call in calls if call[:2] == ("run", "-d") and "agent:test" in call)
    assert ("network", "connect", "bridge") == next(
        call[:3] for call in calls if call[:2] == ("network", "connect")
    )
    assert "--network" in proxy_run and "--network" in hands_run
    assert hands_run[hands_run.index("--runtime") + 1] == "runsc"
    assert hands_run[hands_run.index("--memory") + 1] == "512m"
    assert hands_run[hands_run.index("-p") + 1] == "127.0.0.1::9222"
    assert not any("API_KEY" in item or "SETUP_KEY" in item for item in hands_run)


def test_max_steps_kills_cell_and_records_honest_failure(monkeypatch) -> None:
    _fake_docker(monkeypatch)
    manager = CellManager()
    cell_id = manager.create("native", ["example.invalid"], SandboxLimits(max_steps=1))["cell_id"]
    assert manager.record_step(cell_id) == 1
    with pytest.raises(CellError, match="max_steps"):
        manager.record_step(cell_id)
    status = manager.status(cell_id)
    assert status["state"] == "destroyed" and status["failure_reason"] == "max_steps"
    job = manager.destroy(cell_id)["job_record"]
    assert job["failure_reason"] == "max_steps"
    assert not job["checkpoints"]["task"]["ok"]
    assert job["usage"]["steps"] == 1


def test_preflight_failure_closes_resources_without_claiming_proof(monkeypatch) -> None:
    calls = _fake_docker(monkeypatch)
    monkeypatch.setattr(
        cells_module,
        "_wait_preflight",
        lambda _name: {
            "pod_identity": {
                "hostname": "synthetic",
                "uname": {"system": "Linux", "release": "x", "machine": "x"},
                "cpu_virtualization_flags": [],
                "dev_kvm_present": False,
            },
            "isolation_probes": {
                "network": {"host": "denied.invalid", "blocked": True},
                "writes": {
                    "outside_pod": {"blocked": True},
                    "outside_writable_mount": {"blocked": True},
                },
            },
            "secret_probes": {
                "env_keys_found": 1,
                "files_with_keys": 0,
                "metadata_ip": "BLOCKED",
                "mesh": "BLOCKED",
            },
            "peak_memory_mb": 9.0,
        },
    )
    manager = CellManager()
    with pytest.raises(CellError, match="preflight"):
        manager.create("native", ["example.invalid"])
    assert any(call[:2] == ("network", "rm") for call in calls)
    job = next(iter(manager._cells.values())).job_record
    assert job is not None and not job["checkpoints"]["secrets"]["ok"]


def test_unsupported_paths_fail_before_docker(monkeypatch) -> None:
    calls = _fake_docker(monkeypatch)
    manager = CellManager()
    with pytest.raises(CellError, match="Vultr admission"):
        manager.create("native", ["example.invalid"], placement="throwaway_vx1")
    with pytest.raises(CellError, match="skyvern config"):
        manager.create("skyvern", ["example.invalid"])
    assert calls == []


def _skyvern_config() -> dict:
    return {
        "image": "public.ecr.aws/skyvern/skyvern@sha256:" + "a" * 64,
        "postgres_image": "postgres:14-alpine@sha256:" + "b" * 64,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
    }


def _brain_env() -> dict:
    return {
        "OPENAI_COMPATIBLE_API_BASE": "http://100.64.0.2:8787/v1",
        "OPENAI_COMPATIBLE_API_KEY": "session-only-token-synthetic",
    }


def test_skyvern_creates_brain_with_separate_network_and_exact_gateway_firewall(
    monkeypatch, tmp_path: Path
) -> None:
    calls = _fake_docker(monkeypatch)
    monkeypatch.setenv("ONTOFILL_SANDBOX_DOCKER_HOST", "ssh://sandbox@100.64.0.3")
    monkeypatch.setattr(cells_module, "_ensure_image", lambda _image: None)
    monkeypatch.setattr(cells_module, "_container_uplink_ip", lambda _name, _network: "172.17.0.9")
    monkeypatch.setattr(cells_module, "_wait_gateway", lambda *_args: None)
    monkeypatch.setattr(cells_module, "_wait_brain", lambda _port: None)
    monkeypatch.setattr(
        cells_module.CellManager, "_wait_database", staticmethod(lambda _name: None)
    )
    monkeypatch.setattr(
        cells_module,
        "_probe_brain",
        lambda *_args: {
            "cdp": "ALLOWED",
            "gateway": "ALLOWED",
            "egress": "BLOCKED",
            "metadata": "BLOCKED",
            "mesh": "BLOCKED",
            "credential_env_names": ["OPENAI_API_KEY"],
        },
    )
    firewall_calls: list[tuple[str, ...]] = []
    rules: set[tuple[str, ...]] = set()

    def firewall(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        firewall_calls.append(args)
        rule = (args[1], *args[3:]) if args[0] == "-I" else tuple(args[1:])
        if args[0] == "-I":
            rules.add(rule)
        elif args[0] == "-D":
            rules.discard(rule)
        rc = 0 if args[0] != "-C" or rule in rules else 1
        return subprocess.CompletedProcess(args, rc, "", "")

    monkeypatch.setattr(cells_module, "_host_iptables", firewall)
    token_calls: list[tuple[str, ...]] = []

    def token_docker(token: str, *args: str) -> None:
        assert token == "session-only-token-synthetic"
        assert token not in " ".join(args)
        token_calls.append(args)

    monkeypatch.setattr(cells_module, "_docker_with_session_token", token_docker)
    trace: list[dict] = []
    manager = CellManager(
        lake=FileLake(tmp_path / "lake"),
        case_id="synthetic",
        run_id="mock-synthetic",
        on_trace=trace.append,
    )
    opened = manager.create(
        "skyvern", ["example.invalid"], skyvern=_skyvern_config(), brain_env=_brain_env()
    )
    assert opened["brain_url"] == "http://127.0.0.1:49153"
    assert opened["cdp_url"].startswith("ws://127.0.0.1:")
    assert manager.status(opened["cell_id"])["state"] == "ready"
    assert len(token_calls) == 1 and "-e" in token_calls[0]
    runs = [call for call in calls if call[:2] == ("run", "-d")]
    brain_run = token_calls[0]
    brain_network = brain_run[brain_run.index("--network") + 1]
    hands_run = next(call for call in runs if "agent:test" in call)
    hands_network = hands_run[hands_run.index("--network") + 1]
    assert brain_network != hands_network
    uplink = next(
        call[-1] for call in calls if call[:2] == ("network", "create") and "uplink" in call[-1]
    )
    assert uplink not in {brain_network, hands_network}
    assert any(
        call[:2] == ("network", "connect")
        and call[2:]
        == (
            uplink,
            next(run[run.index("--name") + 1] for run in runs if "RELAY_LISTEN_PORT=8787" in run),
        )
        for call in calls
    )
    assert "-p" not in brain_run
    assert any(
        call[:4] == ("network", "create", "--internal", "-o")
        and "gateway_mode_ipv4=isolated" in call[4]
        for call in calls
    )
    assert any(call[:2] == ("run", "-d") and "127.0.0.1::8000" in call for call in runs)
    assert "-e" in brain_run and "OPENAI_API_KEY" in brain_run
    assert brain_run[brain_run.index("--log-driver") + 1] == "none"
    assert "session-only-token-synthetic" not in json.dumps(runs)
    assert any(
        "-s" in rule
        and "172.17.0.9" in rule
        and "100.64.0.2" in rule
        and "8787" in rule
        and "ACCEPT" in rule
        for rule in firewall_calls
    )
    assert any("DROP" in rule and "172.17.0.9" in rule for rule in firewall_calls)
    assert any(rule[:2] == ("-I", "INPUT") and "172.17.0.9" in rule for rule in firewall_calls)
    with pytest.raises(CellError, match="session credential"):
        manager.report_task_result(
            opened["cell_id"], {"leak": "session-only-token-synthetic"}, ok=True
        )
    manager.report_task_result(opened["cell_id"], {"task": "synthetic"}, ok=True)
    closed = manager.destroy(opened["cell_id"])
    assert closed["job_record"]["checkpoints"]["teardown"]["ok"]
    assert "session-only-token-synthetic" not in json.dumps(closed)
    assert "session-only-token-synthetic" not in json.dumps(trace)
    assert rules == set()


def test_skyvern_config_rejects_unpinned_or_long_lived_credentials_before_docker(
    monkeypatch,
) -> None:
    calls = _fake_docker(monkeypatch)
    monkeypatch.setenv("ONTOFILL_SANDBOX_DOCKER_HOST", "ssh://sandbox@100.64.0.3")
    manager = CellManager()
    config = _skyvern_config()
    config["image"] = "public.ecr.aws/skyvern/skyvern:latest"
    with pytest.raises(CellError, match="sha256"):
        manager.create("skyvern", ["example.invalid"], skyvern=config, brain_env=_brain_env())
    env = _brain_env()
    env["OPENAI_COMPATIBLE_API_BASE"] = "http://169.254.169.254:8787"
    with pytest.raises(CellError, match="NetBird"):
        manager.create("skyvern", ["example.invalid"], skyvern=_skyvern_config(), brain_env=env)
    config = _skyvern_config()
    config["expires_at"] = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    with pytest.raises(CellError, match="expire"):
        manager.create("skyvern", ["example.invalid"], skyvern=config, brain_env=_brain_env())
    env = _brain_env()
    env["OPENAI_COMPATIBLE_API_KEY"] = "short"
    with pytest.raises(CellError, match="session gateway token"):
        manager.create("skyvern", ["example.invalid"], skyvern=_skyvern_config(), brain_env=env)
    env = _brain_env()
    env["VULTR_INFERENCE_API_KEY"] = "synthetic-long-lived-not-used"
    with pytest.raises(CellError, match="brain_env requires exactly"):
        manager.create("skyvern", ["example.invalid"], skyvern=_skyvern_config(), brain_env=env)
    assert calls == []


def test_old_docker_cannot_publish_cell_ports(monkeypatch) -> None:
    calls = _fake_docker(monkeypatch)
    fake = cells_module._docker

    def old_engine(*args: str, **kwargs) -> subprocess.CompletedProcess[str]:
        if args[:2] == ("info", "--format"):
            calls.append(args)
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"ServerVersion": "27.5.1", "Runtimes": {"runsc": {}}}), ""
            )
        return fake(*args, **kwargs)

    monkeypatch.setattr(cells_module, "_docker", old_engine)
    with pytest.raises(CellError, match="28 or newer"):
        CellManager().create("native", ["example.invalid"])
    assert calls == [("info", "--format", "{{json .}}")]


def test_remote_host_commands_use_the_dedicated_docker_ssh_identity(monkeypatch) -> None:
    monkeypatch.setenv(
        "DOCKER_SSH_COMMAND",
        "ssh -i /synthetic/sandbox-key -o StrictHostKeyChecking=yes",
    )
    prefix = cells_module._ssh_prefix()
    assert prefix[:3] == ["ssh", "-i", "/synthetic/sandbox-key"]
    assert prefix[-2:] == ["-o", "StrictHostKeyChecking=yes"]
    monkeypatch.setenv("DOCKER_SSH_COMMAND", "ssh -o ProxyCommand=unsafe")
    with pytest.raises(CellError, match="unsupported"):
        cells_module._ssh_prefix()


def test_skyvern_firewall_failure_rolls_back_both_networks(monkeypatch) -> None:
    calls = _fake_docker(monkeypatch)
    monkeypatch.setenv("ONTOFILL_SANDBOX_DOCKER_HOST", "ssh://sandbox@100.64.0.3")
    monkeypatch.setattr(cells_module, "_ensure_image", lambda _image: None)
    monkeypatch.setattr(cells_module, "_container_uplink_ip", lambda _name, _network: "172.17.0.9")
    drop_added = False
    drop_removed = False

    def firewall(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        nonlocal drop_added, drop_removed
        if args[0] == "-I" and args[-1] == "DROP":
            drop_added = True
        if args[0] == "-I" and args[-1] == "ACCEPT":
            raise CellError("synthetic firewall rejection")
        if args[0] == "-D" and args[-1] == "DROP":
            drop_removed = True
        rc = 1 if args[0] == "-C" and drop_removed else 0
        return subprocess.CompletedProcess(args, rc, "", "")

    monkeypatch.setattr(cells_module, "_host_iptables", firewall)
    manager = CellManager()
    with pytest.raises(CellError, match="firewall rejection"):
        manager.create(
            "skyvern", ["example.invalid"], skyvern=_skyvern_config(), brain_env=_brain_env()
        )
    assert drop_added and drop_removed
    assert sum(call[:2] == ("network", "rm") for call in calls) == 3
    job = next(iter(manager._cells.values())).job_record
    assert job is not None and not job["checkpoints"]["task"]["ok"]


def test_loopback_http_endpoint_requires_token(monkeypatch) -> None:
    _fake_docker(monkeypatch)
    manager = CellManager()
    server = serve_cells(manager, token="synthetic-token", port=0)
    assert server.server_address[0] == "127.0.0.1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(base + "/cells", method="POST", data=b"{}"), timeout=2)
        assert denied.value.code == 401
        request = Request(
            base + "/cells",
            method="POST",
            data=json.dumps({"backend": "native", "allowed_domains": ["example.invalid"]}).encode(),
            headers={"Authorization": "Bearer synthetic-token", "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=2) as response:
            cell_id = json.load(response)["cell_id"]
        request = Request(
            base + f"/cells/{cell_id}",
            method="DELETE",
            headers={"Authorization": "Bearer synthetic-token"},
        )
        with urlopen(request, timeout=2) as response:
            assert json.load(response)["state"] == "destroyed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_http_skyvern_request_forwards_only_the_two_brain_env_fields(monkeypatch) -> None:
    manager = CellManager()
    received: list[tuple] = []

    def create(*args, **kwargs) -> dict:
        received.append((args, kwargs))
        return {
            "cell_id": "cell:synthetic",
            "cdp_url": "ws://127.0.0.1:1/devtools/browser/x",
            "brain_url": "http://127.0.0.1:2",
            "live_view_port": None,
        }

    monkeypatch.setattr(manager, "create", create)
    server = serve_cells(manager, token="synthetic-token", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body = {
            "backend": "skyvern",
            "allowed_domains": ["example.invalid"],
            "skyvern": _skyvern_config(),
            "brain_env": _brain_env(),
        }
        request = Request(
            f"http://127.0.0.1:{server.server_port}/cells",
            method="POST",
            data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer synthetic-token", "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=2) as response:
            assert "session-only-token-synthetic" not in response.read().decode()
        assert received[0][1]["brain_env"] == _brain_env()
        assert set(received[0][1]["brain_env"]) == {
            "OPENAI_COMPATIBLE_API_BASE",
            "OPENAI_COMPATIBLE_API_KEY",
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_session_token_is_not_placed_in_docker_argv_or_exception(monkeypatch) -> None:
    seen: list[tuple[list[str], dict]] = []

    def failed_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        seen.append((command, kwargs["env"].copy()))
        return subprocess.CompletedProcess(command, 1, "", "synthetic failure")

    monkeypatch.setattr(cells_module.subprocess, "run", failed_run)
    with pytest.raises(CellError) as error:
        cells_module._docker_with_session_token(
            "session-only-token-synthetic", "run", "-e", "OPENAI_API_KEY", "pinned:image"
        )
    assert "session-only-token-synthetic" not in str(error.value)
    assert "session-only-token-synthetic" not in " ".join(seen[0][0])
    assert seen[0][1]["OPENAI_API_KEY"] == "session-only-token-synthetic"
