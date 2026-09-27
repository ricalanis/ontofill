"""Pure crawl policy shared by the gVisor spider and synthetic regressions."""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from math import isfinite
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser


@dataclass(frozen=True)
class CrawlPolicy:
    allowed_domain: str
    max_depth: int = 2
    page_cap: int = 30
    delay_seconds: float = 1.0
    max_redirects: int = 5
    max_redirects_total: int = 100
    max_response_bytes: int = 512 * 1024
    user_agent: str = "OntofillSpider/1.0"

    def __post_init__(self) -> None:
        domain = self.allowed_domain.casefold().rstrip(".")
        if not domain or "/" in domain or ":" in domain:
            raise ValueError("allowed_domain must be a DNS name")
        if type(self.max_depth) is not int or not 0 <= self.max_depth <= 2:
            raise ValueError("max_depth must be between 0 and 2")
        if type(self.page_cap) is not int or not 1 <= self.page_cap <= 50:
            raise ValueError("page_cap must be between 1 and 50")
        if (
            isinstance(self.delay_seconds, bool)
            or not isinstance(self.delay_seconds, (int, float))
            or not isfinite(self.delay_seconds)
            or not 0.25 <= self.delay_seconds <= 60
        ):
            raise ValueError("delay_seconds must be between 0.25 and 60")
        if type(self.max_redirects) is not int or not 1 <= self.max_redirects <= 10:
            raise ValueError("max_redirects must be between 1 and 10")
        if type(self.max_redirects_total) is not int or not 1 <= self.max_redirects_total <= 250:
            raise ValueError("max_redirects_total must be between 1 and 250")
        if (
            type(self.max_response_bytes) is not int
            or not 1024 <= self.max_response_bytes <= 512 * 1024
        ):
            raise ValueError("max_response_bytes must be between 1 KiB and 512 KiB")


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._form_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        names = {name.casefold(): value or "" for name, value in attrs}
        if tag.casefold() == "form":
            self._form_depth += 1
            return
        if tag.casefold() not in {"a", "area"} or self._form_depth:
            return
        href = names.get("href", "").strip()
        if not href:
            return
        rel = names.get("rel", "").casefold().split()
        hint = " ".join(
            (
                " ".join(rel),
                names.get("aria-label", ""),
                names.get("title", ""),
                names.get("class", ""),
            )
        ).casefold()
        path = urlsplit(href).path.casefold()
        if "download" in names or re.search(
            r"\.(?:pdf|csv|xlsx?|docx?|zip|json|xml)(?:$|\?)", path
        ):
            kind = "download"
        elif "next" in rel or re.search(r"\b(next|older|more results|page\s*\d+)\b", hint):
            kind = "paginate"
        elif re.search(r"/(?:search|find|query)(?:/|$)", path):
            kind = "search_results"
        else:
            kind = "navigate"
        self.links.append((href, kind))

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "form" and self._form_depth:
            self._form_depth -= 1


