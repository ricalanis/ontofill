"""NativeBackend on real headless Chromium against the loopback fixture site."""

from __future__ import annotations

import pytest
from test_controller_support import Site

from backends.native.backend import NativeBackend, is_search_form
from controller.backend import Action

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def site():
    with Site() as s:
        yield s


@pytest.fixture
def browser():
    b = NativeBackend()
    b.open({"session_id": "t", "allowed_domains": ["127.0.0.1"]})
    yield b
    b.close()


def test_elements_and_forms(site, browser):
    assert browser.act(Action("navigate", {"url": site.url("search.html")})).ok
    o = browser.observe()
    names = {e.name: e for e in o.elements}
    assert names["Buscar"].kind == "submit" and names["Buscar"].form["search"] is True
    assert names["Entidad Ejemplo 01"].kind == "link" and names["Entidad Ejemplo 01"].href.endswith("/detail.html")
    assert o.screenshot_png.startswith(b"\x89PNG") and "Registro público" in o.text
    again = browser.observe()
    assert [e.id for e in again.elements] == [e.id for e in o.elements]  # ids stable within a page load
    browser.act(Action("navigate", {"url": site.url("submit.html")}))
    send = next(e for e in browser.observe().elements if e.name == "Send complaint")
    assert send.kind == "submit" and send.form == {"method": "post", "action": f"{site.base}/complaint",
                                                   "search": False}


def test_allowlist_blocks_and_records(site, browser):
    r = browser.act(Action("navigate", {"url": "http://evil.invalid/exfil"}))
    assert not r.ok and "evil.invalid" in r.blocked_hosts
    browser.act(Action("navigate", {"url": site.url("hostile.html")}))
    o = browser.observe()
    assert "evil.invalid" in browser.blocked
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in o.text  # observed faithfully; screening happens upstream


def test_type_click_extract(site, browser):
    browser.act(Action("navigate", {"url": site.url("search.html")}))
    els = {e.name: e.id for e in browser.observe().elements}
    assert browser.act(Action("type", {"element_id": els["Nombre de la entidad"], "text": "Entidad"})).ok
    r = browser.act(Action("click", {"element_id": els["Buscar"]}))
    assert r.ok and "results.html?q=Entidad" in r.url
    els = {e.name: e.id for e in browser.observe().elements}
    assert browser.act(Action("click", {"element_id": els["Entidad Ejemplo 01"]})).url.endswith("/detail.html")
    got = browser.act(Action("extract", {"fields": {"legal_name": "#legal-name", "tax_id": "#tax-id",
                                                    "missing": "#nope"}}))
    assert got.ok and got.values["legal_name"]["value"] == "Entidad Ejemplo 01"
    assert got.values["tax_id"] == {"value": "XAXX010101000", "selector": "#tax-id"}
    assert got.values["missing"]["value"] is None
    assert browser.act(Action("back", {})).ok and browser.act(Action("scroll", {"direction": "down"})).ok


def test_start_url_off_allowlist_refused():
    b = NativeBackend()
    try:
        with pytest.raises(ValueError):
            b.open({"session_id": "t", "allowed_domains": ["127.0.0.1"], "start_url": "http://evil.invalid/"})
    finally:
        b.close()


def test_is_search_form():
    assert is_search_form({"role": "search", "method": "post"})
    assert is_search_form({"method": "get", "input_types": ["text"], "input_names": ["q"], "submit_names": ["Go"]})
    assert not is_search_form({"method": "post", "input_names": ["q"]})
    assert not is_search_form({"method": "get", "input_names": ["q"], "submit_names": ["Send complaint"]})
    assert not is_search_form({"method": "get", "input_types": ["password"], "input_names": ["q"]})
    assert not is_search_form({"method": "get", "has_textarea": True, "input_names": ["q"]})
