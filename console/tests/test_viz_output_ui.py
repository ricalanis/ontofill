"""Output, Entities, entity and lineage pages in a real browser at 1280 and 390 px: no horizontal page scroll."""

import socket
import threading
import time

import pytest
import uvicorn
from conftest import spec_for

from ontofill_console.web import create_app, settings_from_env

pytestmark = pytest.mark.ui
playwright = pytest.importorskip("playwright.sync_api")

PAGES = ("/cases/libraries/output", "/cases/libraries/output?all=1", "/cases/parks/output",
         "/cases/libraries/entities", "/cases/parks/entities", "/cases/libraries/entities/lib%3Afixture-001",
         "/cases/libraries/entities/lib%3Afixture-024", "/cases/libraries/lineage/val%3Alib-001-name",
         "/cases/libraries/lineage/val%3Alib-024-opening_hours")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(cases_dir):
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
def test_output_and_entity_pages_fit(server, width):
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
        # the heatmap's cells are links into the entity page, anchored at the property
        page.goto(server + "/cases/libraries/output")
        page.locator(".heat a.cell--gold").first.click()
        page.wait_for_url("**/entities/**#p-*")
        assert page.locator(".prop:target").count() == 1
        assert not errors, errors
        browser.close()
