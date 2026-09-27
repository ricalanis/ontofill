"""Pluggable, jurisdiction-keyed column patterns for the document profiler.

Patterns are pure code with no case-specific vocabulary: a pattern is a name, a
compiled regex and the jurisdictions it applies to (``None`` = universal). The
library is a plain mapping so a case can extend or override it without editing
this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Pattern:
    name: str
    regex: re.Pattern[str]
    jurisdictions: tuple[str, ...] | None = None
    kind: str = "id"


def _p(
    name: str, expression: str, *, jurisdictions: tuple[str, ...] | None = None, kind: str = "id"
):
    return Pattern(name, re.compile(expression), jurisdictions, kind)


# Universal patterns first, then jurisdiction-keyed ones (tax ids, postal codes).
PATTERNS: tuple[Pattern, ...] = (
    _p("email", r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"),
    _p("url", r"^https?://\S+$"),
    _p("date_iso", r"^\d{4}-\d{2}-\d{2}$", kind="date"),
    _p("date_slash", r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$", kind="date"),
    _p("datetime_iso", r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", kind="date"),
    _p("year", r"^(?:19|20)\d{2}$", kind="date"),
    _p("amount", r"^\$?\s?-?\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?$", kind="amount"),
    _p("amount_currency", r"^\$?\s?-?\d+(?:\.\d{1,2})?\s?(?:MXN|USD|EUR|mxn|usd)$", kind="amount"),
    _p("percentage", r"^-?\d+(?:\.\d+)?\s?%$", kind="amount"),
    _p("phone", r"^\+?\d[\d\s().-]{6,}\d$", kind="contact"),
    _p("integer", r"^\d+$", kind="number"),
    _p("decimal", r"^-?\d+\.\d+$", kind="number"),
    _p(
        "legal_suffix",
        r"(?i)\b(?:S\.?A\.?\s?DE\s?C\.?V\.?|S\.?A\.?|S\.?C\.?|"
        r"S\.?DE\s?R\.?L\.?(?:\s?DE\s?C\.?V\.?)?|LLC|INC\.?|CORP\.?|GMBH|LTDA|PLC)\b",
    ),
    _p(
        "address_hint",
        r"(?i)\b(?:calle|c\.|avenida|av\.|blvd|boulevard|street|st\.|road|rd\.|"
        r"colonia|col\.|cp\s?\d{4,5}|suite|no\.\s?\d+)\b",
        kind="address",
    ),
    _p("postal_mx", r"^\d{5}$", jurisdictions=("MX",), kind="address"),
    _p("tax_id_rfc", r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{2,3}$", jurisdictions=("MX",), kind="tax_id"),
    _p(
        "tax_id_curp",
        r"^[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d$",
        jurisdictions=("MX",),
        kind="tax_id",
    ),
    _p("tax_id_us_ein", r"^\d{2}-\d{7}$", jurisdictions=("US",), kind="tax_id"),
    _p("postal_us", r"^\d{5}(?:-\d{4})?$", jurisdictions=("US",), kind="address"),
)


def match_patterns(value: str, *, jurisdictions: tuple[str, ...] = ()) -> list[str]:
    """Return the patterns a single cell value matches for the given jurisdictions."""
    if not value or len(value) > 500:
        return []
    allowed = set(jurisdictions)
    matched = []
    for pattern in all_patterns():
        if pattern.jurisdictions is not None and not (set(pattern.jurisdictions) & allowed):
            continue
        if pattern.regex.search(value):
            matched.append(pattern.name)
    return matched


_EXTRA_PATTERNS: list[Pattern] = []
_MAX_EXTRA_PATTERNS = 40
_INVALID_PATTERN = re.compile(r"[^A-Za-z0-9_]")


def register_patterns(overrides: object) -> int:
    """Merge case- or jurisdiction-supplied patterns from the parse envelope.

    Each entry is ``{"name", "regex", "jurisdictions"?, "kind"?}``. Invalid entries are
    skipped so a bad override never fails the parse; returns the number registered.
    """
    if not isinstance(overrides, list):
        return 0
    added = 0
    for entry in overrides[:_MAX_EXTRA_PATTERNS]:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        expression = entry.get("regex")
        if not isinstance(name, str) or not _INVALID_PATTERN.fullmatch(name or ""):
            continue
        if not isinstance(expression, str) or len(expression) > 400:
            continue
        jurisdictions = entry.get("jurisdictions")
        if jurisdictions is not None and not isinstance(jurisdictions, list):
            continue
        try:
            compiled = re.compile(expression)
        except re.error:
            continue
        kind = entry.get("kind")
        _EXTRA_PATTERNS.append(
            Pattern(
                name,
                compiled,
                tuple(jurisdictions) if jurisdictions else None,
                kind if isinstance(kind, str) else "id",
            )
        )
        added += 1
    return added


def all_patterns() -> tuple[Pattern, ...]:
    """The built-in library plus any patterns registered for this pod invocation."""
    return (*PATTERNS, *_EXTRA_PATTERNS)
