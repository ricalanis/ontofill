"""Remote Docker output transfer is tested without an actual VM."""

from __future__ import annotations

import subprocess

from ontofill.sandbox import capture


def test_remote_pod_copies_outputs_before_teardown(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ONTOFILL_SANDBOX_DOCKER_HOST", "ssh://root@100.64.0.2")
    calls: list[tuple[str, ...]] = []

    def fake_docker(*args, **_kwargs):
        calls.append(args)
        if args[0] == "wait":
            return subprocess.CompletedProcess(args, 0, "0\n", "")
        if args[0] == "cp":
            (tmp_path / "result.json").write_text('{"status": 200}')
        return subprocess.CompletedProcess(args, 0, "container-id\n", "")

    monkeypatch.setattr(capture, "_docker", fake_docker)
    mount_args = capture._pod_output_args(tmp_path)
    result = capture._run_agent_pod("synthetic-pod", tmp_path, *mount_args, "synthetic-image")
    assert result.returncode == 0
    assert (tmp_path / "result.json").exists()
    assert [row[0] for row in calls] == ["run", "exec", "cp", "exec", "wait", "logs"]
    assert "-v" not in calls[0]
    assert "--tmpfs" in calls[0]
    assert "--rm" not in calls[0]
    assert "CAPTURE_WAIT_FOR_COPY=1" in calls[0]
    assert calls[2][:2] == ("cp", "synthetic-pod:/out/.")
    assert calls[3][-2:] == ("touch", "/out/.copied")


def test_local_pod_keeps_bind_mount(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ONTOFILL_SANDBOX_DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    calls = []

    def fake_docker(*args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(capture, "_docker", fake_docker)
    args = capture._pod_output_args(tmp_path)
    capture._run_agent_pod("synthetic-pod", tmp_path, *args, "synthetic-image")
    assert calls[0][:4] == ("run", "--rm", "--name", "synthetic-pod")
    assert "-v" in calls[0]
    assert "--tmpfs" not in calls[0]


def test_remote_pod_failure_does_not_copy_unpublished_output(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ONTOFILL_SANDBOX_DOCKER_HOST", "ssh://root@100.64.0.2")
    calls = []

    def fake_docker(*args, **_kwargs):
        calls.append(args[0])
        if args[0] == "exec":
            return subprocess.CompletedProcess(args, 1, "", "")
        if args[0] == "inspect":
            return subprocess.CompletedProcess(args, 0, "false\n", "")
        if args[0] == "wait":
            return subprocess.CompletedProcess(args, 0, "23\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(capture, "_docker", fake_docker)
    result = capture._run_agent_pod("synthetic-pod", tmp_path, "synthetic-image")
    assert result.returncode == 23
    assert calls == ["run", "exec", "inspect", "wait", "logs"]
