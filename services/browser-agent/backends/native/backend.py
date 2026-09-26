"""Native backend: our own Playwright driver (sync API).

In a cell it attaches to the gVisor browser pod over CDP (`cdp_url`); in development it launches a local headless
Chromium. Either way every request the page makes goes through a route handler that aborts hosts outside the
session's allowed domains (the pod's egress proxy enforces the same list one layer down) and records them.
All calls must come from one thread: the controller's Session guarantees that.
"""

from __future__ import annotations

import contextlib
import socket
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError

from controller.backend import Action, ActResult, Element, Observation, host_allowed
from controller.guard import COMMIT_WORDS, SEARCH_WORDS

SEARCH_NAMES = frozenset({"q", "query", "search", "s", "buscar", "busqueda", "búsqueda", "term", "terms", "keyword",
                          "keywords", "filter", "filtro", "text", "texto", "nombre", "name", "rfc", "page", "pagina"})
LOCAL_SCHEMES = ("data", "blob", "about")

COLLECT_JS = r"""
() => {
  const visible = el => {
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  window.__baNext = window.__baNext || 1;
  const out = [];
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link]';
  for (const el of document.querySelectorAll(sel)) {
    if (!visible(el)) continue;
    if (!el.dataset.baId) el.dataset.baId = 'e' + (window.__baNext++);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const role = el.getAttribute('role') || '';
    const f = el.form || el.closest('form');
    let kind;
    if (tag === 'a' || role === 'link') kind = 'link';
    else if (tag === 'select') kind = 'select';
    else if ((tag === 'button' && (type === 'submit' || (type === '' && f))) ||
             (tag === 'input' && (type === 'submit' || type === 'image'))) kind = 'submit';
    else if (tag === 'button' || role === 'button' || (tag === 'input' && (type === 'button' || type === 'reset')))
      kind = 'button';
    else kind = 'input';
    const name = (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('placeholder') ||
                  el.getAttribute('name') || el.getAttribute('title') || '').trim().replace(/\s+/g, ' ').slice(0, 120);
    let form = null;
    if (f) {
      form = {
        method: (f.getAttribute('method') || 'get').toLowerCase(), action: f.action || '',
        role: f.getAttribute('role') || '',
        input_types: [...f.querySelectorAll('input')].map(i => (i.getAttribute('type') || 'text').toLowerCase()),
        input_names: [...f.querySelectorAll('input,select,textarea')].map(i => (i.name || '').toLowerCase()),
        has_textarea: !!f.querySelector('textarea'),
        submit_names: [...f.querySelectorAll('button,input[type=submit]')]
          .map(b => (b.innerText || b.value || '').trim()),
      };
    }
    out.push({id: el.dataset.baId, kind, role: role || tag, name, href: tag === 'a' ? el.href : null,
              input_type: tag === 'input' ? (type || 'text') : (tag === 'textarea' ? 'textarea' : null), form});
  }
  return {title: document.title, text: document.body ? document.body.innerText : '', elements: out};
}
"""


def is_search_form(info: dict) -> bool:
    """A form the read-only archetype may submit: role=search, or a GET form with search-like inputs and no
    control that commits something. POST forms are never treated as searches (the approver decides)."""
    if (info.get("role") or "").lower() == "search":
        return True
    if info.get("method", "get") != "get":
        return False
    types = set(info.get("input_types") or [])
    if info.get("has_textarea") or types & {"password", "email", "file", "tel"}:
        return False
    if any(COMMIT_WORDS.search(n) and not SEARCH_WORDS.search(n) for n in info.get("submit_names") or []):
        return False
    names = set(info.get("input_names") or [])
    return bool("search" in types or names & SEARCH_NAMES
                or any(SEARCH_WORDS.search(n) for n in info.get("submit_names") or []))



def _free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