def same_allowed_domain(url: str, allowed_domain: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    domain = allowed_domain.casefold().rstrip(".")
    return (
        parsed.scheme.casefold() in {"http", "https"}
        and not parsed.username
        and not parsed.password
        and bool(host)
        and (host == domain or host.endswith("." + domain))
    )


def normalize_link(href: str, base_url: str, allowed_domain: str) -> tuple[str | None, str | None]:
    """Resolve a URL and explain policy blocks without ever submitting a form."""
    candidate = urldefrag(urljoin(base_url, href.strip()))[0]
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return None, "unsafe_scheme_or_credentials"
    if not same_allowed_domain(candidate, allowed_domain):
        return candidate, "cross_registrable_domain"
    return candidate, None


def page_stop_reason(url: str, html: str) -> str | None:
    """Stop before following links from authentication and CAPTCHA interstitials."""
    parsed = urlsplit(url)
    if re.search(
        r"(?:^|/)(?:login|log-in|signin|sign-in|authenticate|auth)(?:/|$)",
        parsed.path,
        re.IGNORECASE,
    ):
        return "login_or_captcha"
    if re.search(
        r"\b(?:captcha|recaptcha|hcaptcha|verify you are human|robot check)\b",
        html,
        re.IGNORECASE,
    ):
        return "login_or_captcha"
    if re.search(r"<input\b[^>]*\btype\s*=\s*['\"]?password\b", html, re.IGNORECASE):
        return "login_or_captcha"
    return None


def extract_links(html: str, base_url: str, allowed_domain: str) -> list[dict]:
    parser = _Links()
    parser.feed(html)
    links: list[dict] = []
    seen: set[str] = set()
    for href, kind in parser.links:
        url, reason = normalize_link(href, base_url, allowed_domain)
        if url is None:
            links.append({"url": href, "kind": kind, "reason": reason})
            continue
        if url in seen:
            continue
        seen.add(url)
        links.append({"url": url, "kind": kind, "reason": reason})
    return links


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme.casefold()}://{parsed.netloc.casefold()}"


def _robot_state(
    origin: str,
    policy: CrawlPolicy,
    fetch: Callable[[str], Mapping],
    throttle: Callable[[], None],
) -> tuple[dict, RobotFileParser | None, float, str | None]:
    robots_url = origin + "/robots.txt"
    throttle()
    response = dict(fetch(robots_url))
    status = response.get("status")
    body = response.get("body", b"")
    record = {
        "origin": origin,
        "url": robots_url,
        "http_status": status if isinstance(status, int) else None,
        "decision": "conservative_stop",
        "crawl_delay_seconds": None,
        "body": body if isinstance(body, bytes) else b"",
    }
    if status == 404:
        parser = RobotFileParser(robots_url)
        parser.parse([])
        record["decision"] = "allow"
        return record, parser, policy.delay_seconds, None
    if (
        response.get("too_large")
        or status is None
        or not isinstance(status, int)
        or not 200 <= status < 300
    ):
        return record, None, policy.delay_seconds, "robots"
    if response.get("final_url", robots_url) != robots_url:
        return record, None, policy.delay_seconds, "robots"
    if not isinstance(body, bytes):
        return record, None, policy.delay_seconds, "robots"
    parser = RobotFileParser(robots_url)
    try:
        parser.parse(body.decode("utf-8", errors="replace").splitlines())
    except (TypeError, ValueError):
        return record, None, policy.delay_seconds, "robots"
    server_delay = parser.crawl_delay(policy.user_agent) or parser.crawl_delay("*") or 0
    if not isinstance(server_delay, (int, float)) or not isfinite(server_delay) or server_delay < 0:
        return record, None, policy.delay_seconds, "robots"
    delay = max(policy.delay_seconds, float(server_delay))
    record.update(decision="allow", crawl_delay_seconds=float(server_delay))
    return record, parser, delay, None


