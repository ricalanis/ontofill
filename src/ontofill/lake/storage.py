"""Identical object keys over a local directory or an S3 bucket."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import boto3
import yaml
from botocore.exceptions import ClientError

BRONZE_KEY = re.compile(r"sha256:[0-9a-f]{64}\Z")
GOLD_KEY = re.compile(r"gold/[A-Za-z0-9_.-]+/(?:[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+\Z")
RUN_KEY = re.compile(
    r"runs/[A-Za-z0-9_.-]+/(?:[A-Za-z0-9_.-]+/(?:trace\.live\.jsonl|jobs\.jsonl|status\.json)|latest\.json)\Z"
)


def validate_key(key: str) -> str:
    if not (BRONZE_KEY.fullmatch(key) or GOLD_KEY.fullmatch(key) or RUN_KEY.fullmatch(key)) or any(
        part in (".", "..") for part in key.split("/")
    ):
        raise ValueError(f"invalid lake object key: {key!r}")
    return key


def object_path(key: str) -> str:
    validate_key(key)
    return f"bronze/sha256/{key.removeprefix('sha256:')}" if key.startswith("sha256:") else key


def metadata_path(key: str) -> str:
    if not BRONZE_KEY.fullmatch(key):
        raise ValueError("metadata requires a bronze key")
    return f"{object_path(key)}.meta.json"


def metadata_bytes(metadata: dict[str, str] | None) -> bytes:
    provided = metadata or {}
    document = {
        "content_type": provided.get("content_type", "application/octet-stream"),
        "url": provided.get("url", ""),
        "captured_at": provided.get("captured_at", datetime.now(UTC).isoformat()),
        "source_id": provided.get("source_id", ""),
        "step_id": provided.get("step_id", ""),
    }
    return json.dumps(document, sort_keys=True).encode("utf-8")


class FileLake:
    def __init__(self, root: str | Path) -> None:
        if isinstance(root, str) and root.startswith("file://"):
            parsed = urlparse(root)
            if parsed.netloc not in ("", "localhost"):
                raise ValueError("file:// lake root must be local")
            root = unquote(parsed.path)
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, data: bytes, metadata: dict[str, str] | None = None) -> str:
        key = f"sha256:{hashlib.sha256(data).hexdigest()}"
        self.write_key(key, data)
        sidecar = self.root / metadata_path(key)
        if not sidecar.exists():
            sidecar.write_bytes(metadata_bytes(metadata))
        return key

    def write_key(self, key: str, data: bytes) -> None:
        path = self.root / object_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if BRONZE_KEY.fullmatch(key) and path.exists():
            if path.read_bytes() != data:
                raise ValueError("immutable bronze object hash collision")
            return
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(data)
        temporary.replace(path)

    def read_key(self, key: str) -> bytes:
        return (self.root / object_path(key)).read_bytes()

    def read_metadata(self, key: str) -> dict[str, str]:
        return json.loads((self.root / metadata_path(key)).read_text(encoding="utf-8"))

    def exists(self, key: str) -> bool:
        return (self.root / object_path(key)).exists()


class S3Lake:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
    ) -> None:
        self.bucket = bucket
        if not endpoint_url.startswith(("http://", "https://")):
            endpoint_url = f"https://{endpoint_url}"
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

    def put_bytes(self, data: bytes, metadata: dict[str, str] | None = None) -> str:
        key = f"sha256:{hashlib.sha256(data).hexdigest()}"
        self.write_key(key, data)
        self.client.put_object(
            Bucket=self.bucket,
            Key=metadata_path(key),
            Body=metadata_bytes(metadata),
            ContentType="application/json",
        )
        return key

    def write_key(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=object_path(key), Body=data)

    def read_key(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=object_path(key))["Body"].read()

    def read_metadata(self, key: str) -> dict[str, str]:
        data = self.client.get_object(Bucket=self.bucket, Key=metadata_path(key))["Body"].read()
        return json.loads(data)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=object_path(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True


def lake_for_case(case_dir: Path) -> FileLake | S3Lake:
    """Resolve the lake pointer; LAKE_ROOT deliberately overrides cloud config for local runs."""
    local_root = os.environ.get("LAKE_ROOT")
    if local_root:
        return FileLake(local_root)
    pointer = case_dir.resolve().parent / "lake.yaml"
    if not pointer.exists():
        raise FileNotFoundError(f"missing lake pointer: {pointer}")
    config = yaml.safe_load(pointer.read_text(encoding="utf-8"))
    bronze = config["bronze"]
    if bronze["kind"] == "file":
        return FileLake(bronze["root"])
    if bronze["kind"] != "s3":
        raise ValueError(f"unsupported bronze kind: {bronze['kind']}")
    endpoint = bronze.get("endpoint", "")
    bucket = bronze.get("bucket", "")
    access_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    if not all((endpoint, bucket, access_key, secret_key)) or bucket.startswith("<"):
        raise RuntimeError(
            "S3 lake configuration is incomplete; set LAKE_ROOT for local development"
        )
    return S3Lake(bucket, endpoint, access_key, secret_key)
