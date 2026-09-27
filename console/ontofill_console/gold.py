"""Read-only access to the gold export.

Layout (same for S3 and a local directory):

    gold/<case_id>/latest.json            {"run_id": ...}
    gold/<case_id>/<run_id>/entities.jsonl        (CONTRACT §11)
    gold/<case_id>/<run_id>/ontology.json
    gold/<case_id>/<run_id>/trace.jsonl
    gold/<case_id>/<run_id>/metrics.json

Bronze objects (screenshots, raw captures) are addressed by `sha256:<hex>` keys and live in the same bucket
under `bronze_key_template` (default `bronze/sha256/{hex}`).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Protocol

import yaml

from .domain import Domain

DEFAULT_BRONZE_TEMPLATE = "bronze/sha256/{hex}"


class Source(Protocol):
    def read(self, key: str) -> bytes | None: ...
    def list_dirs(self, prefix: str) -> list[str]: ...


class LocalSource:
    """A directory laid out like the bucket (fixtures, cached fallback run)."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"key escapes root: {key}")
        return path

    def read(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def list_dirs(self, prefix: str) -> list[str]:
        path = self._path(prefix)
        return sorted(p.name for p in path.iterdir() if p.is_dir()) if path.is_dir() else []

    def __repr__(self) -> str:
        return f"LocalSource({self.root})"


class S3Source:
    """Any S3 API (Vultr Object Storage, MinIO). Credentials come from the standard AWS_* env vars."""

    def __init__(self, bucket: str, endpoint: str | None = None, region: str | None = None):
        import boto3  # optional dependency: `uv sync --extra s3`

        if endpoint and not endpoint.startswith("http"):
            endpoint = f"https://{endpoint}"
        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint, region_name=region)

    def read(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None

    def list_dirs(self, prefix: str) -> list[str]:
        prefix = prefix.rstrip("/") + "/"
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix, Delimiter="/"
        )
        return sorted(
            cp["Prefix"][len(prefix) :].rstrip("/") for page in pages for cp in page.get("CommonPrefixes", [])
        )

    def __repr__(self) -> str:
        return f"S3Source({self.bucket})"


def _jsonl(raw: bytes | None) -> list[dict]:
    if not raw:
        return []
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]


@dataclass
class ValueRef:
    """One property value of one entity, indexed by its value_id."""

    value_id: str
    entity_id: str
    prop: str
    data: dict


def inferred_ontology(entities: list[dict]) -> dict:
    """Last resort when an export ships entities without an ontology: most common class is primary."""
    counts: dict[str, int] = {}
    props: dict[str, dict[str, None]] = {}
    for e in entities:
        counts[e.get("class") or "entity"] = counts.get(e.get("class") or "entity", 0) + 1
        props.setdefault(e.get("class") or "entity", {}).update(dict.fromkeys(e.get("properties") or {}))
    primary = max(counts, key=counts.get) if counts else "entity"
    return {"primary_class": primary, "classes": [{"id": c} for c in counts],
            "properties": [{"id": p, "domain": c} for c, ps in props.items() for p in ps]}


