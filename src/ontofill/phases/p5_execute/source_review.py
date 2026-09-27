"""Digest-bound review for document links published by a trusted P5 source."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import ValidationError

from ontofill.case.checkpoints import (
    ApprovalArtifactMismatch,
    load_json,
    load_verified_approval,
    require_approval,
    write_json,
)
from ontofill.contracts import validate_document
from ontofill.phases.p3_fanout.authority import authority_result, source_fingerprint

_DNS_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\Z")
_BRONZE_KEY = re.compile(r"sha256:[0-9a-f]{64}\Z")
_LINK_TEXT_LIMIT = 500
MAX_NEW_LINK_CANDIDATES_PER_OBJECTIVE = 3


class SourceReviewPending(RuntimeError):
    """P5 found an unapproved source link and must pause before executing it."""

    def __init__(
        self,
        directory: Path,
        trace: list[dict],
        sandbox_jobs: list[dict],
        reason: str,
    ) -> None:
        self.directory = directory
        self.trace = trace
        self.sandbox_jobs = sandbox_jobs
        self.reason = reason
        super().__init__(reason)


def _allowed_host(host: str, allowed_domains: list[str]) -> bool:
    normalized = host.casefold().rstrip(".")
    return any(
        normalized == domain.casefold().rstrip(".")
        or normalized.endswith("." + domain.casefold().rstrip("."))
        for domain in allowed_domains
    )


def reviewable_download_host(url: str, allowed_domains: list[str]) -> str | None:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username
        or parsed.password
        or port not in {None, 80, 443}
        or not _DNS_NAME.fullmatch(host)
        or ".." in host
    ):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None if _allowed_host(host, allowed_domains) else host
    return None


def trusted_parent_source(
    case_dir: Path, *, parent_source_id: str, parent_page_url: str, authority_policy: dict
) -> bool:
    """Require policy trust or a current digest-bound source approval for the parent."""
    trusted, _reason = authority_result(parent_page_url, policy=authority_policy)
    if trusted:
        return True

    directory = case_dir / "03-fanout/sources" / parent_source_id
    candidate_path = directory / "candidate.json"
    marker = directory / "APPROVED"
    if not candidate_path.is_file() or not marker.is_file():
        return False
    relative = candidate_path.relative_to(case_dir).as_posix()
    try:
        approval = load_verified_approval(marker, case_dir, [relative], "source")
        candidate = load_json(candidate_path)
    except (OSError, ValueError, ValidationError) as exc:
        raise ApprovalArtifactMismatch("source") from exc
    return not (
        candidate.get("url") != parent_page_url
        or approval.get("source_fingerprint") != candidate.get("fingerprint")
        or approval.get("decision", "approve") == "deny"
    )


def canonical_link_url(value: str) -> str:
    """Normalize URL identity without changing its path, query, or fragment."""
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if not host or parsed.scheme not in {"http", "https"}:
        raise ValueError("link review requires an absolute HTTP URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("link review URL has an invalid port") from exc
    netloc = host
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]"
    if port is not None and not (
        (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)
    ):
        netloc += f":{port}"
    return parsed._replace(scheme=parsed.scheme.casefold(), netloc=netloc).geturl()


def _candidate_id(parent_source_id: str, link_url: str) -> str:
    seed = json.dumps(
        [parent_source_id, canonical_link_url(link_url)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "source-link-" + hashlib.sha256(seed.encode()).hexdigest()[:20]


def link_candidate_directory(case_dir: Path, parent_source_id: str, link_url: str) -> Path:
    return case_dir / "03-fanout/sources" / _candidate_id(parent_source_id, link_url)


def _stored_candidate_matches(
    candidate: dict,
    *,
    source_id: str,
    parent_source_id: str,
    parent_page_url: str,
    link_url: str,
    authority_policy: dict,
) -> bool:
    link_provenance = candidate.get("link_provenance")
    if not isinstance(link_provenance, dict):
        return False
    canonical_url = canonical_link_url(link_url)
    if (
        candidate.get("source_id") != source_id
        or candidate.get("url") != canonical_url
        or candidate.get("provider") != "sandbox_page_link"
        or link_provenance.get("parent_source_id") != parent_source_id
        or link_provenance.get("parent_page_url") != parent_page_url
        or link_provenance.get("link_url") != canonical_url
        or link_provenance.get("parent_capture_key") != candidate.get("capture_key")
    ):
        return False
    expected_fingerprint = source_fingerprint(
        url=candidate["url"],
        title=candidate.get("title", ""),
        snippet=candidate.get("snippet", ""),
        provider="sandbox_page_link",
        capture_key=candidate.get("capture_key"),
        authority_policy=authority_policy,
    )
    return candidate.get("fingerprint") == expected_fingerprint


def _read_link_candidate(
    *,
    case_dir: Path,
    parent_source_id: str,
    parent_page_url: str,
    parent_capture_key: str,
    link_url: str,
    link_text: str,
    link_index: int,
    authority_policy: dict,
    provenance: dict,
    screenshot_key: str | None,
    parent_step_id: str | None,
) -> tuple[dict, Path]:
    link_url = canonical_link_url(link_url)
    source_id = _candidate_id(parent_source_id, link_url)
    directory = link_candidate_directory(case_dir, parent_source_id, link_url)
    link_provenance = {
        "parent_source_id": parent_source_id,
        "parent_page_url": parent_page_url,
        "parent_capture_key": parent_capture_key,
        "link_url": link_url,
        "link_text": " ".join(link_text.split())[:_LINK_TEXT_LIMIT],
        "link_index": link_index,
    }
    if parent_step_id:
        link_provenance["step_id"] = parent_step_id
    snippet = f"Document link observed on {parent_page_url}"
    fingerprint = source_fingerprint(
        url=link_url,
        title=link_provenance["link_text"],
        snippet=snippet,
        provider="sandbox_page_link",
        capture_key=parent_capture_key,
        authority_policy=authority_policy,
    )
    candidate = {
        "source_id": source_id,
        "url": link_url,
        "title": link_provenance["link_text"],
        "snippet": snippet,
        "provider": "sandbox_page_link",
        "providers": ["sandbox_page_link"],
        "capture_key": parent_capture_key,
        "authority": "review",
        "authority_tier": "unknown",
        "authority_reason": "The document host is linked by a trusted source but is not in the approved source policy.",
        "fingerprint": fingerprint,
        "link_provenance": link_provenance,
        "generated_by": provenance,
    }
    if screenshot_key is not None and _BRONZE_KEY.fullmatch(screenshot_key):
        candidate["screenshot_key"] = screenshot_key
    validate_document("source-candidate", candidate)
    return candidate, directory


def review_link_candidate(
    *,
    case_dir: Path,
    parent_source_id: str,
    parent_page_url: str,
    parent_capture_key: str,
    link_url: str,
    link_text: str,
    link_index: int,
    allowed_domains: list[str],
    authority_policy: dict,
    provenance: dict,
    screenshot_key: str | None = None,
    parent_step_id: str | None = None,
) -> tuple[str, str | None, Path]:
    """Persist and verify one off-domain download candidate without performing a request."""
    host = reviewable_download_host(link_url, allowed_domains)
    if host is None or _allowed_host(host, allowed_domains):
        raise ValueError("link review requires a supported public off-domain document URL")
    if not _BRONZE_KEY.fullmatch(parent_capture_key):
        raise ValueError("link review requires the parent page's bronze capture key")
    if not trusted_parent_source(
        case_dir,
        parent_source_id=parent_source_id,
        parent_page_url=parent_page_url,
        authority_policy=authority_policy,
    ):
        raise ValueError("off-domain document link parent is not a trusted source")

    expected, directory = _read_link_candidate(
        case_dir=case_dir,
        parent_source_id=parent_source_id,
        parent_page_url=parent_page_url,
        parent_capture_key=parent_capture_key,
        link_url=link_url,
        link_text=link_text,
        link_index=link_index,
        authority_policy=authority_policy,
        provenance=provenance,
        screenshot_key=screenshot_key,
        parent_step_id=parent_step_id,
    )
    candidate_path = directory / "candidate.json"
    marker = directory / "APPROVED"
    relative = candidate_path.relative_to(case_dir).as_posix()

    if candidate_path.exists():
        try:
            current = load_json(candidate_path)
        except (OSError, ValueError) as exc:
            raise ApprovalArtifactMismatch("source") from exc
        if not _stored_candidate_matches(
            current,
            source_id=expected["source_id"],
            parent_source_id=parent_source_id,
            parent_page_url=parent_page_url,
            link_url=link_url,
            authority_policy=authority_policy,
        ):
            raise ApprovalArtifactMismatch("source")
        expected = current

    # Verify an existing decision against existing bytes before any possible write.
    approval = None
    if marker.exists():
        try:
            approval = load_verified_approval(marker, case_dir, [relative], "source")
            current = load_json(candidate_path)
        except (OSError, ValueError, ValidationError) as exc:
            raise ApprovalArtifactMismatch("source") from exc
        if approval.get("source_fingerprint") != expected["fingerprint"]:
            raise ApprovalArtifactMismatch("source")
        if approval.get("decision", "approve") == "deny":
            return "denied", None, directory
        if provenance.get("backend") != "vultr":
            return "pending", None, directory
        return "approved", current["url"], directory

    if not candidate_path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        write_json(candidate_path, expected)

    pending = directory / "APPROVAL_PENDING.md"
    if not pending.exists():
        require_approval(
            directory,
            phase=3,
            checkpoint="source",
            artifact_paths=[relative],
            generated_by=provenance,
            source_fingerprint=expected["fingerprint"],
            case_dir=case_dir,
        )
    return "pending", None, directory
