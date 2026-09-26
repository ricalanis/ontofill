"""Synthetic browser-cell lifecycle checks; no Docker host is changed."""

from __future__ import annotations

import json
import subprocess
import threading
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
                args, 0, json.dumps({"Name": "synthetic", "Runtimes": {"runsc": {}}}), ""
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
    monkeypatch.setattr(cells_module, "_published_port", lambda _name: 49152)
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
    with pytest.raises(CellError, match="Skyvern"):
        manager.create("skyvern", ["example.invalid"])
    assert calls == []


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