@dataclass
class Run:
    """One gold export run, fully loaded and indexed. Entities follow CONTRACT §11; `domain` explains them."""

    case_id: str
    run_id: str
    entities: list[dict]
    trace: list[dict]
    metrics: dict
    domain: Domain
    layout: str = "entities"
    dod_queries: list[dict] = field(default_factory=list)  # declarative DoD queries (dod-queries.json), if any
    entities_by_id: dict[str, dict] = field(default_factory=dict)
    values: dict[str, ValueRef] = field(default_factory=dict)
    steps_by_id: dict[str, dict] = field(default_factory=dict)
    steps_by_value: dict[str, list[dict]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.entities_by_id = {e["id"]: e for e in self.entities}
        for e in self.entities:
            for name, f in (e.get("properties") or {}).items():
                if isinstance(f, dict) and f.get("value_id"):
                    self.values[f["value_id"]] = ValueRef(f["value_id"], e["id"], name, f)
        for step in self.trace:
            self.steps_by_id[step["step_id"]] = step
            for vid in step.get("value_ids") or []:
                self.steps_by_value.setdefault(vid, []).append(step)

    @property
    def primary(self) -> list[dict]:
        """Entities of the ontology's primary class: the ones a dossier is about."""
        return [e for e in self.entities if e.get("class") == self.domain.primary_class]

    def title(self, entity: dict | None) -> str:
        if not entity:
            return "Unknown"
        prop = self.domain.title_property(entity.get("class"))
        f = (entity.get("properties") or {}).get(prop or "") or {}
        return str(f.get("value")) if f.get("value") not in (None, "") else entity["id"]

    def identifier(self, entity: dict | None) -> str | None:
        prop = self.domain.identifier_property((entity or {}).get("class"))
        f = ((entity or {}).get("properties") or {}).get(prop or "") or {}
        return f.get("value")

    @cached_property
    def inference_backend(self) -> str | None:
        """'recorded' if anything in the run came from the recorded inference double (CONTRACT section 7)."""
        return backend_of(self.metrics, self.trace, self.values.values())

    def lineage(self, value_id: str) -> list[list[dict]]:
        """One chain per step that produced the value: that step, then its parent_step_id ancestors."""
        chains = []
        for step in self.steps_by_value.get(value_id, []):
            chain, seen, cur = [], set(), step
            while cur and cur["step_id"] not in seen:  # guard against cycles in a malformed trace
                seen.add(cur["step_id"])
                chain.append(cur)
                cur = self.steps_by_id.get(cur.get("parent_step_id") or "")
            chains.append(chain)
        return chains


def backend_of(metrics: dict | None, steps: Iterable[dict] = (), values: Iterable = ()) -> str | None:
    """Worst-case inference backend: any recorded artifact makes the whole thing 'recorded'."""
    seen = set()
    if (metrics or {}).get("inference_backend"):
        seen.add(metrics["inference_backend"])
    for s in steps:
        seen.add((s.get("generated_by") or {}).get("backend"))
    for v in values:
        data = v.data if hasattr(v, "data") else v
        seen.add((data.get("generated_by") or {}).get("backend"))
    seen.discard(None)
    for backend in ("recorded", "vultr", "jev"):  # recorded taints everything; jev alone is a supporting backend
        if backend in seen:
            return backend
    return None


class GoldStore:
    """Gold export for one case. Lookups that cannot be answered yet raise LookupError."""

    def __init__(
        self,
        source: Source,
        case_id: str | None = None,
        bronze_key_template: str = DEFAULT_BRONZE_TEMPLATE,
    ):
        self.source = source
        self.bronze_key_template = bronze_key_template
        self._case_id = case_id
        self._cache: dict[str, Run] = {}
        self.case_dir: Path | None = None  # set by the app: the case's approved ontology describes the entities
        self.pinned_run_id: str | None = None  # serve this run even without latest.json (mock runs)

    @property
    def case_id(self) -> str:
        if not self._case_id:  # resolved lazily: the bucket may hold no gold yet when the app starts
            self._case_id = self._detect_case_id()
        return self._case_id

    def _detect_case_id(self) -> str:
        cases = self.source.list_dirs("gold")
        if not cases:  # no gold published yet: a case paused at a checkpoint still has its run feed under runs/
            cases = self.source.list_dirs("runs")
        if len(cases) != 1:
            raise LookupError(f"set case_id in lake.yaml: found {len(cases)} cases under gold/ or runs/ in {self.source!r}")
        return cases[0]

    @property
    def prefix(self) -> str:
        return f"gold/{self.case_id}"

    def latest_run_id(self) -> str | None:
        if self.pinned_run_id:
            return self.pinned_run_id
        raw = self.source.read(f"{self.prefix}/latest.json")
        return json.loads(raw)["run_id"] if raw else None

    def run_ids(self) -> list[str]:
        return self.source.list_dirs(self.prefix)

    def run(self, run_id: str | None = None) -> Run:
        run_id = run_id or self.latest_run_id()
        if not run_id:
            raise LookupError(f"no latest.json under {self.prefix} in {self.source!r}")
        if run_id not in self._cache:
            base = f"{self.prefix}/{run_id}"
            metrics_raw = self.source.read(f"{base}/metrics.json")
            entities = _jsonl(self.source.read(f"{base}/entities.jsonl"))
            layout = "entities"
            domain = self._domain(base, entities)
            queries = self._dod_queries(base)
            for q in queries:  # §12: the per-entity threshold lives in the DoD query itself
                if q.get("aggregate") == "entities_meeting_completeness" and q.get("min_ratio") is not None:
                    domain.dod_threshold, domain.threshold_stated = float(q["min_ratio"]), True
            self._cache[run_id] = Run(
                case_id=self.case_id,
                run_id=run_id,
                entities=entities,
                trace=_jsonl(self.source.read(f"{base}/trace.jsonl")),
                metrics=json.loads(metrics_raw) if metrics_raw else {},
                domain=domain,
                layout=layout,
                dod_queries=queries,
            )
        return self._cache[run_id]

    def _domain(self, base: str, entities: list[dict]) -> Domain:
        """Run's own ontology.json, else the case's approved ontology, else inferred from the entities."""
        for raw in (self.source.read(f"{base}/ontology.json"), self._case_ontology()):
            try:
                onto = json.loads(raw) if raw else None
            except ValueError:
                onto = None
            if isinstance(onto, dict) and (onto.get("primary_class") or any(
                    isinstance(c, dict) and c.get("primary") for c in onto.get("classes") or [])):
                return Domain.from_ontology(onto)
        return Domain.from_ontology(inferred_ontology(entities))

    def _dod_queries(self, base: str) -> list[dict]:
        """Run's dod-queries.json, else the case's (02-ontology/dod-queries.json)."""
        raws = [self.source.read(f"{base}/dod-queries.json")]
        if self.case_dir:
            path = Path(self.case_dir) / "02-ontology" / "dod-queries.json"
            raws.append(path.read_bytes() if path.is_file() else None)
        for raw in raws:
            try:
                doc = json.loads(raw) if raw else None
            except ValueError:
                doc = None
            if isinstance(doc, dict) and isinstance(doc.get("queries"), list):
                return doc["queries"]
        return []

    def _case_ontology(self) -> bytes | None:
        if not self.case_dir:
            return None
        path = Path(self.case_dir) / "02-ontology" / "ontology.json"
        return path.read_bytes() if path.is_file() else None

    def refresh(self) -> None:
        self._cache.clear()

    def _bronze_path(self, key: str) -> str | None:
        algo, _, hexdigest = key.partition(":")
        if algo != "sha256" or not hexdigest or not all(c in "0123456789abcdef" for c in hexdigest):
            return None
        return self.bronze_key_template.format(hex=hexdigest)

    # Live run feed (CONTRACT section 4b): runs/<case_id>/latest.json, <run_id>/status.json, trace.live.jsonl

    def live_run_id(self) -> str | None:
        if self.pinned_run_id:
            return self.pinned_run_id
        raw = self.source.read(f"runs/{self.case_id}/latest.json")
        return json.loads(raw)["run_id"] if raw else None

    def live_run_ids(self) -> list[str]:
        return self.source.list_dirs(f"runs/{self.case_id}")

    def live_status(self, run_id: str) -> dict | None:
        raw = self.source.read(f"runs/{self.case_id}/{run_id}/status.json")
        try:
            return json.loads(raw) if raw else None
        except ValueError:  # caught mid-rewrite; the next poll gets it
            return None

    def live_steps(self, run_id: str) -> list[dict]:
        raw = self.source.read(f"runs/{self.case_id}/{run_id}/trace.live.jsonl")
        steps = []
        for line in (raw or b"").decode("utf-8", "replace").splitlines():
            try:
                steps.append(json.loads(line))
            except ValueError:  # a line still being appended
                break
        return steps

    def live_jobs(self, run_id: str) -> list[dict]:
        """Sandbox jobs with their proof checkpoints (section 8); the last record per job_id wins."""
        raw = self.source.read(f"runs/{self.case_id}/{run_id}/jobs.jsonl")
        jobs: dict[str, dict] = {}
        for line in (raw or b"").decode("utf-8", "replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                break
            if rec.get("job_id"):
                jobs[rec["job_id"]] = {**jobs.get(rec["job_id"], {}), **rec}
        return list(jobs.values())

    def bronze(self, key: str) -> bytes | None:
        path = self._bronze_path(key)
        return self.source.read(path) if path else None

    def bronze_meta(self, key: str) -> dict:
        """Sidecar `<object>.meta.json`: {content_type, url, captured_at, source_id, step_id} (CONTRACT section 3)."""
        path = self._bronze_path(key)
        raw = self.source.read(f"{path}.meta.json") if path else None
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}


def sniff_media_type(data: bytes) -> str:
    head = data[:512].lstrip()
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return "image/svg+xml"
    if head[:1] in (b"{", b"["):
        return "application/json"
    if head[:1] == b"<":
        return "text/plain; charset=utf-8"  # captured HTML is shown as text, never rendered in our origin
    return "application/octet-stream"


def store_for_case(case_dir: Path, lake: str | None = None, env: dict[str, str] | None = None) -> GoldStore:
    """The lake of one registered case: an explicit local lake directory, else `<case parent>/lake.yaml`
    (bronze kind file|local or s3, the engine's lake pointer). The store's case_dir is set to the case."""
    env = dict(os.environ if env is None else env)
    case_dir = Path(case_dir)
    if lake:
        store = GoldStore(LocalSource(lake), None, env.get("ONTOFILL_CONSOLE_BRONZE_KEY_TEMPLATE", DEFAULT_BRONZE_TEMPLATE))
    else:
        store = _store_from_lake_yaml(case_dir.resolve().parent / "lake.yaml", env)
    store.case_dir = case_dir
    return store


def _store_from_lake_yaml(lake_path: Path, env: dict[str, str]) -> GoldStore:
    template = env.get("ONTOFILL_CONSOLE_BRONZE_KEY_TEMPLATE", DEFAULT_BRONZE_TEMPLATE)
    if not lake_path.is_file():
        raise FileNotFoundError("no lake.yaml next to this case yet (or register it as id=case:lake)")
    lake = yaml.safe_load(lake_path.read_text()) or {}
    bronze = lake.get("bronze") or {}
    case_id = lake.get("case_id")
    template = bronze.get("key_template", template)
    if bronze.get("kind") in ("file", "local"):
        raw_root = os.path.expandvars(str(bronze.get("root") or bronze.get("path")))
        root = Path(raw_root.removeprefix("file://"))
        return GoldStore(LocalSource(root if root.is_absolute() else lake_path.parent / root), case_id, template)
    if bronze.get("kind") != "s3":
        raise ValueError(f"unsupported bronze kind in {lake_path}: {bronze.get('kind')!r}")
    endpoint = env.get(bronze["endpoint_env"]) if bronze.get("endpoint_env") else bronze.get("endpoint")
    source = S3Source(bronze["bucket"], endpoint, bronze.get("region") or env.get("AWS_REGION"))
    return GoldStore(source, case_id, template)


def iter_evidence(field_data: dict) -> Iterable[dict]:
    return field_data.get("evidence") or []


class UnavailableStore:
    """Stands in when a case has no reachable lake yet (its approvals must still work)."""

    def __init__(self, reason: str):
        self.reason = reason
        self.case_id = "no-case"

    def run(self, run_id: str | None = None) -> Run:
        raise LookupError(self.reason)

    def run_ids(self) -> list[str]:
        return []

    def refresh(self) -> None:
        pass

    def bronze(self, key: str) -> bytes | None:
        return None

    def live_run_id(self) -> str | None:
        return None

    def live_run_ids(self) -> list[str]:
        return []

    def live_status(self, run_id: str) -> dict | None:
        return None

    def live_steps(self, run_id: str) -> list[dict]:
        return []

    def live_jobs(self, run_id: str) -> list[dict]:
        return []

    def bronze_meta(self, key: str) -> dict:
        return {}
