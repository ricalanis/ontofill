"""Brief-driven source discovery through an injected sandbox search client."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urlsplit

from .models import FailureKind, SearchResult, SourceCandidate, ToolFailure


class SearchClient(Protocol):
    """Search service access is supplied by the caller's sandbox/proxy."""

    def search(self, query: str) -> Sequence[SearchResult]: ...


def brief_search_query(brief: str, *, max_words: int = 28) -> str:
    words = re.findall(r"[\wÀ-ÿ-]+", brief)
    if not words:
        raise ToolFailure(FailureKind.VALIDATION_FAILED, "brief is empty")
    return " ".join(words[:max_words]) + " public data official source"


def source_discover(
    brief: str,
    search_client: SearchClient,
    *,
    limit: int = 10,
) -> tuple[SourceCandidate, ...]:
    query = brief_search_query(brief)
    try:
        results = search_client.search(query)
    except Exception as exc:
        raise ToolFailure(FailureKind.NETWORK, "search service failed") from exc
    candidates: list[SourceCandidate] = []
    seen: set[str] = set()
    for result in results:
        parsed = urlsplit(result.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        if result.url in seen:
            continue
        seen.add(result.url)
        candidates.append(SourceCandidate(result.url, result.title, result.snippet, query))
        if len(candidates) >= limit:
            break
    if not candidates:
        raise ToolFailure(FailureKind.EMPTY_YIELD, "search returned no source candidates")
    return tuple(candidates)
