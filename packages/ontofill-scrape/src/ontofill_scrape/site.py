"""Read captured HTML without navigating or submitting forms."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag
from lxml import html as lxml_html

from .models import FailureKind, PageElement, PageForm, PageLink, PageSnapshot, ToolFailure

_ACTIONABLE = "a[href], button, input, select, textarea, [role=button], [role=link]"
_BLOCKED_TEXT = re.compile(
    r"captcha|verify you are human|sign in to continue|log in to continue|login required",
    re.IGNORECASE,
)


def _soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one('input[type="password"], [class*="captcha"], [id*="captcha"]'):
        raise ToolFailure(FailureKind.BLOCKED, "login or captcha wall detected")
    body = soup.body or soup
    visible = body.get_text(" ", strip=True)
    if _BLOCKED_TEXT.search(visible[:3000]):
        raise ToolFailure(FailureKind.BLOCKED, "login or captcha wall detected")
    return soup


def _path(tag: Tag) -> str:
    parts: list[str] = []
    current: Tag | None = tag
    while current and current.name and current.name != "[document]":
        parent = current.parent
        if isinstance(parent, Tag):
            siblings = [
                child
                for child in parent.children
                if isinstance(child, Tag) and child.name == current.name
            ]
            index = siblings.index(current) + 1
        else:
            index = 1
        parts.append(f"{current.name}:nth-of-type({index})")
        current = parent if isinstance(parent, Tag) else None
    return " > ".join(reversed(parts))


def _element(tag: Tag) -> PageElement:
    attrs = {
        key: " ".join(value) if isinstance(value, list) else str(value)
        for key, value in tag.attrs.items()
        if key in {"id", "name", "type", "href", "aria-label"}
    }
    text = tag.get_text(" ", strip=True)
    name = attrs.get("aria-label") or attrs.get("name") or text or attrs.get("title", "")
    role = str(
        tag.get("role")
        or {"a": "link", "button": "button", "input": "textbox"}.get(tag.name, tag.name)
    )
    return PageElement(tag.name, text, role, name, _path(tag), attrs)


def page_snapshot(html: str, url: str) -> PageSnapshot:
    soup = _soup(html)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    body = soup.body or soup
    text = body.get_text(" ", strip=True)
    elements = tuple(_element(tag) for tag in soup.select(_ACTIONABLE))
    skeleton = " ".join(tag.name for tag in soup.find_all(True))
    parsed = urlsplit(url)
    path = re.sub(r"\b\d+\b", "{id}", parsed.path)
    template = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return PageSnapshot(
        url, title, text, elements, hashlib.sha256(skeleton.encode()).hexdigest(), template
    )


def page_query(html: str, selector: str) -> tuple[PageElement, ...]:
    soup = _soup(html)
    try:
        if selector.startswith(("/", "(")):
            document = lxml_html.fromstring(html)
            paths = document.xpath(selector)
            results = []
            for node in paths:
                if isinstance(node, str):
                    results.append(
                        PageElement("text", node.strip(), "text", node.strip(), selector)
                    )
                elif hasattr(node, "tag"):
                    text = " ".join(node.itertext()).strip()
                    results.append(
                        PageElement(
                            str(node.tag),
                            text,
                            node.get("role") or str(node.tag),
                            node.get("aria-label") or text,
                            node.getroottree().getpath(node),
                        )
                    )
            return tuple(results)
        return tuple(_element(tag) for tag in soup.select(selector))
    except (ValueError, SyntaxError) as exc:
        raise ToolFailure(FailureKind.STALE_BINDING, f"invalid selector: {selector}") from exc


def page_forms(html: str, base_url: str) -> tuple[PageForm, ...]:
    soup = _soup(html)
    forms = []
    for form in soup.select("form"):
        method = str(form.get("method", "get")).lower()
        action = urljoin(base_url, str(form.get("action", "")))
        fields = tuple(
            str(tag.get("name")) for tag in form.select("input[name], select[name], textarea[name]")
        )
        hint = f"{action} {' '.join(fields)}".lower()
        safe = method == "get" and bool(re.search(r"search|query|filter|buscar|\bq\b", hint))
        forms.append(PageForm(action, method, fields, safe))
    return tuple(forms)


def page_links(html: str, base_url: str) -> tuple[PageLink, ...]:
    soup = _soup(html)
    links = []
    for tag in soup.select("a[href], link[href]"):
        url = urljoin(base_url, str(tag.get("href", "")))
        if urlsplit(url).scheme not in {"http", "https"}:
            continue
        rel = tag.get("rel") or []
        links.append(PageLink(url, tag.get_text(" ", strip=True), " ".join(rel)))
    return tuple(links)


def page_pagination(html: str, base_url: str) -> tuple[PageLink, ...]:
    links = page_links(html, base_url)
    return tuple(
        link
        for link in links
        if "next" in link.rel.lower()
        or re.search(r"\b(next|siguiente|more)\b|[›→]", link.text, re.IGNORECASE)
    )
