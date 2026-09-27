"""`ontofill-runner bronze-probe`: one sample of each case's bronze (raw captures) for progress watching.

The run trace can miss capture writes (R52), so the console's "moving but not progressing" alert also reads bronze
growth. One JSON line per sample is appended to <state_dir>/bronze-probe.jsonl (the console mounts the state dir
read-only):

    {"ts": "...Z", "objects": 1244, "newest": "...Z", "prefix": "bronze/", "cases": {"<case_id>": 1244}}

`objects`/`newest` cover each distinct lake once; `cases` gives each case's own lake. Read-only on the lakes."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from .config import Config, load_registry
from .lake import LocalLake, S3Lake, lake_for

PREFIX = "bronze/"
KEEP_LINES = 1500  # ~2 days at one sample every 2 minutes


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def count_bronze(backend) -> tuple[int, datetime | None]:
    """(object count, newest write) under bronze/ of one lake backend."""
    if isinstance(backend, S3Lake):
        n, newest = 0, None
        for page in backend.client.get_paginator("list_objects_v2").paginate(Bucket=backend.bucket, Prefix=PREFIX):
            for obj in page.get("Contents", []):
                n += 1
                if newest is None or obj["LastModified"] > newest:
                    newest = obj["LastModified"]
        return n, newest
    if isinstance(backend, LocalLake):
        root = backend.root / PREFIX
        n, newest = 0, None
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                n += 1
                mt = datetime.fromtimestamp((Path(dirpath) / name).stat().st_mtime, UTC)
                newest = mt if newest is None or mt > newest else newest
        return n, newest
    return 0, None


def _lake_key(backend) -> str:
    return f"s3:{backend.bucket}" if isinstance(backend, S3Lake) else f"file:{getattr(backend, 'root', '')}"


def sample(cfg: Config, now: datetime | None = None) -> dict:
    cases = dict(cfg.cases)
    if cfg.cases_root:
        try:
            cases.update(load_registry(cfg.cases_root)[0])
        except (OSError, ValueError):
            pass
    per_case: dict[str, int] = {}
    seen: dict[str, tuple[int, datetime | None]] = {}
    for cid, spec in sorted(cases.items()):
        try:
            backend = lake_for(spec.case_dir, spec.lake).backend
            key = _lake_key(backend)
            if key not in seen:
                seen[key] = count_bronze(backend)
            per_case[cid] = seen[key][0]
        except Exception:  # noqa: BLE001 - one unreadable lake must not stop the sample
            continue
    newest = max((dt for _, dt in seen.values() if dt), default=None)
    return {
        "ts": _iso(now or datetime.now(UTC)),
        "objects": sum(n for n, _ in seen.values()),
        "newest": _iso(newest),
        "prefix": PREFIX,
        "cases": per_case,
    }


def append(state_dir: Path, line: dict) -> Path:
    path = Path(state_dir) / "bronze-probe.jsonl"
    lines = path.read_text().splitlines()[-(KEEP_LINES - 1) :] if path.is_file() else []
    lines.append(json.dumps(line, separators=(",", ":")))
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return path
