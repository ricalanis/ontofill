"""Entity graph in a real browser: no horizontal page scroll at 1280 and 390 px, the SVG scrolls inside its own
wrapper, hover highlights neighbours, a click selects in place, and the keyboard path (Tab, Space, arrows, Enter)."""

import socket
import threading
import time

import pytest
import uvicorn
from conftest import spec_for
from test_viz_graph import write_scale_run

from ontofill_console.web import create_app, settings_from_env

pytestmark = pytest.mark.ui
playwright = pytest.importorskip("playwright.sync_api")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(cases_dir):
    write_scale_run(cases_dir)
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


PAGES = (
    "/cases/libraries/graph",
    "/cases/libraries/graph?focus=lib%3Afixture-001&depth=2&sel=lib%3Afixture-001",
    "/cases/libraries/graph?signal=hours_not_published",
    "/cases/parks/graph",
    "/cases/libraries/graph?run=run-scale-0500",
    "/cases/libraries/graph?run=run-scale-0500&limit=500",
)


@pytest.mark.parametrize("width", [1280, 390])
def test_graph_pages_fit(server, width):
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
            if page.locator("#ggraph").count():
                wrap = page.evaluate(
                    "(() => { const w = document.querySelector('.gwrap'); "
                    "return [w.clientWidth, w.getBoundingClientRect().right]; })()"
                )
                assert wrap[1] <= width + 0.5, (url, wrap)
        # the list fallback is on the page at phone width
        page.goto(server + "/cases/libraries/graph")
        assert page.locator(".glist li a").first.is_visible()
        assert not errors, errors
        browser.close()


def test_graph_interaction_and_keyboard(server):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(server + "/cases/libraries/graph")
        # hover highlights the node's neighbours
        node = page.locator('.gnode[data-id="op:fixture-1"]')
        node.hover()
        assert "hl" in page.get_attribute("#ggraph", "class")
        assert page.locator(".gnode.is-nb").count() >= 6
        # a pointer click selects in place (no navigation), the panel shows the entity
        url_before = page.url
        node.click()
        page.wait_for_selector("#graph-panel >> text=Open entity with evidence")
        assert "/graph" in page.url and "sel=op%3Afixture-1" in page.url and page.url != url_before
        # an edge click shows the link's evidence values
        page.locator(".gedge--link").first.dispatch_event("click", {"button": 0, "detail": 1})
        page.wait_for_selector("#graph-panel >> text=Why is this value here?")
        # keyboard: nodes are focusable links with labels; Space selects, arrows move, Enter opens the entity
        page.focus('.gnode[data-id="lib:fixture-001"]')
        active = page.evaluate("document.activeElement.getAttribute('aria-label')")
        assert active and "Biblioteca Ejemplo 01" in active
        page.keyboard.press("Space")
        page.wait_for_selector("#graph-panel h3.gp-title >> text=Biblioteca Ejemplo 01")
        page.keyboard.press("ArrowRight")
        moved = page.evaluate("document.activeElement.dataset.id")
        assert moved and moved != "lib:fixture-001"
        page.keyboard.press("Enter")
        page.wait_for_url("**/entities/**")
        assert page.locator("h1").count() == 1
        assert not errors, errors
        browser.close()


def test_graph_works_without_js(server):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(java_script_enabled=False, viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto(server + "/cases/libraries/graph")
        page.locator(".glist-links a:text('evidence')").first.click()
        page.wait_for_selector("#graph-panel >> text=Why is this value here?")
        page.select_option("select[name=cls]", "operator")
        page.click("button:text('Apply')")
        page.wait_for_url("**cls=operator**")
        assert page.locator(".gnode").count() == 4
        browser.close()
