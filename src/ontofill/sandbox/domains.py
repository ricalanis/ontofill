"""Public Suffix List helpers for capture redirect boundaries."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

import tldextract

# Use the package's bundled PSL snapshot. Spider and browser pods must not fetch
# suffix data at runtime, and private suffixes keep tenant hosts isolated.
_EXTRACT = tldextract.TLDExtract(
    suffix_list_urls=(),
    include_psl_private_domains=True,
    extra_suffixes=("test", "invalid"),
)


def registrable_domain(host: str) -> str:
    """Return the registrable domain (or the normalized host if it has no suffix)."""
    normalized = host.casefold().rstrip(".")
    if not normalized:
        return ""
    try:
        return ipaddress.ip_address(normalized).compressed.casefold()
    except ValueError:
        result = _EXTRACT(normalized)
        return (result.top_domain_under_public_suffix or normalized).casefold()


def url_registrable_domain(value: str) -> str:
    """Extract the registrable domain from an absolute URL or hostname."""
    parsed = urlsplit(value if "://" in value or value.startswith("//") else f"//{value}")
    return registrable_domain(parsed.hostname or "")


def same_registrable_domain(first: str, second: str) -> bool:
    """Whether two URLs or hostnames share the same registrable domain."""
    first_domain = url_registrable_domain(first)
    return bool(first_domain and first_domain == url_registrable_domain(second))
