"""Typed, safe, resumable state for Phase 3 discovery.

The checkpoint stores references to bronze captures, never captured page text. A caller
must verify the referenced bronze object and its metadata on the current run before
passing the `(bronze_key, metadata_digest)` pair to :func:`reuse`, then emit a new-run
`reused from checkpoint` trace step. Checkpoint code does not copy prior run traces.
"""

from __future__ import annotations

import errno
import hashlib
import ipaddress
import json
import os
import re
import tempfile
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from jsonschema import ValidationError

from ontofill.contracts import validate_document
from ontofill.phases.p3_fanout.leads import public_url

SCHEMA_VERSION = 1
MAX_CHECKPOINT_BYTES = 8 * 1024 * 1024
CAPTURE_TTL = timedelta(hours=6)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BRONZE_KEY = re.compile(r"^sha256:[0-9a-f]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:/-]{1,240}$")
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_AUTH_QUERY_KEY = re.compile(
    r"(?i)(?:api[_-]?key|token|secret|password|credential|authorization|bearer|"
    r"session|csrf|sig(?:nature)?|access[_-]?key|auth|jwt|sas)"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:api[_-]?key|token|secret|password|credential|authorization|bearer|"
    r"session|csrf|signature|access[_-]?key|auth|jwt|sas)\s*[:=]\s*\S+"
)
_CHECKPOINT_NAME = "p3-checkpoint"


def checkpoint_path(case_dir: Path) -> Path:
    """Return the durable checkpoint path within a case package."""
    return Path(case_dir) / "03-fanout" / "cache" / "p3-checkpoint.json"


def content_digest(content: bytes | str) -> str:
    """Return a lowercase SHA-256 digest for an input artifact's exact bytes."""
    payload = content.encode("utf-8") if isinstance(content, str) else content
    if not isinstance(payload, bytes):
        raise TypeError("checkpoint content must be bytes or text")
    return hashlib.sha256(payload).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return _utc(value, "timestamp").isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date-time") from exc
    return _utc(parsed, field)


