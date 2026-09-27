from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox" / "agent-pod"))
from spider_policy import CrawlPolicy, crawl_site


def _response(url: str, body: str, *, status: int = 200, content_type: str = "text/html") -> dict:
    return {
        "status": status,
        "final_url": url,
        "content_type": content_type,
        "body": body.encode(),
    }


def test_crawl_obeys_robots_depth_domain_and_politeness() -> None:
    seed = "https://catalog.example.invalid/"
    detail = "https://catalog.example.invalid/records/1"
    requested: list[str] = []
    now = [0.0]
    sleeps: list[float] = []
    bodies = {
        seed: (
            '<a href="/records/1" rel="next">Record</a>'
            '<a href="/private">Private</a>'
            '<a href="https://elsewhere.example.net/exit">Exit</a>'
            '<form action="/submit" method="post"><a href="/form-link">Form</a></form>'
        ),
        detail: '<a href="/records/1/files/export.csv" download>Export</a>',
    }

    def fetch(url: str) -> dict:
        requested.append(url)
        now[0] += 0.15
        if url.endswith("/robots.txt"):
            return {
                "status": 200,
                "final_url": url,
                "content_type": "text/plain",
                "body": b"User-agent: OntofillSpider/1.0\nDisallow: /private\nCrawl-delay: 2\n",
            }
        return _response(url, bodies[url])

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    result = crawl_site(
        seed,
        policy=CrawlPolicy(allowed_domain="example.invalid", max_depth=1, page_cap=10),
        fetch=fetch,
        sleep=sleep,
        clock=lambda: now[0],
    )

    assert requested == [
        "https://catalog.example.invalid/robots.txt",
        seed,
        detail,
    ]
    assert result["fetched_pages"] == 2
    assert result["stop_reason"] == "depth_limit"
    assert result["robots"][0]["decision"] == "disallow"
    assert result["robots"][0]["crawl_delay_seconds"] == 2.0
    assert sleeps == pytest.approx([1.85, 1.85])

    edges = {(edge["from_url"], edge["to_url"]): edge for edge in result["edges"]}
    assert edges[(seed, detail)]["followed"] is True
    assert edges[(seed, "https://catalog.example.invalid/private")]["reason"] == "robots_disallow"
    assert edges[(seed, "https://elsewhere.example.net/exit")]["reason"] == (
        "cross_registrable_domain"
    )
    depth_edge = edges[(detail, "https://catalog.example.invalid/records/1/files/export.csv")]
    assert depth_edge["reason"] == "depth_limit"
    assert depth_edge["followed"] is False
    assert not any("form-link" in edge["to_url"] for edge in result["edges"])


def test_crawl_page_cap_settles_unfetched_edges() -> None:
    seed = "https://catalog.example.invalid/"
    one = "https://catalog.example.invalid/one"
    two = "https://catalog.example.invalid/two"
    requested: list[str] = []

    def fetch(url: str) -> dict:
        requested.append(url)
        if url.endswith("/robots.txt"):
            return {"status": 404, "final_url": url, "body": b""}
        if url == seed:
            return _response(url, '<a href="/one">One</a><a href="/two">Two</a>')
        return _response(url, "<p>Page</p>")

    result = crawl_site(
        seed,
        policy=CrawlPolicy(allowed_domain="example.invalid", max_depth=2, page_cap=2),
        fetch=fetch,
        sleep=lambda _: None,
    )

    assert requested == ["https://catalog.example.invalid/robots.txt", seed, one]
    assert result["attempted_pages"] == 2
    assert result["fetched_pages"] == 2
    assert result["stop_reason"] == "page_cap"
    pending = next(edge for edge in result["edges"] if edge["to_url"] == two)
    assert pending["followed"] is False
    assert pending["reason"] == "page_cap"


@pytest.mark.parametrize("status", [401, 403])
def test_auth_status_stops_queued_urls_and_preserves_response_body(status: int) -> None:
    seed = "https://catalog.example.invalid/"
    auth_url = "https://catalog.example.invalid/records/1"
    queued_url = "https://catalog.example.invalid/records/2"
    auth_body = "Authentication required for this page."
    requested: list[str] = []

    def fetch(url: str) -> dict:
        requested.append(url)
        if url.endswith("/robots.txt"):
            return {"status": 404, "final_url": url, "body": b""}
        if url == seed:
            return _response(url, '<a href="/records/1">One</a><a href="/records/2">Two</a>')
        if url == auth_url:
            return _response(url, auth_body, status=status)
        return _response(url, "This page must not be fetched")

    result = crawl_site(
        seed,
        policy=CrawlPolicy(allowed_domain="example.invalid", page_cap=5),
        fetch=fetch,
        sleep=lambda _: None,
    )

    assert requested == ["https://catalog.example.invalid/robots.txt", seed, auth_url]
    assert result["stop_reason"] == "login_or_captcha"
    auth_page = result["pages"][1]
    assert auth_page["status"] == status
    assert auth_page["body"] == auth_body.encode()
    assert auth_page["blocked_reason"] == "login_or_captcha"
    pending_edge = next(edge for edge in result["edges"] if edge["to_url"] == queued_url)
    assert pending_edge["followed"] is False


def test_redirects_are_followed_only_inside_the_allowed_domain() -> None:
    seed = "https://catalog.example.invalid/"
    inside = "https://catalog.example.invalid/records"
    outside = "https://other.example.net/records"
    requested: list[str] = []

    def fetch(url: str) -> dict:
        requested.append(url)
        if url.endswith("/robots.txt"):
            return {"status": 404, "final_url": url, "body": b""}
        if url == seed:
            return {"status": 302, "final_url": seed, "location": "/records", "body": b""}
        return {"status": 302, "final_url": inside, "location": outside, "body": b""}

    result = crawl_site(
        seed,
        policy=CrawlPolicy(allowed_domain="example.invalid"),
        fetch=fetch,
        sleep=lambda _: None,
    )

    assert requested == ["https://catalog.example.invalid/robots.txt", seed, inside]
    assert result["pages"][0]["redirect_chain"] == [seed, inside]
    assert result["pages"][0]["blocked_reason"] == "redirect_outside_registrable_domain"
    assert result["blocked"][-1]["url"] == outside


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_depth": -1}, "max_depth"),
        ({"max_depth": 3}, "max_depth"),
        ({"max_depth": True}, "max_depth"),
        ({"page_cap": 0}, "page_cap"),
        ({"page_cap": 51}, "page_cap"),
        ({"page_cap": True}, "page_cap"),
        ({"delay_seconds": 0.1}, "delay_seconds"),
        ({"max_response_bytes": 512 * 1024 + 1}, "max_response_bytes"),
    ],
)
def test_crawl_policy_rejects_unbounded_or_invalid_settings(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        CrawlPolicy(allowed_domain="example.invalid", **kwargs)
