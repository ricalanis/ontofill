"""Conservative authority screening for URLs observed in source discovery."""

from __future__ import annotations

import hashlib
import json
from ipaddress import ip_address
from urllib.parse import urlsplit

GOVERNMENT_SUFFIXES = (
    ".gov",
    ".gov.mx",
    ".gob.mx",
    ".gob.es",
    ".gov.br",
    ".gouv.fr",
    ".gc.ca",
    ".europa.eu",
)


def source_class(title: str, snippet: str, provider: str) -> str:
    """Classify only the result text actually captured by the provider."""
    if provider == "ocds_catalog":
        return "open-contracting publication"
    text = f"{title} {snippet}".casefold()
    if any(word in text for word in ("sanction", "sancion", "debar")):
        return "sanction registry"
    if any(word in text for word in ("tax authority", "tributaria", "fiscal", "revenue")):
        return "tax-authority list"
    if any(word in text for word in ("gazette", "diario oficial", "boletín oficial")):
        return "official gazette"
    if any(word in text for word in ("company registry", "registro mercantil", "companies house")):
        return "company registry"
    if any(word in text for word in ("procurement", "licitaci", "contrataci")):
        return "procurement portal"
    if any(word in text for word in ("open data", "datos abiertos", "data catalog")):
        return "open-data catalog"
    return "supplier website"


def authority_result(url: str, *, trusted_origin: str | None = None) -> tuple[bool, str]:
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
    if trusted_origin and host == trusted_origin:
        return True, "recognized open-data publisher from captured catalog"
    if any(host.endswith(suffix) for suffix in GOVERNMENT_SUFFIXES):
        return True, "government domain"
    return False, "publisher authority needs human review"


def source_fingerprint(
    *,
    url: str,
    title: str,
    snippet: str,
    provider: str,
    capture_key: str | None,
) -> str:
    evidence = {
        "url": url,
        "title": title,
        "snippet": snippet,
        "provider": provider,
        "capture_key": capture_key,
    }
    return hashlib.sha256(
        json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
