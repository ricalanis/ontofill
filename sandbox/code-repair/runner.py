"""Pod entry point: run an extractor on captures, without test expectations or network access."""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import json
import sys
import traceback
from pathlib import Path


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
    try:
        payloads = json.loads(input_path.read_text(encoding="utf-8"))["captures"]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            spec = importlib.util.spec_from_file_location("candidate", candidate_path)
            if spec is None or spec.loader is None:
                raise RuntimeError("candidate code could not be loaded")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            extract = module.extract
            for encoded in payloads:
                rows = extract(base64.b64decode(encoded, validate=True))
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise TypeError("extract(capture: bytes) must return a list of dictionaries")
                outputs.append(rows)
    except Exception as exc:  # noqa: BLE001 - candidate code can raise any exception
        error = f"{type(exc).__name__}: {exc}"
        stderr.write(traceback.format_exc())
    try:
        output_path.write_text(
            json.dumps({"outputs": outputs, "stderr": stderr.getvalue(), "error": error}),
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write(f"runner result could not be written: {type(exc).__name__}\n")
        raise SystemExit(2) from exc


if __name__ == "__main__":
    run(Path("/work/input.json"), Path("/work/output.json"))