def crawl_site(
    seed_url: str,
    *,
    policy: CrawlPolicy,
    fetch: Callable[[str], Mapping],
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """BFS crawl using only injected single-request GETs; redirects are policy-owned."""
    if not same_allowed_domain(seed_url, policy.allowed_domain):
        raise ValueError("seed must be inside the allowed registrable domain")

    queue = deque([(seed_url, 0, None, "navigate")])
    queued = {seed_url}
    completed: set[str] = set()
    robots_by_origin: dict[str, tuple[dict, RobotFileParser | None, float]] = {}
    robot_records: list[dict] = []
    pages: list[dict] = []
    edges: list[dict] = []
    blocked: list[dict] = []
    attempted_pages = 0
    redirects = 0
    last_request: float | None = None
    stop_reason = "queue_exhausted"
    depth_limited = False
    last_status: int | None = None

    def throttle(delay: float | None = None) -> None:
        nonlocal last_request
        now = clock()
        minimum = policy.delay_seconds if delay is None else delay
        if last_request is not None:
            wait = max(0.0, minimum - (now - last_request))
            if wait:
                sleep(wait)
                now = clock()
        last_request = now

    def robots_for(url: str) -> tuple[dict, RobotFileParser | None, float, str | None]:
        nonlocal last_status
        origin = _origin(url)
        if origin not in robots_by_origin:
            info, parser, delay, reason = _robot_state(origin, policy, fetch, throttle)
            if isinstance(info.get("http_status"), int):
                last_status = info["http_status"]
            robots_by_origin[origin] = (info, parser, delay)
            robot_records.append(info)
            return info, parser, delay, reason
        info, parser, delay = robots_by_origin[origin]
        return info, parser, delay, None

    def settle_edge(parent: str | None, target: str, *, followed: bool, reason: str | None) -> None:
        if parent is None:
            return
        for edge in reversed(edges):
            if (
                edge.get("from_url") == parent
                and edge.get("to_url") == target
                and edge.get("reason") == "pending"
            ):
                edge.update(followed=followed, reason=reason)
                return

    while queue and attempted_pages < policy.page_cap:
        requested_url, depth, parent_url, link_kind = queue.popleft()
        if requested_url in completed:
            continue
        if not same_allowed_domain(requested_url, policy.allowed_domain):
            blocked.append({"url": requested_url, "reason": "cross_registrable_domain"})
            continue

        attempt_url = requested_url
        redirect_chain = [requested_url]
        final_response: dict | None = None
        blocked_reason: str | None = None
        network_requested = False
        local_redirects = 0
        while True:
            robot, parser, delay, robot_error = robots_for(attempt_url)
            if robot_error:
                stop_reason = "robots"
                blocked_reason = "robots_unavailable"
                break
            if parser is None or not parser.can_fetch(policy.user_agent, attempt_url):
                robot["decision"] = "disallow"
                blocked_reason = "robots_disallow"
                break
            throttle(delay)
            network_requested = True
            response = dict(fetch(attempt_url))
            status = response.get("status")
            last_status = status if isinstance(status, int) else last_status
            location = response.get("location")
            if status not in {301, 302, 303, 307, 308}:
                final_response = response
                break
            if not isinstance(location, str) or not location.strip():
                final_response = response
                blocked_reason = "redirect_without_location"
                break
            target = urldefrag(urljoin(attempt_url, location.strip()))[0]
            if not same_allowed_domain(target, policy.allowed_domain):
                final_response = response
                blocked_reason = "redirect_outside_registrable_domain"
                blocked.append({"url": target, "from_url": attempt_url, "reason": blocked_reason})
                break
            local_redirects += 1
            redirects += 1
            if local_redirects > policy.max_redirects or redirects > policy.max_redirects_total:
                final_response = response
                blocked_reason = "redirect_limit"
                blocked.append({"url": target, "from_url": attempt_url, "reason": blocked_reason})
                break
            attempt_url = target
            redirect_chain.append(target)

        attempted_pages += 1
        completed.add(requested_url)
        if blocked_reason in {"robots_disallow", "robots_unavailable"}:
            blocked.append({"url": requested_url, "reason": blocked_reason})
        if stop_reason == "robots":
            settle_edge(parent_url, requested_url, followed=False, reason=blocked_reason)
            break
        if final_response is None:
            settle_edge(parent_url, requested_url, followed=False, reason=blocked_reason)
            continue

        status = final_response.get("status")
        body = final_response.get("body", b"")
        body = body if isinstance(body, bytes) else b""
        content_type = str(final_response.get("content_type", "application/octet-stream"))
        final_url = str(final_response.get("final_url") or attempt_url)
        if final_url != attempt_url:
            # The transport must return one response without following redirects.
            blocked_reason = "transport_followed_redirect"
            final_url = attempt_url
        if len(body) > policy.max_response_bytes:
            body = body[: policy.max_response_bytes]
            blocked_reason = "response_too_large"
        if status is None:
            blocked_reason = blocked_reason or str(final_response.get("error") or "network_error")
        elif status in {401, 403}:
            blocked_reason = blocked_reason or "login_or_captcha"
        page = {
            "requested_url": requested_url,
            "final_url": final_url,
            "redirect_chain": redirect_chain,
            "depth": depth,
            "status": status if isinstance(status, int) else None,
            "content_type": content_type,
            "method": "GET",
            "link_kind": link_kind,
            "parent_url": parent_url,
            "body": body,
            "blocked_reason": blocked_reason,
        }
        pages.append(page)
        if parent_url is not None:
            edge_reason = blocked_reason
            if status is not None and not 200 <= status < 300:
                edge_reason = edge_reason or f"http_{status}"
            elif status is None:
                edge_reason = edge_reason or "network_error"
            settle_edge(
                parent_url,
                requested_url,
                followed=network_requested,
                reason=edge_reason,
            )
        if status is None or not 200 <= status < 300 or blocked_reason:
            if status is None:
                stop_reason = "network_error"
                break
            if status in {401, 403}:
                stop_reason = "login_or_captcha"
                break
            continue
        if not content_type.casefold().startswith(("text/html", "application/xhtml+xml")):
            continue
        html = body.decode("utf-8", errors="replace")
        if page_stop_reason(final_url, html):
            stop_reason = "login_or_captcha"
            break
        if depth >= policy.max_depth:
            # Keep the links as edges but never enqueue beyond the fixed depth.
            for link in extract_links(html, final_url, policy.allowed_domain):
                depth_limited = True
                edges.append(
                    {
                        "from_url": final_url,
                        "to_url": link["url"],
                        "kind": link["kind"],
                        "method": "GET",
                        "risk_tier": "SAFE",
                        "followed": False,
                        "reason": link["reason"] or "depth_limit",
                    }
                )
            continue
        for link in extract_links(html, final_url, policy.allowed_domain):
            target, reason = link["url"], link["reason"]
            if reason:
                edges.append(
                    {
                        "from_url": final_url,
                        "to_url": target,
                        "kind": link["kind"],
                        "method": "GET",
                        "risk_tier": "SAFE",
                        "followed": False,
                        "reason": reason,
                    }
                )
                continue
            if target in queued:
                edges.append(
                    {
                        "from_url": final_url,
                        "to_url": target,
                        "kind": link["kind"],
                        "method": "GET",
                        "risk_tier": "SAFE",
                        "followed": False,
                        "reason": "already_queued",
                    }
                )
                continue
            queued.add(target)
            queue.append((target, depth + 1, final_url, link["kind"]))
            edges.append(
                {
                    "from_url": final_url,
                    "to_url": target,
                    "kind": link["kind"],
                    "method": "GET",
                    "risk_tier": "SAFE",
                    "followed": False,
                    "reason": "pending",
                }
            )

    if queue and attempted_pages >= policy.page_cap:
        for edge in edges:
            if edge.get("reason") == "pending":
                edge.update(followed=False, reason="page_cap")
    if stop_reason == "queue_exhausted" and queue:
        stop_reason = "page_cap" if attempted_pages >= policy.page_cap else "depth_limit"
    elif stop_reason == "queue_exhausted" and depth_limited:
        stop_reason = "depth_limit"
    return {
        "attempted_pages": attempted_pages,
        "fetched_pages": sum(1 for page in pages if page["status"] is not None),
        "max_depth": policy.max_depth,
        "page_cap": policy.page_cap,
        "delay_seconds": policy.delay_seconds,
        "redirects": redirects,
        "max_redirects_total": policy.max_redirects_total,
        "stop_reason": stop_reason,
        "last_status": last_status,
        "pages": pages,
        "robots": robot_records,
        "edges": edges,
        "blocked": blocked,
    }
