"""Headless browser: the tour fits 1280 and 390 px without horizontal page scroll for the full case and the brief-only
case, its steps collapse and expand from the keyboard, and the start button leads to step 1's view."""

import socket
import threading
import time

import pytest
import uvicorn
from conftest import spec_for

from ontofill_console.web import create_app, settings_from_env

pytestmark = pytest.mark.ui
playwright = pytest.importorskip("playwright.sync_api")

PAGES = ["/tour", "/tour?case=parks", "/tour?case=libraries&run=run-libraries-0001"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(cases_dir):
    app = create_app(settings_from_env({"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir)}))
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


def test_tour_fits_desktop_and_phone(server):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for width in (1280, 390):
            page.set_viewport_size({"width": width, "height": 900})
            for url in PAGES:
                resp = page.goto(server + url)
                assert resp.status == 200, url
                scroll = page.evaluate("document.documentElement.scrollWidth")
                assert scroll <= width, (url, width, scroll)
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(server + "/tour")
        first = page.locator("#q1 details")
        assert first.evaluate("d => d.open")
        page.focus("#q1 summary")
        page.keyboard.press("Enter")
        assert not first.evaluate("d => d.open")
        page.keyboard.press("Enter")
        assert first.evaluate("d => d.open")
        page.click("text=Start the tour")
        assert page.url.endswith("/inbox")
        assert not errors, errors
        browser.close()
