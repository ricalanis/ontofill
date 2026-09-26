"""Conservative source classification and case-approved authority checks."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from ipaddress import ip_address
from urllib.parse import urlsplit


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
    for publisher in (policy or {}).get("trusted_publishers", []):
        for domain in publisher.get("domains", []):
            approved = domain.lower().rstrip(".")
            if host == approved or host.endswith("." + approved):
                return True, f"approved publisher kind: {publisher['kind']}"
    return False, "publisher authority needs human review"


def source_fingerprint(
    *,
    url: str,
    title: str,
    snippet: str,
    provider: str,
    capture_key: str | None,
    authority_policy: dict | None = None,
) -> str:
    evidence = {
        "url": url,
        "title": title,
        "snippet": snippet,
        "provider": provider,
        "capture_key": capture_key,
        "authority_policy": authority_policy or {},
    }
    return hashlib.sha256(
        json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
