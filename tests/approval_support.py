"""Synthetic console-shaped review markers for engine tests."""

from __future__ import annotations

import hashlib
from pathlib import Path


def bind_approval(case_dir: Path, paths: list[str], marker: dict) -> dict:
    return {
        **marker,
        "identity_source": "local",
        "artifact_sha256": {
            relative: hashlib.sha256((case_dir / relative).read_bytes()).hexdigest()
            for relative in paths
        },
    }
