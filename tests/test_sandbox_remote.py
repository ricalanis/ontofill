"""Remote Docker output transfer is tested without an actual VM."""

from __future__ import annotations

import base64
import io
import subprocess
import sys
import zipfile

import pytest

from ontofill.sandbox import capture


def test_remote_fetch_output_archive_copies_bounded_payload(tmp_path) -> None:
    (tmp_path / "result.json").write_text('{"status": 200}')
    (tmp_path / "payload.bin").write_bytes(b"synthetic document")
    (tmp_path / "ignored.secret").write_text("must not leave the pod")
    script = capture._REMOTE_OUTPUT_SCRIPT.replace(
        "root = pathlib.Path('/out')", f"root = pathlib.Path({str(tmp_path)!r})"
    )
    encoded = subprocess.check_output([sys.executable, "-c", script], text=True)
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded))) as archive:
        assert sorted(archive.namelist()) == ["payload.bin", "result.json"]
        assert archive.read("payload.bin") == b"synthetic document"
    assert capture._REMOTE_OUTPUT_NAME.fullmatch("payload.bin")


def test_remote_pod_copies_outputs_before_teardown(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ONTOFILL_SANDBOX_DOCKER_HOST", "ssh://root@100.64.0.2")
    calls: list[tuple[str, ...]] = []

    def fake_docker(*args, **_kwargs):
        calls.append(args)
        if args[0] == "wait":
            return subprocess.CompletedProcess(args, 0, "0\n", "")
        if args[0] == "exec" and args[2] == "python":
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("result.json", '{"status": 200}')
                archive.writestr("page-0001.html", "<html>public</html>")
                archive.writestr("document.bin", b"%PDF synthetic")
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            return subprocess.CompletedProcess(args, 0, encoded, "")
        return subprocess.CompletedProcess(args, 0, "container-id\n", "")

    monkeypatch.setattr(capture, "_docker", fake_docker)
    mount_args = capture._pod_output_args(tmp_path)
    result = capture._run_agent_pod("synthetic-pod", tmp_path, *mount_args, "synthetic-image")
    assert result.returncode == 0
    assert (tmp_path / "result.json").exists()
    assert (tmp_path / "page-0001.html").read_text() == "<html>public</html>"
    assert (tmp_path / "document.bin").read_bytes() == b"%PDF synthetic"
    assert [row[0] for row in calls] == ["run", "exec", "exec", "exec", "wait", "logs"]
    assert "-v" not in calls[0]
    assert "--tmpfs" in calls[0]
    assert "--rm" not in calls[0]
    assert "CAPTURE_WAIT_FOR_COPY=1" in calls[0]
    assert calls[2][:4] == ("exec", "synthetic-pod", "python", "-c")
    assert calls[3][-2:] == ("touch", "/out/.copied")


def test_remote_output_rejects_archive_path_traversal(monkeypatch, tmp_path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("result.json", "{}")
        archive.writestr("../escape", "untrusted")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    monkeypatch.setattr(
        capture,
        "_docker",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, encoded, ""),
    )

    with pytest.raises(capture.CaptureError, match="invalid output filename"):
        capture._copy_remote_output("synthetic-pod", tmp_path, 30)
    assert not (tmp_path.parent / "escape").exists()


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
    assert calls[0][:3] == ("run", "--name", "synthetic-pod")
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
    assert calls == ["run", "exec", "inspect", "wait", "logs", "inspect"]
