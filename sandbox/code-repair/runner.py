"""Pod entry point: run an extractor on captures, without test expectations or network access."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import json
import os
import socket
import sys
import traceback
from pathlib import Path

_MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_BRONZE_DIGEST = set("0123456789abcdef")
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def _probe_socket(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return False
    except OSError:
        return True


def _probe_write(path: Path) -> bool:
    try:
        with path.open("xb") as stream:
            stream.write(b"probe")
        path.unlink(missing_ok=True)
    except OSError:
        return True
    return False


def _files_with_secret_names(
    roots: tuple[Path, ...] = (Path("/run/secrets"), Path("/root"), Path("/home"), Path("/work")),
) -> int:
    """Count bounded secret-named files without opening or exposing their contents."""
    count = 0
    visited = 0
    for root in roots:
        if not root.exists():
            continue
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if not Path(directory, name).is_symlink()]
            for name in files:
                visited += 1
                if visited > 1000:
                    return count
                if any(marker.lower() in name.lower() for marker in _SECRET_MARKERS):
                    count += 1
    return count


def _proof() -> dict:
    """Measure this pod's isolation exactly as the parse pod does."""
    env_keys = sum(any(marker in name.upper() for marker in _SECRET_MARKERS) for name in os.environ)
    metadata_blocked = _probe_socket("169.254.169.254", 80)
    mesh_blocked = _probe_socket("100.64.0.1", 22)
    external_blocked = _probe_socket("1.1.1.1", 53)
    etc_write_blocked = _probe_write(Path("/etc/.ontofill-write-probe"))
    uname = os.uname()
    flags: list[str] = []
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("flags"):
                flags = sorted(set(line.split(":", 1)[-1].split()))
                break
    except OSError:
        pass
    probes = [
        {
            "probe": "network_non_allowlisted",
            "blocked": external_blocked,
            "target": "1.1.1.1:53",
        },
        {
            "probe": "write_outside_pod",
            "blocked": _probe_write(Path("/host/.ontofill-write-probe")),
            "target": "/host/.ontofill-write-probe",
        },
        {
            "probe": "write_outside_writable_mount",
            "blocked": etc_write_blocked,
            "target": "/etc/.ontofill-write-probe",
        },
        {
            "probe": "no_host_mounts",
            "blocked": not Path("/host").exists(),
            "target": "/host",
        },
    ]
    secrets = {
        "env_keys_found": env_keys,
        "files_with_keys": _files_with_secret_names(),
        "metadata_ip": "BLOCKED" if metadata_blocked else "ALLOWED",
        "mesh": "BLOCKED" if mesh_blocked else "ALLOWED",
    }
    secrets["ok"] = (
        secrets["env_keys_found"] == 0
        and secrets["files_with_keys"] == 0
        and secrets["metadata_ip"] == "BLOCKED"
        and secrets["mesh"] == "BLOCKED"
    )
    return {
        "pod": {
            "hostname": socket.gethostname(),
            "uname": {"system": uname.sysname, "release": uname.release, "machine": uname.machine},
            "cpu_virtualization_flags": flags,
            "dev_kvm_present": Path("/dev/kvm").exists(),
        },
        "isolation": {"probes": probes},
        "secrets": secrets,
        "network_probes": {
            "external": external_blocked,
            "metadata_ip": metadata_blocked,
            "mesh": mesh_blocked,
        },
    }


def _peak_memory_mb() -> float:
    import resource

    # Linux reports ru_maxrss in KiB.
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3)


class LimitedText:
    def __init__(self, limit: int = 8192) -> None:
        self.limit = limit
        self.parts: list[str] = []
        self.length = 0

    def write(self, value: str) -> int:
        available = max(0, self.limit - self.length)
        if available:
            self.parts.append(value[:available])
            self.length += min(available, len(value))
        return len(value)

    def flush(self) -> None:
        pass

    def getvalue(self) -> str:
        return "".join(self.parts)


def run(
    input_path: Path, output_path: Path, candidate_path: Path = Path("/work/candidate.py")
) -> None:
    import time

    started = time.monotonic()
    stderr = LimitedText()
    stdout = LimitedText()
    outputs: list[list[dict]] = []
    error = None
    verified_digests: list[str] = []
    input_source = "synthetic_bytes"
    capture_count = 0
    proof: dict = {}
    try:
        payloads = json.loads(input_path.read_text(encoding="utf-8"))["captures"]
        if not isinstance(payloads, list) or not payloads:
            raise ValueError("repair requires captured bronze inputs")
        capture_count = len(payloads)
        captures = []
        total_bytes = 0
        for index, reference in enumerate(payloads):
            if isinstance(reference, str):
                payload = base64.b64decode(reference, validate=True)
            elif isinstance(reference, dict):
                if set(reference) != {"path", "expected_sha256", "max_bytes"}:
                    raise ValueError("invalid bronze staging descriptor")
                path = reference["path"]
                digest = reference["expected_sha256"]
                maximum = reference["max_bytes"]
                expected_path = f"/work/capture-{index}"
                if (
                    path != expected_path
                    or not isinstance(digest, str)
                    or len(digest) != 64
                    or any(char not in _BRONZE_DIGEST for char in digest)
                    or type(maximum) is not int
                    or not 1 <= maximum <= _MAX_CAPTURE_BYTES
                ):
                    raise ValueError("invalid bronze staging descriptor")
                try:
                    with Path(path).open("rb") as stream:
                        payload = stream.read(maximum + 1)
                except OSError as exc:
                    raise ValueError("staged bronze input could not be read") from exc
                if len(payload) > maximum:
                    raise ValueError("staged bronze input exceeds its size limit")
                actual = hashlib.sha256(payload).hexdigest()
                if actual != digest:
                    raise ValueError("staged bronze digest does not match its key")
                verified_digests.append(actual)
                input_source = "lake_bronze"
            else:
                raise TypeError("invalid repair capture input")
            total_bytes += len(payload)
            if len(payload) > _MAX_CAPTURE_BYTES or total_bytes > _MAX_CAPTURE_BYTES:
                raise ValueError("repair captures exceed 8 MiB")
            captures.append(payload)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            spec = importlib.util.spec_from_file_location("candidate", candidate_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("candidate code could not be loaded")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            extract = module.extract
            for payload in captures:
                rows = extract(payload)
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise TypeError("extract(capture: bytes) must return a list of dictionaries")
                outputs.append(rows)
    except Exception as exc:  # noqa: BLE001 - candidate code can raise any exception
        error = f"{type(exc).__name__}: {exc}"
        stderr.write(traceback.format_exc())
    finally:
        try:
            proof = _proof()
        except Exception:  # noqa: BLE001 - emit an honest failed proof
            proof = {}
    try:
        output_path.write_text(
            json.dumps(
                {
                    "outputs": outputs,
                    "stderr": stderr.getvalue(),
                    "error": error,
                    "input_proof": {
                        "source": input_source,
                        "digests_verified": verified_digests,
                        "capture_count": capture_count,
                    },
                    "proof": proof,
                    "usage": {
                        "peak_memory_mb": _peak_memory_mb(),
                        "wall_s": round(time.monotonic() - started, 3),
                        "steps": 1,
                    },
                }
            ),
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write(f"runner result could not be written: {type(exc).__name__}\n")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    run(Path("/work/input.json"), Path("/work/output.json"))
