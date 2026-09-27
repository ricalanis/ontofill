"""Read-only access to a case's lake: the run feed (runs/<case_id>/…) on a local directory or S3."""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml


class LocalLake:
    def __init__(self, root: Path):
        self.root = Path(root)

    def read(self, key: str) -> bytes | None:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            return None
        return p.read_bytes() if p.is_file() else None

    def list_dirs(self, prefix: str) -> list[str]:
        p = self.root / prefix
        return sorted(d.name for d in p.iterdir() if d.is_dir()) if p.is_dir() else []


class S3Lake:
    def __init__(self, bucket: str, endpoint: str | None, region: str | None):
        import boto3  # optional dependency: `uv sync --extra s3`

        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint, region_name=region)

    def read(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None

    def list_dirs(self, prefix: str) -> list[str]:
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix.rstrip("/") + "/", Delimiter="/")
        return sorted(cp["Prefix"].rstrip("/").rsplit("/", 1)[-1] for page in pages
                      for cp in page.get("CommonPrefixes", []))


class CaseLake:
    """The run feed of one case: latest run id, its status, and its trace (for spend)."""

    def __init__(self, backend, case_id: str | None):
        self.backend = backend
        self._case_id = case_id

    @property
    def case_id(self) -> str | None:
        if not self._case_id:
            ids = self.backend.list_dirs("runs")
            self._case_id = ids[0] if len(ids) == 1 else None
        return self._case_id

    def _json(self, key: str) -> dict | None:
        raw = self.backend.read(key)
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def latest_run_id(self) -> str | None:
        if not self.case_id:
            return None
        return (self._json(f"runs/{self.case_id}/latest.json") or {}).get("run_id")

    def active_run_id(self) -> str | None:
        """The run the runner should follow: the latest one, unless it has finished (done/failed/killed) while an
        older run of the same case is still paused at a checkpoint — e.g. a separate proof run was written into a
        scratch lake after the case paused. Then the most recently updated paused run."""
        latest = self.latest_run_id()
        st = (self.status(latest) if latest else None) or {}
        if latest and st.get("state") not in ("done", "failed", "killed", None):
            return latest
        paused = []
        for rid in self.run_ids():
            s = self.status(rid) or {}
            if s.get("state") == "paused" and s.get("checkpoint_pending"):
                paused.append((str(s.get("updated_at") or s.get("started_at") or ""), rid))
        return max(paused)[1] if paused else latest

    def run_ids(self) -> list[str]:
        return self.backend.list_dirs(f"runs/{self.case_id}") if self.case_id else []

    def status(self, run_id: str) -> dict | None:
        return self._json(f"runs/{self.case_id}/{run_id}/status.json") if self.case_id else None

    def trace_usd(self, run_id: str) -> float:
        raw = self.backend.read(f"runs/{self.case_id}/{run_id}/trace.live.jsonl") if self.case_id else None
        total = 0.0
        for line in (raw or b"").decode(errors="replace").splitlines():
            try:
                usage = (json.loads(line).get("usage") or {})
            except (ValueError, AttributeError):
                continue
            if isinstance(usage, dict):
                total += float(usage.get("est_usd") or 0)
        return total


def lake_for(case_dir: Path, explicit: Path | None, env: dict[str, str] | None = None) -> CaseLake:
    env = dict(os.environ if env is None else env)
    if explicit:
        return CaseLake(LocalLake(explicit), None)
    lake_path = Path(case_dir).resolve().parent / "lake.yaml"
    if not lake_path.is_file():
        raise FileNotFoundError(f"no lake.yaml next to {case_dir}")
    cfg = yaml.safe_load(lake_path.read_text()) or {}
    bronze = cfg.get("bronze") or {}
    case_id = cfg.get("case_id")
    if bronze.get("kind") in ("file", "local"):
        root = Path(os.path.expandvars(str(bronze.get("root") or bronze.get("path"))).removeprefix("file://"))
        return CaseLake(LocalLake(root if root.is_absolute() else lake_path.parent / root), case_id)
    if bronze.get("kind") == "s3":
        endpoint = env.get(bronze["endpoint_env"]) if bronze.get("endpoint_env") else bronze.get("endpoint")
        return CaseLake(S3Lake(bronze["bucket"], endpoint, bronze.get("region") or env.get("AWS_REGION")), case_id)
    raise ValueError(f"unsupported bronze kind in {lake_path}: {bronze.get('kind')!r}")
