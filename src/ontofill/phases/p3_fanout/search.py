"""Discover public procurement datasets from a live, sandboxed catalog."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from ontofill_scrape import SearchResult

from ontofill.lake import FileLake, S3Lake
from ontofill.sandbox import capture_url


class SandboxSearchClient:
    """The search endpoint is fixed; every candidate source URL comes from its results."""

    def __init__(self, lake: FileLake | S3Lake, run_id: str, generated_by: dict) -> None:
        self.lake = lake
        self.run_id = run_id
        self.generated_by = generated_by
        self.trace: list[dict] = []
        self.jobs: list[dict] = []
        self.capture_key: str | None = None

    def search(self, query: str) -> tuple[SearchResult, ...]:
        url = "https://data.open-contracting.org/en/search/"
        capture = capture_url(
            url,
            allowed_domains=["data.open-contracting.org"],
            lake=self.lake,
            run_id=self.run_id,
            source_id="search-provider",
            objective_id=None,
            tdd_path="03-fanout/search-policy.json",
            phase=3,
            generated_by=self.generated_by,
        )
        self.trace.extend(capture["trace"])
        self.jobs.append(capture)
        self.capture_key = capture["html_key"]
        return parse_search_results(capture["html"], query, base_url=url)


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
            "supplier",
            "suppliers",
            "contract",
            "contracts",
            "procurement",
            "proveedores",
            "proveedor",
            "datos",
            "publicos",
            "fuente",
        }
    }


def parse_search_results(
    html: str, query: str, *, base_url: str = "https://data.open-contracting.org/en/search/"
) -> tuple[SearchResult, ...]:
    soup = BeautifulSoup(html, "html.parser")
    terms = _words(query)
    results: list[tuple[int, SearchResult]] = []
    for article in soup.select("article"):
        anchor = article.select_one('a[href*="/publication/"]')
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