class NativeBackend:
    def __init__(self, cdp_url: str | None = None, headless: bool = True, viewport: tuple[int, int] = (1280, 800),
                 text_limit: int = 12000, timeout_ms: int = 15000, debug_port: bool = False):
        self.cdp_url = cdp_url
        # Local launch only: also open a loopback DevTools port so a second, read-only client (the live view) can
        # screencast this browser. A cell's browser is already reached over its own cdp_url.
        self.debug_port = debug_port
        self.cdp_endpoint: str | None = None
        self.headless = headless
        self.viewport = viewport
        self.text_limit = text_limit
        self.timeout_ms = timeout_ms
        self.nav_grace_ms = 300  # how long after a click a navigation may take to start
        self._nav = {"requested": 0, "committed": 0}
        self.allowed_domains: list[str] = []
        self.blocked: list[str] = []  # every blocked host, in order of first attempt
        self._new_blocked: list[str] = []
        self._pw = self._browser = self._context = self.page = None

    # --- lifecycle ------------------------------------------------------------------------------------------
    def open(self, session: dict) -> None:
        from playwright.sync_api import sync_playwright

        self.allowed_domains = list(session.get("allowed_domains") or [])
        cdp_url = session.get("cdp_url") or self.cdp_url
        self._pw = sync_playwright().start()
        if cdp_url:
            self._browser = self._pw.chromium.connect_over_cdp(cdp_url, timeout=self.timeout_ms)
            self.cdp_endpoint = cdp_url
        elif self.debug_port:
            port = _free_port()
            self._browser = self._pw.chromium.launch(
                headless=self.headless, args=["--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={port}"])
            self.cdp_endpoint = f"http://127.0.0.1:{port}"
        else:
            self._browser = self._pw.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            viewport={"width": self.viewport[0], "height": self.viewport[1]}, accept_downloads=False,
            service_workers="block")
        self._context.route("**/*", self._route)
        self._context.on("page", self._on_page)
        self.page = self._context.new_page()
        self._wire(self.page)
        start = session.get("start_url")
        if start:
            if not host_allowed(urlsplit(start).hostname, self.allowed_domains):
                raise ValueError(f"start_url host {urlsplit(start).hostname!r} is not an allowed domain")
            self.page.goto(start, wait_until="domcontentloaded", timeout=self.timeout_ms)

    def close(self) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                with contextlib.suppress(PlaywrightError):
                    closer.close()
        if self._pw is not None:
            self._pw.stop()
        self._pw = self._browser = self._context = self.page = None

    # --- network policy -------------------------------------------------------------------------------------
    def _route(self, route) -> None:
        url = route.request.url
        parts = urlsplit(url)
        if parts.scheme in LOCAL_SCHEMES or host_allowed(parts.hostname, self.allowed_domains):
            route.continue_()
            return
        host = parts.hostname or parts.scheme or url[:60]
        if host not in self.blocked:
            self.blocked.append(host)
        self._new_blocked.append(host)
        route.abort("blockedbyclient")

    def _on_page(self, page) -> None:
        """A popup or new tab becomes the page we drive (it is inside the same routed context)."""
        self._wire(page)
        self.page = page

    def _wire(self, page) -> None:
        page.on("dialog", lambda d: d.dismiss())
        # Count main-frame document requests and commits, so a click that starts a navigation can wait for it.
        page.on("request", lambda r: self._nav.__setitem__("requested", self._nav["requested"] + 1)
                if r.is_navigation_request() and r.frame == page.main_frame else None)
        page.on("framenavigated", lambda f: self._nav.__setitem__("committed", self._nav["committed"] + 1)
                if f == page.main_frame else None)

    def _drain_blocked(self) -> list[str]:
        out, self._new_blocked = sorted(set(self._new_blocked)), []
        return out

    # --- observe --------------------------------------------------------------------------------------------
    def observe(self) -> Observation:
        page = self.page
        with contextlib.suppress(PlaywrightError):
            page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
        data = page.evaluate(COLLECT_JS)
        elements = []
        for e in data["elements"]:
            form = e.get("form")
            if form is not None:
                form = {"method": form["method"], "action": form["action"], "search": is_search_form(form)}
            elements.append(Element(id=e["id"], kind=e["kind"], role=e["role"], name=e["name"],
                                    selector=f'[data-ba-id="{e["id"]}"]', href=e.get("href"),
                                    input_type=e.get("input_type"), form=form))
        text = data["text"] or ""
        if len(text) > self.text_limit:
            text = text[: self.text_limit] + "\n[…truncated]"
        shot = page.screenshot(type="png", full_page=False, timeout=self.timeout_ms)
        return Observation(url=page.url, title=data["title"] or "", text=text, elements=elements,
                           screenshot_png=shot, blocked_hosts=self._drain_blocked())

    # --- act ------------------------------------------------------------------------------------------------
    def _selector(self, ref: str) -> str:
        ref = str(ref or "").strip()
        if ref.startswith("e") and ref[1:].isdigit():
            return f'[data-ba-id="{ref}"]'
        return ref

    def _settle(self, before: dict | None = None) -> None:
        """After a click: if it started a main-frame navigation (a document request within the grace window), wait
        for that navigation to commit and load, so the next observe tags the new page, not the old one."""
        with contextlib.suppress(PlaywrightError):
            if before is not None:
                waited = 0
                while self._nav["requested"] == before["requested"] and waited < self.nav_grace_ms:
                    self.page.wait_for_timeout(25)
                    waited += 25
                if self._nav["requested"] != before["requested"]:
                    waited = 0
                    while self._nav["committed"] == before["committed"] and waited < self.timeout_ms:
                        self.page.wait_for_timeout(25)
                        waited += 25
            self.page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)

    def act(self, action: Action) -> ActResult:
        page = self.page
        a = action.args
        try:
            if action.tool == "navigate":
                url = urljoin(page.url if page.url.startswith("http") else "", str(a.get("url", "")))
                if not host_allowed(urlsplit(url).hostname, self.allowed_domains):
                    host = urlsplit(url).hostname or url[:60]
                    if host not in self.blocked:
                        self.blocked.append(host)
                    return ActResult(False, page.url, f"blocked: {host} is not an allowed domain", blocked_hosts=[host])
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            elif action.tool == "click":
                before = dict(self._nav)
                page.locator(self._selector(a.get("element_id"))).first.click(timeout=self.timeout_ms)
                self._settle(before)
            elif action.tool == "type":
                page.locator(self._selector(a.get("element_id"))).first.fill(str(a.get("text", "")),
                                                                               timeout=self.timeout_ms)
            elif action.tool == "select":
                loc = page.locator(self._selector(a.get("element_id"))).first
                try:
                    loc.select_option(str(a.get("value", "")), timeout=self.timeout_ms)
                except PlaywrightError:
                    loc.select_option(label=str(a.get("value", "")), timeout=self.timeout_ms)
            elif action.tool == "scroll":
                page.mouse.wheel(0, -700 if a.get("direction") == "up" else 700)
            elif action.tool == "back":
                page.go_back(wait_until="domcontentloaded", timeout=self.timeout_ms)
            elif action.tool == "extract":
                return self._extract(dict(a.get("fields") or {}))
            elif action.tool == "done":
                pass
            else:
                return ActResult(False, page.url, f"unknown action {action.tool!r}")
        except PlaywrightError as exc:
            msg = str(exc).splitlines()[0][:300]
            blocked = self._drain_blocked()
            if "ERR_BLOCKED_BY_CLIENT" in msg and blocked:
                msg = f"blocked: {', '.join(blocked)} is not an allowed domain"
            return ActResult(False, self.page.url if self.page else None, msg, blocked_hosts=blocked)
        return ActResult(True, self.page.url, blocked_hosts=self._drain_blocked())

    def _extract(self, fields: dict) -> ActResult:
        values: dict[str, dict] = {}
        missing = []
        for name, ref in fields.items():
            selector = self._selector(ref)
            try:
                loc = self.page.locator(selector).first
                if loc.count() == 0:
                    raise LookupError("no match")
                tag = loc.evaluate("el => el.tagName.toLowerCase()")
                value = loc.input_value() if tag in ("input", "textarea", "select") else loc.inner_text()
                values[name] = {"value": value.strip() or None, "selector": selector}
            except (PlaywrightError, LookupError) as exc:
                values[name] = {"value": None, "selector": selector, "error": str(exc).splitlines()[0][:120]}
                missing.append(name)
        ok = len(missing) < len(fields) or not fields
        return ActResult(ok, self.page.url, None if ok else "no field found", values=values)
