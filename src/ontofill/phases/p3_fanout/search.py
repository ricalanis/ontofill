"""Discover captured public sources through pluggable sandbox providers."""

from __future__ import annotations

import base64
import re
import time
import unicodedata
from collections.abc import Sequence
from typing import ClassVar
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup
from ontofill_scrape import SearchResult, page_snapshot
from ontofill_scrape.models import FailureKind, ToolFailure

from ontofill.lake import FileLake, S3Lake
from ontofill.sandbox import capture_url


class SandboxSearchClient:
    """Search a case-configured public catalog through the sandbox."""

    name = "catalog"

    def __init__(
        self,
        lake: FileLake | S3Lake,
        run_id: str,
        generated_by: dict,
        *,
        endpoint: str,
        capture=capture_url,
    ) -> None:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("catalog endpoint must be a public HTTP(S) URL")
        self.endpoint = endpoint
        self.trusted_origin = parsed.hostname
        self.lake = lake
        self.run_id = run_id
        self.generated_by = generated_by
        self.trace: list[dict] = []
        self.jobs: list[dict] = []
        self.capture_key: str | None = None
        self.capture = capture

    def search(self, query: str) -> tuple[SearchResult, ...]:
        url = self.endpoint
        try:
            capture = self.capture(
                url,
                allowed_domains=[self.trusted_origin],
                lake=self.lake,
                run_id=self.run_id,
                source_id="search-provider",
                objective_id=None,
                tdd_path="03-fanout/search-policy.json",
                phase=3,
                generated_by=self.generated_by,
            )
        except Exception as exc:
            self.trace.extend(getattr(exc, "trace", []))
            raise
        self.trace.extend(capture["trace"])
        self.jobs.append(capture)
        self.capture_key = capture["html_key"]
        if capture["status"] >= 400:
            raise ToolFailure(FailureKind.NETWORK, "catalog returned an error")
        page_snapshot(capture["html"], capture["url"])
        return parse_search_results(capture["html"], query, base_url=url)


def _unwrap_bing(href: str) -> str:
    parsed = urlsplit(href)
    if parsed.hostname not in {"bing.com", "www.bing.com"}:
        return href
    encoded = parse_qs(parsed.query).get("u", [""])[0]
    if encoded.startswith("a1"):
        try:
            return base64.urlsafe_b64decode(encoded[2:] + "===").decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return ""
    return ""


def parse_web_results(html: str, *, provider: str) -> tuple[SearchResult, ...]:
    """Read only links visible in a captured HTML search result page."""
    soup = BeautifulSoup(html, "html.parser")
    selector = "li.b_algo h2 a[href]" if provider == "bing_html" else "a.result__a[href]"
    found = []
    for anchor in soup.select(selector):
        href = anchor.get("href", "")
        if provider == "bing_html":
            href = _unwrap_bing(href)
        else:
            parsed = urlsplit(href)
            if parsed.hostname in {"duckduckgo.com", "www.duckduckgo.com"}:
                href = parse_qs(parsed.query).get("uddg", [""])[0]
        if urlsplit(href).scheme not in {"http", "https"}:
            continue
        title = anchor.get_text(" ", strip=True)
        if not title:
            continue
        container = anchor.find_parent("li") or anchor.parent
        snippet = container.get_text(" ", strip=True)[:900] if container else title
        found.append(SearchResult(href, title, snippet))
    return tuple(found)


class SandboxWebSearchProvider:
    """S1 HTML search through one no-login endpoint and the normal egress proxy."""

    ENDPOINTS: ClassVar[dict[str, tuple[str, str, list[str]]]] = {
        "bing_html": ("https://www.bing.com/search", "q", ["www.bing.com", "bing.com"]),
        "duckduckgo_html": (
            "https://html.duckduckgo.com/html/",
            "q",
            ["html.duckduckgo.com", "duckduckgo.com"],
        ),
    }

    def __init__(
        self,
        name: str,
        lake: FileLake | S3Lake,
        run_id: str,
        generated_by: dict,
        *,
        capture=capture_url,
        min_interval_seconds: float = 3,
    ) -> None:
        if name not in self.ENDPOINTS:
            raise ValueError(f"unknown web search provider: {name}")
        self.name = name
        self.trusted_origin = None
        self.lake, self.run_id, self.generated_by = lake, run_id, generated_by
        self.capture = capture
        self.min_interval_seconds = min_interval_seconds
        self._last_request = 0.0
        self.trace: list[dict] = []
        self.jobs: list[dict] = []
        self.capture_key: str | None = None

    def search(self, query: str) -> tuple[SearchResult, ...]:
        now = time.monotonic()
        if self._last_request and now - self._last_request < self.min_interval_seconds:
            raise ToolFailure(FailureKind.RATE_LIMITED, "provider cooldown is active")
        self._last_request = now
        endpoint, query_name, allowed = self.ENDPOINTS[self.name]
        url = endpoint + "?" + urlencode({query_name: query})
        try:
            captured = self.capture(
                url,
                allowed_domains=allowed,
                lake=self.lake,
                run_id=self.run_id,
                source_id="search-provider",
                objective_id=None,
                tdd_path="03-fanout/search-policy.json",
                phase=3,
                generated_by=self.generated_by,
            )
        except Exception as exc:
            self.trace.extend(getattr(exc, "trace", []))
            raise
        self.trace.extend(captured["trace"])
        self.jobs.append(captured)
        self.capture_key = captured["html_key"]
        if captured["status"] in {202, 401, 403, 429}:
            raise ToolFailure(
                FailureKind.BLOCKED, f"{self.name} blocked: http_{captured['status']}"
            )
        if captured["status"] >= 400:
            raise ToolFailure(FailureKind.NETWORK, f"{self.name} returned an error")
        page_snapshot(captured["html"], captured["url"])
        return parse_web_results(captured["html"], provider=self.name)


