"""The interface every browser backend implements (native Playwright loop, Skyvern cells).

A backend is the "hands": it observes a page and executes one concrete action. It never calls a model and never
decides whether an action is allowed; the controller's loop does that (plan → guard → act → verify).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from shared.gateway_client import GatewayError

# Actions the planner can request. READ_ACTIONS never change remote state and skip the action gate.
# Errors a model call through the gateway can raise, including a malformed body.
CALL_ERRORS = (GatewayError, httpx.HTTPError, KeyError, IndexError, TypeError, ValueError)
ACTIONS = ("navigate", "click", "type", "select", "scroll", "back", "extract", "done")
READ_ACTIONS = frozenset({"scroll", "extract", "done"})
ELEMENT_KINDS = ("link", "button", "input", "select", "submit")


@dataclass
class Element:
    id: str  # stable within a page load: e<n>, kept in a data-ba-id attribute
    kind: str  # link | button | input | select | submit
    role: str  # tag name or ARIA role
    name: str  # accessible text: aria-label, text, value or placeholder
    selector: str
    href: str | None = None
    input_type: str | None = None
    form: dict | None = None  # {"method", "action", "search": bool} when inside a <form>

    def brief(self) -> str:
        extra = f" -> {self.href}" if self.href else ""
        form = ""
        if self.form:
            form = f" [form {self.form.get('method', 'get').upper()}{' search' if self.form.get('search') else ''}]"
        return f"{self.id} {self.kind} \"{self.name[:80]}\"{extra}{form}"


@dataclass
class Observation:
    url: str
    title: str
    text: str  # visible text, truncated
    elements: list[Element] = field(default_factory=list)
    screenshot_png: bytes = b""
    screenshot_key: str | None = None  # set by the controller once stored
    blocked_hosts: list[str] = field(default_factory=list)

    def element(self, element_id: str | None) -> Element | None:
        return next((e for e in self.elements if e.id == element_id), None)

    def summary(self) -> dict:
        return {"url": self.url, "title": self.title, "elements": len(self.elements),
                "text_chars": len(self.text), "screenshot_key": self.screenshot_key}


@dataclass
class Action:
    tool: str
    args: dict = field(default_factory=dict)

    @property
    def element_id(self) -> str | None:
        return self.args.get("element_id")

    def describe(self, obs: Observation | None = None) -> str:
        """One line for the gate, the approval file and the planner history."""
        el = obs.element(self.element_id) if obs else None
        target = f" {el.kind} \"{el.name[:80]}\"" if el else (f" {self.element_id}" if self.element_id else "")
        if self.tool == "navigate":
            return f"navigate to {self.args.get('url')}"
        if self.tool == "type":
            return f"type \"{str(self.args.get('text', ''))[:80]}\" into{target}"
        if self.tool == "select":
            return f"select \"{self.args.get('value')}\" in{target}"
        if self.tool == "extract":
            return f"extract {', '.join(self.args.get('fields', []) or [])}"
        host = f" on {urlsplit(obs.url).hostname}" if obs and obs.url else ""
        return f"{self.tool}{target}{host}"

    def as_dict(self) -> dict:
        return {"tool": self.tool, "args": self.args}


@dataclass
class ActResult:
    ok: bool
    url: str | None = None
    error: str | None = None
    values: dict | None = None  # extract: {field: {"value", "selector"}}
    blocked_hosts: list[str] = field(default_factory=list)
    http_status: int | None = None  # navigate/back: the main document's final HTTP status (R36)
    elapsed_ms: int | None = None  # navigate/back: wall time of the navigation, success or failure

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, [], {})} | {"ok": self.ok}


class Backend(Protocol):
    """One browser for one session. Calls come from a single thread (the session's worker)."""

    def open(self, session: dict) -> None:
        """session: {session_id, allowed_domains, start_url?, cdp_url?}"""

    def observe(self) -> Observation: ...

    def act(self, action: Action) -> ActResult: ...

    def close(self) -> None: ...


def host_allowed(host: str | None, allowed_domains: list[str]) -> bool:
    """A host is allowed when it equals an allowed domain or is a subdomain of one."""
    if not host:
        return False
    host = host.lower().rstrip(".")
    for d in allowed_domains:
        d = d.lower().strip().lstrip(".")
        if host == d or host.endswith("." + d):
            return True
    return False
