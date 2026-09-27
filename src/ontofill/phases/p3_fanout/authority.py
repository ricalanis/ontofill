"""Conservative source classification and case-approved authority checks."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from ipaddress import ip_address
from urllib.parse import urlsplit

GOVERNMENT_LEVELS = (
    "national_federal",
    "state_provincial",
    "municipal",
    "autonomous_bodies",
)
_LEVEL_DESCENDANTS = {
    "national_federal": frozenset({"state_provincial", "municipal", "autonomous_bodies"}),
    "state_provincial": frozenset({"municipal", "autonomous_bodies"}),
    "municipal": frozenset(),
    "autonomous_bodies": frozenset(),
}
_TIER_RANK = {"review": 0, "low": 1, "secondary": 2, "primary": 3}


def policy_channel_types(policy: Mapping) -> tuple[str, ...]:
    """Use the approved policy's channels rather than a code-level domain list."""
    channels = {
        channel
        for entry in policy.get("authority_matrix", [])
        if isinstance(entry, Mapping)
        for channel in entry.get("channel_types", [])
        if isinstance(channel, str)
    }
    return tuple(sorted(channels))


def _tokens(value: str) -> set[str]:
    text = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(char for char in text if not unicodedata.combining(char))
    return set(re.findall(r"[a-z0-9]{3,}", plain))


def source_class(title: str, snippet: str, classes: list[dict]) -> str:
    """Pick only among source classes in the approved case ontology."""
    if not classes:
        raise ValueError("the case ontology defines no source classes")
    observed = _tokens(f"{title} {snippet}")
    return max(classes, key=lambda item: len(observed & _tokens(item["label"])))["id"]


def source_display_identity(url: str, label: str | None, source_id: str) -> dict[str, str]:
    """Build the optional public label/host shown for a discovered source."""
    try:
        host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        host = ""
    clean_label = " ".join(label.split())[:200] if isinstance(label, str) else ""
    return {
        "source_label": clean_label or host or source_id,
        **({"source_host": host} if host else {}),
    }


def government_level_in_scope(policy: Mapping, government_level: str | None) -> bool:
    """Return whether a classified level falls under the policy's jurisdiction root."""
    hierarchy = policy.get("jurisdiction_hierarchy")
    if not isinstance(hierarchy, Mapping):
        return False
    root_level = hierarchy.get("root_level")
    if government_level not in GOVERNMENT_LEVELS or root_level not in GOVERNMENT_LEVELS:
        return False
    if government_level == root_level:
        return True
    return (
        bool(hierarchy.get("include_descendants"))
        and government_level in (_LEVEL_DESCENDANTS[root_level])
    )


def authority_matrix_tier(
    policy: Mapping,
    *,
    source_class: str | None,
    government_level: str | None,
    channel_type: str | None,
) -> str | None:
    """Match a publisher class, in-scope government level, and channel to its tier."""
    if (
        not isinstance(source_class, str)
        or not source_class.strip()
        or not isinstance(government_level, str)
        or not isinstance(channel_type, str)
        or not government_level_in_scope(policy, government_level)
    ):
        return None
    tiers = [
        str(entry.get("default_tier"))
        for entry in policy.get("authority_matrix", [])
        if isinstance(entry, Mapping)
        and entry.get("source_class") == source_class
        and government_level in entry.get("government_levels", [])
        and channel_type in entry.get("channel_types", [])
        and entry.get("default_tier") in _TIER_RANK
    ]
    # Duplicate matching rows are ambiguous; use the least permissive tier.
    return min(tiers, key=_TIER_RANK.__getitem__) if tiers else None


def authority_result(url: str, *, policy: dict | None = None) -> tuple[bool, str]:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"https", "http"} or not host or parsed.username or parsed.password:
        return False, "invalid public URL"
    if host == "localhost" or host.endswith(".localhost"):
        return False, "local host is not a public authority"
    try:
        address = ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            return False, "private address is not a public authority"
    matching = []
    for publisher in (policy or {}).get("trusted_publishers", []):
        for domain in publisher.get("domains", []):
            approved = domain.lower().rstrip(".")
            if host == approved or host.endswith("." + approved):
                matching.append(publisher)
    if any(publisher.get("tier") == "secondary" for publisher in matching):
        return False, "secondary cross-check source needs authority review"
    if any(publisher.get("tier") == "review" for publisher in matching):
        return False, "publisher tier requires authority review"
    if matching:
        return True, f"approved publisher kind: {matching[0]['kind']}"
    return False, "publisher authority needs human review"


def source_fingerprint(
    *,
    url: str,
    title: str,
    snippet: str,
    provider: str,
    capture_key: str | None,
    authority_policy: dict | None = None,
    access_path: dict | None = None,
) -> str:
    evidence = {
        "url": url,
        "title": title,
        "snippet": snippet,
        "provider": provider,
        "capture_key": capture_key,
        "authority_policy": authority_policy or {},
    }
    if access_path is not None:
        evidence["access_path"] = access_path
    return hashlib.sha256(
        json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
