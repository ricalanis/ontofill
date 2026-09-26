"""Small typed contracts shared by the read-only tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FailureKind(StrEnum):
    NETWORK = "network"
    RATE_LIMITED = "rate_limited"
    BLOCKED = "blocked"
    LAYOUT_DRIFT = "layout_drift"
    STALE_BINDING = "stale_binding"
    EMPTY_YIELD = "empty_yield"
    VALIDATION_FAILED = "validation_failed"
    CONFLICT = "conflict"
    INJECTION_DETECTED = "injection_detected"


class ToolFailure(Exception):
    def __init__(self, kind: FailureKind, message: str):
        self.kind = kind
        super().__init__(message)


@dataclass(frozen=True)
class PageElement:
    tag: str
    text: str
    role: str
    name: str
    path: str
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PageSnapshot:
    url: str
    title: str
    text: str
    elements: tuple[PageElement, ...]
    skeleton_hash: str
    url_template: str


@dataclass(frozen=True)
class PageForm:
    action: str
    method: str
    fields: tuple[str, ...]
    search_or_filter: bool


@dataclass(frozen=True)
class PageLink:
    url: str
    text: str
    rel: str = ""


@dataclass(frozen=True)
class FetchedFile:
    url: str
    content: bytes
    content_type: str


@dataclass(frozen=True)
class ParsedRow:
    sheet: str | None
    row_number: int
    values: tuple[Any, ...]


@dataclass(frozen=True)
class ParsedFile:
    format: str
    rows: tuple[ParsedRow, ...] = ()
    text: str = ""


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    snippet: str = ""


@dataclass(frozen=True)
class SourceCandidate:
    url: str
    title: str
    snippet: str
    query: str
    source_type: str = "web"


@dataclass(frozen=True)
class Evidence:
    url: str
    bronze_key: str
    selector: str
    captured_at: str
    source_id: str
    screenshot_key: str | None = None


@dataclass(frozen=True)
class Observation:
    subject: str
    property: str
    value: Any
    evidence: Evidence
    confidence: float = 0.0