class ProviderSearchClient:
    """Try distinct providers once, retaining every attempt and capture."""

    def __init__(self, providers: Sequence) -> None:
        if not providers or len({provider.name for provider in providers}) != len(providers):
            raise ValueError("providers must be nonempty and uniquely named")
        self.providers = tuple(providers)
        self.trace: list[dict] = []
        self.jobs: list[dict] = []
        self.attempts: list[dict] = []
        self.result_metadata: dict[str, dict] = {}
        self.capture_key: str | None = None

    def search(self, query: str) -> tuple[SearchResult, ...]:
        results: list[SearchResult] = []
        failures: list[ToolFailure] = []
        for provider in self.providers:
            previous_trace, previous_jobs = len(provider.trace), len(provider.jobs)
            try:
                found = tuple(provider.search(query))
                outcome = "ok" if found else "empty"
            except ToolFailure as exc:
                found, outcome = (), exc.kind.value
                failures.append(exc)
            except (OSError, RuntimeError, ValueError) as exc:
                found, outcome = (), "network"
                failures.append(ToolFailure(FailureKind.NETWORK, str(exc)))
            fresh_trace = provider.trace[previous_trace:]
            if outcome == "blocked" and fresh_trace:
                dispatch = next(
                    (
                        row
                        for row in fresh_trace
                        if row.get("evaluated", {}).get("proof_checkpoint") == "dispatch_result"
                    ),
                    fresh_trace[0],
                )
                reason = str(failures[-1]).lower()
                if "captcha" in reason:
                    reason = "blocked: captcha"
                elif "http_403" in reason:
                    reason = "blocked: http_403"
                elif "bot" in reason or "human" in reason:
                    reason = "blocked: bot_wall"
                else:
                    reason = "blocked: provider"
                dispatch["event"] = "hard_stop"
                dispatch["evaluated"] = {**dispatch.get("evaluated", {}), "reason": reason}
            self.trace.extend(fresh_trace)
            self.jobs.extend(provider.jobs[previous_jobs:])
            self.capture_key = provider.capture_key or self.capture_key
            self.attempts.append(
                {
                    "provider": provider.name,
                    "query": query,
                    "outcome": outcome,
                    "capture_key": provider.capture_key,
                    "result_count": len(found),
                    "step_ids": [row["step_id"] for row in fresh_trace],
                }
            )
            for item in found:
                self.result_metadata.setdefault(
                    item.url,
                    {
                        "provider": provider.name,
                        "capture_key": provider.capture_key,
                        "trusted_origin": provider.trusted_origin,
                    },
                )
                results.append(item)
        if results:
            return tuple(results)
        if failures:
            if all(error.kind == FailureKind.BLOCKED for error in failures):
                raise ToolFailure(FailureKind.BLOCKED, "all search providers blocked the query")
            raise failures[-1]
        raise ToolFailure(FailureKind.EMPTY_YIELD, "providers returned no results")


def _words(value: str) -> set[str]:
    plain = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return {
        word
        for word in re.findall(r"[a-z]{4,}", plain)
        if word
        not in {
            "public",
            "data",
            "source",
            "official",
            "datos",
            "publicos",
            "fuente",
        }
    }


def parse_search_results(html: str, query: str, *, base_url: str) -> tuple[SearchResult, ...]:
    soup = BeautifulSoup(html, "html.parser")
    terms = _words(query)
    results: list[tuple[int, SearchResult]] = []
    for article in soup.select("article"):
        anchor = article.select_one("a[href]")
        if anchor is None:
            continue
        href = urljoin(base_url, anchor.get("href", ""))
        if urlsplit(href).scheme not in {"http", "https"}:
            continue
        heading = article.find(["h2", "h3", "h4", "h5"])
        title = (
            heading.get_text(" ", strip=True)
            if heading
            else article.get_text(" ", strip=True)[:160]
        )
        snippet = article.get_text(" ", strip=True)[:900]
        score = len(terms & _words(title)) * 3 + len(terms & _words(snippet))
        jurisdiction = title.partition(":")[0]
        if ":" in title and _words(jurisdiction) & terms:
            score += 100
        if score:
            results.append((score, SearchResult(url=href, title=title, snippet=snippet)))
    results.sort(key=lambda pair: pair[0], reverse=True)
    return tuple(result for _, result in results)
