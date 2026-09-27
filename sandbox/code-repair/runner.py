"""Pod entry point: run an extractor on captures, without test expectations or network access."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import json
import sys
import traceback
from pathlib import Path

_MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_BRONZE_DIGEST = set("0123456789abcdef")


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
    stderr = LimitedText()
    stdout = LimitedText()
    outputs: list[list[dict]] = []
    error = None
    verified_digests: list[str] = []
    input_source = "synthetic_bytes"
    capture_count = 0
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
                }
            ),
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write(f"runner result could not be written: {type(exc).__name__}\n")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    run(Path("/work/input.json"), Path("/work/output.json"))