def _bounded_text(value: str, field: str, maximum: int, *, single_line: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    clean = value.strip()
    if not clean or len(clean) > maximum:
        raise ValueError(f"{field} must contain 1 to {maximum} characters")
    if single_line and ("\n" in clean or "\r" in clean):
        raise ValueError(f"{field} must be a single line")
    if _SECRET_ASSIGNMENT.search(clean):
        raise ValueError(f"{field} may not contain credential assignments")
    return clean


def _public_dns_host(value: str) -> bool:
    """Reject IP literals, local names, and malformed host labels before reuse."""
    host = value.casefold().rstrip(".")
    if not host or len(host) > 253:
        return False
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return False
    labels = host.split(".")
    if len(labels) < 2 or labels[-1].isdigit():
        return False
    if labels[-1] in {"arpa", "internal", "invalid", "local", "localhost", "metadata"}:
        return False
    return all(
        1 <= len(label) <= 63
        and label[0].isalnum()
        and label[-1].isalnum()
        and all(char.isalnum() or char == "-" for char in label)
        for label in labels
    )


def _url_identity(value: str) -> tuple[str, str]:
    """Return a query-free URL and a digest of the full runtime URL identity."""
    raw = _bounded_text(value, "url", 2048, single_line=True)
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("url is malformed") from exc
    if not public_url(raw) or not parsed.hostname or not _public_dns_host(parsed.hostname):
        raise ValueError("url must have a public DNS host and use HTTP(S)")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("url may not contain user credentials")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("url port must be between 1 and 65535")
    if any(
        _AUTH_QUERY_KEY.search(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        raise ValueError("url may not contain credential query parameters")

    host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    netloc = host if port is None or default_port else f"{host}:{port}"
    canonical = urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", "", ""))
    runtime = urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))
    return canonical, hashlib.sha256(runtime.encode("utf-8")).hexdigest()


def normalize_url(value: str) -> str:
    """Return a canonical URL without query values or fragments."""
    return _url_identity(value)[0]


def url_digest(value: str) -> str:
    """Digest the full HTTP(S) URL identity without retaining query values."""
    return _url_identity(value)[1]


def _source_identity(value: str) -> str:
    return _bounded_text(value, "source_identity", 512, single_line=True)


def _digest(value: str, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _bronze_key(value: str, field: str = "bronze_key") -> str:
    if not isinstance(value, str) or not _BRONZE_KEY.fullmatch(value):
        raise ValueError(f"{field} must be a SHA-256 bronze key")
    return value


def _version(value: str, field: str) -> str:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ValueError(f"{field} must be a short version identifier")
    return value


def _reason_code(value: str, field: str = "reason_code") -> str:
    if not isinstance(value, str) or not _REASON_CODE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase reason code")
    return value


@dataclass(frozen=True)
class CheckpointInputs:
    """Content fingerprints and code versions that govern resumable P3 state."""

    prd_digest: str
    ontology_digest: str
    authority_digest: str
    approvals_digest: str
    p3_algorithm_version: str
    critic_version: str

    def __post_init__(self) -> None:
        for name in ("prd_digest", "ontology_digest", "authority_digest", "approvals_digest"):
            _digest(getattr(self, name), name)
        _version(self.p3_algorithm_version, "p3_algorithm_version")
        _version(self.critic_version, "critic_version")

    @classmethod
    def from_content(
        cls,
        *,
        prd: bytes | str,
        ontology: bytes | str,
        authority: bytes | str,
        approvals: bytes | str,
        p3_algorithm_version: str,
        critic_version: str,
    ) -> CheckpointInputs:
        """Fingerprint exact input bytes without including run IDs or budget settings."""
        return cls(
            prd_digest=content_digest(prd),
            ontology_digest=content_digest(ontology),
            authority_digest=content_digest(authority),
            approvals_digest=content_digest(approvals),
            p3_algorithm_version=p3_algorithm_version,
            critic_version=critic_version,
        )


@dataclass(frozen=True)
class QueryRun:
    query: str
    provider: str
    iteration: int
    status: Literal["completed", "unavailable", "error"] = "completed"

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", _bounded_text(self.query, "query", 500, single_line=True))
        object.__setattr__(self, "provider", _bounded_text(self.provider, "provider", 100))
        if not isinstance(self.iteration, int) or self.iteration < 1:
            raise ValueError("query iteration must be a positive integer")
        if self.status not in {"completed", "unavailable", "error"}:
            raise ValueError("query status is unsupported")


@dataclass(frozen=True)
class Lead:
    query: str
    provider: str
    url: str
    source_identity: str
    url_digest: str | None = None
    property_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", _bounded_text(self.query, "query", 500, single_line=True))
        object.__setattr__(self, "provider", _bounded_text(self.provider, "provider", 100))
        safe_url, computed_digest = _url_identity(self.url)
        object.__setattr__(self, "url", safe_url)
        object.__setattr__(
            self,
            "url_digest",
            _digest(self.url_digest, "url_digest")
            if self.url_digest is not None
            else computed_digest,
        )
        object.__setattr__(self, "source_identity", _source_identity(self.source_identity))
        property_ids = tuple(
            _bounded_text(item, "property_id", 240, single_line=True) for item in self.property_ids
        )
        if any(not _IDENTIFIER.fullmatch(item) for item in property_ids):
            raise ValueError("property IDs must be stable identifiers")
        object.__setattr__(self, "property_ids", tuple(dict.fromkeys(property_ids)))


@dataclass(frozen=True)
class CaptureRecord:
    url: str
    source_identity: str
    bronze_key: str
    metadata_digest: str
    captured_at: datetime
    url_digest: str | None = None

    def __post_init__(self) -> None:
        safe_url, computed_digest = _url_identity(self.url)
        object.__setattr__(self, "url", safe_url)
        object.__setattr__(
            self,
            "url_digest",
            _digest(self.url_digest, "url_digest")
            if self.url_digest is not None
            else computed_digest,
        )
        object.__setattr__(self, "source_identity", _source_identity(self.source_identity))
        _bronze_key(self.bronze_key)
        _digest(self.metadata_digest, "metadata_digest")
        object.__setattr__(self, "captured_at", _utc(self.captured_at, "captured_at"))


@dataclass(frozen=True)
class CriticVerdict:
    """Normalized critic outcome keyed by a bronze digest and critic version."""

    capture_digest: str
    critic_version: str
    decision: Literal["accepted", "rejected", "needs_review"]
    capability_ids: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _bronze_key(self.capture_digest, "capture_digest")
        _version(self.critic_version, "critic_version")
        if self.decision not in {"accepted", "rejected", "needs_review"}:
            raise ValueError("critic decision is unsupported")
        capability_ids = tuple(
            _bounded_text(value, "capability_id", 240, single_line=True)
            for value in self.capability_ids
        )
        if any(not _IDENTIFIER.fullmatch(value) for value in capability_ids):
            raise ValueError("capability IDs must be stable identifiers")
        reason_codes = tuple(_reason_code(value, "reason_code") for value in self.reason_codes)
        if len(set(capability_ids)) != len(capability_ids) or len(set(reason_codes)) != len(
            reason_codes
        ):
            raise ValueError("critic verdict entries must be unique")
        object.__setattr__(self, "capability_ids", capability_ids)
        object.__setattr__(self, "reason_codes", reason_codes)


@dataclass(frozen=True)
class GapState:
    gap_id: str
    status: Literal["open", "covered", "blocked"]
    reason_code: str
    remaining_count: int

    def __post_init__(self) -> None:
        gap_id = _bounded_text(self.gap_id, "gap_id", 240, single_line=True)
        if not _IDENTIFIER.fullmatch(gap_id):
            raise ValueError("gap ID must be a stable identifier")
        object.__setattr__(self, "gap_id", gap_id)
        _reason_code(self.reason_code)
        if self.status not in {"open", "covered", "blocked"}:
            raise ValueError("gap status is unsupported")
        if not isinstance(self.remaining_count, int) or self.remaining_count < 0:
            raise ValueError("remaining_count must be a nonnegative integer")


@dataclass(frozen=True)
class P3Checkpoint:
    inputs: CheckpointInputs
    updated_at: datetime
    query_runs: tuple[QueryRun, ...] = ()
    leads: tuple[Lead, ...] = ()
    captures: tuple[CaptureRecord, ...] = ()
    critic_verdicts: tuple[CriticVerdict, ...] = ()
    iteration_count: int = 0
    gap_state: tuple[GapState, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        for name in ("query_runs", "leads", "captures", "critic_verdicts", "gap_state"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not isinstance(self.iteration_count, int) or self.iteration_count < 0:
            raise ValueError("iteration_count must be a nonnegative integer")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported P3 checkpoint schema version")


@dataclass(frozen=True)
class LoadResult:
    checkpoint: P3Checkpoint | None
    reason: Literal["loaded", "missing", "invalid"]


@dataclass(frozen=True)
class CheckResult:
    checkpoint: P3Checkpoint
    reused_parts: tuple[str, ...]
    invalidated_parts: tuple[str, ...]
    changed_inputs: tuple[str, ...]


def _empty(inputs: CheckpointInputs, now: datetime) -> P3Checkpoint:
    return P3Checkpoint(inputs=inputs, updated_at=now)


def _to_document(
    checkpoint: P3Checkpoint, generated_by: Mapping[str, str] | None = None
) -> dict[str, object]:
    provenance = generated_by or {
        "backend": "recorded",
        "model": "p3-checkpoint",
        "at": _timestamp(checkpoint.updated_at),
    }
    backend = provenance.get("backend")
    if backend not in {"recorded", "vultr", "jev"}:
        raise ValueError("checkpoint generated_by backend is unsupported")
    model = _bounded_text(str(provenance.get("model") or ""), "model", 200, single_line=True)
    generated_at = provenance.get("at") or _timestamp(checkpoint.updated_at)
    if not isinstance(generated_at, str):
        raise TypeError("checkpoint generated_by.at must be text")
    _parse_timestamp(generated_at, "generated_by.at")
    return {
        "schema_version": checkpoint.schema_version,
        "inputs": {
            "prd_digest": checkpoint.inputs.prd_digest,
            "ontology_digest": checkpoint.inputs.ontology_digest,
            "authority_digest": checkpoint.inputs.authority_digest,
            "approvals_digest": checkpoint.inputs.approvals_digest,
            "p3_algorithm_version": checkpoint.inputs.p3_algorithm_version,
            "critic_version": checkpoint.inputs.critic_version,
        },
        "updated_at": _timestamp(checkpoint.updated_at),
        "generated_by": {"backend": backend, "model": model, "at": generated_at},
        "query_runs": [
            {
                "query": item.query,
                "provider": item.provider,
                "iteration": item.iteration,
                "status": item.status,
            }
            for item in checkpoint.query_runs
        ],
        "leads": [
            {
                "query": item.query,
                "provider": item.provider,
                "url": item.url,
                "url_digest": item.url_digest,
                "source_identity": item.source_identity,
                "property_ids": list(item.property_ids),
            }
            for item in checkpoint.leads
        ],
        "captures": [
            {
                "url": item.url,
                "url_digest": item.url_digest,
                "source_identity": item.source_identity,
                "bronze_key": item.bronze_key,
                "metadata_digest": item.metadata_digest,
                "captured_at": _timestamp(item.captured_at),
            }
            for item in checkpoint.captures
        ],
        "critic_verdicts": [
            {
                "capture_digest": item.capture_digest,
                "critic_version": item.critic_version,
                "decision": item.decision,
                "capability_ids": list(item.capability_ids),
                "reason_codes": list(item.reason_codes),
            }
            for item in checkpoint.critic_verdicts
        ],
        "iteration_count": checkpoint.iteration_count,
        "gap_state": [
            {
                "gap_id": item.gap_id,
                "status": item.status,
                "reason_code": item.reason_code,
                "remaining_count": item.remaining_count,
            }
            for item in checkpoint.gap_state
        ],
    }


def _from_document(document: dict[str, object]) -> P3Checkpoint:
    inputs = CheckpointInputs(**document["inputs"])
    return P3Checkpoint(
        inputs=inputs,
        updated_at=_parse_timestamp(document["updated_at"], "updated_at"),
        query_runs=tuple(QueryRun(**item) for item in document["query_runs"]),
        leads=tuple(
            Lead(**{**item, "property_ids": tuple(item["property_ids"])})
            for item in document["leads"]
        ),
        captures=tuple(
            CaptureRecord(
                **{
                    **item,
                    "captured_at": _parse_timestamp(item["captured_at"], "captured_at"),
                }
            )
            for item in document["captures"]
        ),
        critic_verdicts=tuple(
            CriticVerdict(
                **{
                    **item,
                    "capability_ids": tuple(item["capability_ids"]),
                    "reason_codes": tuple(item["reason_codes"]),
                }
            )
            for item in document["critic_verdicts"]
        ),
        iteration_count=document["iteration_count"],
        gap_state=tuple(GapState(**item) for item in document["gap_state"]),
        schema_version=document["schema_version"],
    )


def _validate_checkpoint(
    checkpoint: P3Checkpoint, generated_by: Mapping[str, str] | None = None
) -> dict[str, object]:
    document = _to_document(checkpoint, generated_by)
    validate_document(_CHECKPOINT_NAME, document)
    return document


def load(path: Path) -> LoadResult:
    """Load a checkpoint; missing, corrupt, or unsupported files return no reusable state."""
    try:
        with Path(path).open("rb") as handle:
            payload = handle.read(MAX_CHECKPOINT_BYTES + 1)
        if len(payload) > MAX_CHECKPOINT_BYTES:
            return LoadResult(None, "invalid")
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        validate_document(_CHECKPOINT_NAME, document)
        if not isinstance(document, dict):
            return LoadResult(None, "invalid")
        checkpoint = _from_document(document)
        _validate_checkpoint(checkpoint)
    except FileNotFoundError:
        return LoadResult(None, "missing")
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
        ValueError,
        RecursionError,
    ):
        return LoadResult(None, "invalid")
    return LoadResult(checkpoint, "loaded")


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        directory_fd = os.open(directory, flags)
    except OSError as exc:
        if exc.errno in {errno.EINVAL, errno.ENOTSUP, errno.EACCES}:
            return
        raise
    try:
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            if exc.errno not in {errno.EINVAL, errno.ENOTSUP, errno.EBADF}:
                raise
    finally:
        os.close(directory_fd)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("checkpoint JSON contains a duplicate key")
        document[key] = value
    return document


def save(
    path: Path,
    checkpoint: P3Checkpoint,
    *,
    generated_by: Mapping[str, str] | None = None,
) -> None:
    """Atomically persist a validated checkpoint in a same-directory temporary file."""
    document = _validate_checkpoint(checkpoint, generated_by)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if len(payload.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
        raise ValueError("checkpoint exceeds the maximum file size")
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        _fsync_directory(destination.parent)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


def check(
    checkpoint: P3Checkpoint | None,
    inputs: CheckpointInputs,
    *,
    now: datetime | None = None,
) -> CheckResult:
    """Filter cached state to the current inputs and return parts safe to resume.

    Bronze captures are reusable across PRD, ontology, algorithm, and critic changes
    because their bytes are immutable. Authority or approval changes invalidate those
    pointers. A PRD/ontology change also clears query, lead, iteration, and gap progress.
    PRD/ontology and critic changes drop old critic verdicts; a new critic pass can
    consume an otherwise eligible recent capture.
    """
    current_time = _utc(now or _now(), "now")
    if checkpoint is None:
        return CheckResult(_empty(inputs, current_time), (), (), ())
    _validate_checkpoint(checkpoint)

    previous = checkpoint.inputs
    changed = tuple(
        name
        for name in (
            "prd_digest",
            "ontology_digest",
            "authority_digest",
            "approvals_digest",
            "p3_algorithm_version",
            "critic_version",
        )
        if getattr(previous, name) != getattr(inputs, name)
    )
    goals_changed = previous.prd_digest != inputs.prd_digest or (
        previous.ontology_digest != inputs.ontology_digest
    )
    policy_changed = previous.authority_digest != inputs.authority_digest or (
        previous.approvals_digest != inputs.approvals_digest
    )
    algorithm_changed = previous.p3_algorithm_version != inputs.p3_algorithm_version
    critic_changed = previous.critic_version != inputs.critic_version

    query_runs = checkpoint.query_runs
    leads = checkpoint.leads
    captures = checkpoint.captures
    verdicts = checkpoint.critic_verdicts
    iteration_count = checkpoint.iteration_count
    gap_state = checkpoint.gap_state

    invalidated: list[str] = []
    if goals_changed or algorithm_changed:
        query_runs = ()
        leads = ()
        iteration_count = 0
        gap_state = ()
        invalidated.extend(("query_runs", "leads", "iteration_count", "gap_state"))
    elif policy_changed:
        leads = ()
        gap_state = ()
        invalidated.extend(("leads", "gap_state"))

    if goals_changed or critic_changed:
        verdicts = ()
        invalidated.append("critic_verdicts")

    if policy_changed:
        captures = ()
        verdicts = ()
        invalidated.extend(("captures", "critic_verdicts"))

    fresh_captures = tuple(
        item for item in captures if timedelta(0) <= current_time - item.captured_at <= CAPTURE_TTL
    )
    if len(fresh_captures) != len(captures):
        invalidated.append("captures")
    captures = fresh_captures

    valid_capture_digests = {item.bronze_key for item in captures}
    fresh_verdicts = tuple(
        item
        for item in verdicts
        if item.critic_version == inputs.critic_version
        and item.capture_digest in valid_capture_digests
    )
    if len(fresh_verdicts) != len(verdicts):
        invalidated.append("critic_verdicts")
    verdicts = fresh_verdicts

    invalidated_parts = tuple(dict.fromkeys(invalidated))
    retained = {
        "query_runs": bool(query_runs),
        "leads": bool(leads),
        "captures": bool(captures),
        "critic_verdicts": bool(verdicts),
        "iteration_count": iteration_count > 0,
        "gap_state": bool(gap_state),
    }
    result = P3Checkpoint(
        inputs=inputs,
        updated_at=current_time,
        query_runs=query_runs,
        leads=leads,
        captures=captures,
        critic_verdicts=verdicts,
        iteration_count=iteration_count,
        gap_state=gap_state,
    )
    _validate_checkpoint(result)
    return CheckResult(
        checkpoint=result,
        reused_parts=tuple(name for name, has_data in retained.items() if has_data),
        invalidated_parts=invalidated_parts,
        changed_inputs=changed,
    )


def reuse(
    checkpoint: P3Checkpoint,
    inputs: CheckpointInputs,
    *,
    url: str,
    source_identity: str,
    verified_captures: Collection[tuple[str, str]],
    now: datetime | None = None,
) -> CaptureRecord | None:
    """Return a recent capture only after the caller verifies its bronze object and sidecar.

    `verified_captures` contains `(bronze_key, metadata_digest)` pairs whose immutable
    bronze bytes and corresponding metadata were revalidated from the lake for this run.
    `check` must be called first so changed authority or approval inputs have been applied.
    The caller records a `reused from checkpoint` trace step in the current run.
    """
    if checkpoint.inputs != inputs:
        return None
    try:
        target_url, target_digest = _url_identity(url)
    except (TypeError, ValueError):
        return None
    target_identity = _source_identity(source_identity)
    current_time = _utc(now or _now(), "now")
    matches = [
        item
        for item in checkpoint.captures
        if item.url == target_url
        and item.url_digest == target_digest
        and item.source_identity == target_identity
        and (item.bronze_key, item.metadata_digest) in verified_captures
        and timedelta(0) <= current_time - item.captured_at <= CAPTURE_TTL
    ]
    return max(matches, key=lambda item: item.captured_at, default=None)
