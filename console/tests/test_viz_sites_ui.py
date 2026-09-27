"""Site graphs in a real browser: no horizontal page scroll at 1280 and 390 px, the drawing scrolls inside its own
wrapper, keyboard selection (focus a node, Enter) and the coverage overlay work without a reload."""

import socket
import threading
import time

import pytest
import uvicorn
from conftest import spec_for
from test_viz_sites import SOURCE, bronze_keys, install_graph, recorded_graph

from ontofill_console.web import create_app, settings_from_env

pytestmark = pytest.mark.ui
playwright = pytest.importorskip("playwright.sync_api")

PAGES = ("/cases/libraries/sites", f"/cases/libraries/sites/{SOURCE}", "/cases/parks/sites",
         "/cases/libraries/sites/empty-example")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(cases_dir):
    imgs, htmls = bronze_keys(cases_dir / "libraries" / "lake")
    install_graph(cases_dir, recorded_graph(imgs, htmls))
    install_graph(cases_dir, None, source="empty-example", raw="{}")
    app = create_app(settings_from_env({"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir)}))
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.mark.parametrize("width", [1280, 390])
def test_site_pages_fit(server, width):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for url in PAGES:
            resp = page.goto(server + url)
            assert resp.status == 200, url
            sw = page.evaluate("Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)")
            assert sw <= width, (url, width, sw)
        page.goto(f"{server}/cases/libraries/sites/{SOURCE}")
        wrap = page.locator(".sg-wrap")
        if width == 390:  # the drawing scrolls inside its wrapper; the list fallback is there and readable
            assert wrap.evaluate("el => el.scrollWidth > el.clientWidth")
            assert page.locator(".sg-list details").count() == 5
            assert page.locator(".sg-list details").first.is_visible()
        assert not errors, errors
        browser.close()


def test_keyboard_select_and_overlay(server):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server}/cases/libraries/sites/{SOURCE}")
        assert page.locator(".sg-drill:visible").count() == 0 and page.locator("#sg-pick").is_visible()
        node = page.locator(".sg-node[aria-label^='Library detail page']")
        node.focus()
        assert page.evaluate("document.activeElement.getAttribute('aria-label')").startswith("Library detail page")
        url_before = page.url
        page.keyboard.press("Enter")
        drill = page.locator(".sg-drill:visible")
        drill.wait_for()
        assert drill.count() == 1 and "Library detail page" in drill.inner_text()
        assert node.get_attribute("aria-current") == "true"
        assert "type=type-" in page.url and page.url.split("?")[0] == url_before.split("?")[0]  # no reload, URL kept
        assert drill.locator("img").count() >= 1 and drill.locator("a[href*='/runs/']").count() >= 1
        assert not page.locator("#sg-pick").is_visible()
        # Tab reaches the next node, Enter moves the selection
        page.keyboard.press("Tab")
        page.keyboard.press("Enter")
        assert page.locator(".sg-drill:visible").count() == 1
        assert node.get_attribute("aria-current") is None
        # coverage overlay without a reload
        page.select_option("#sg-ov", "name")
        assert "cov-covered" in node.get_attribute("class")
        assert node.locator(".sg-glyph").text_content() == "●"
        assert page.locator("#sg-covlegend").is_visible()
        assert page.locator(".sg-covsum[data-prop='name']").is_visible()
        assert "overlay=name" in page.url
        search = page.locator(".sg-node[aria-label^='Search page']")
        assert "cov-none" in search.get_attribute("class") and "Coverage: none" in search.get_attribute("aria-label")
        page.select_option("#sg-ov", "")
        assert not page.locator("#sg-covlegend").is_visible() and node.locator(".sg-glyph").text_content() == ""
        assert not errors, errors
        browser.close()


def test_works_without_js(server):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(java_script_enabled=False, viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(f"{server}/cases/libraries/sites/{SOURCE}")
        page.locator(".sg-node[aria-label^='Library detail page']").click()
        page.wait_for_url("**type=type-*")
        assert page.locator(".sg-drill:visible").count() == 1
        page.select_option("#sg-ov", "name")
        page.click(".sg-overlay button")
        page.wait_for_url("**overlay=name*")
        assert "cov-covered" in page.locator(".sg-node[aria-label^='Library detail page']").get_attribute("class")
        assert page.locator(".sg-drill:visible").count() == 1  # the selection survived the overlay form
        browser.close()
