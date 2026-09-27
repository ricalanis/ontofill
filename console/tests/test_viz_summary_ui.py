"""Summary in a real browser at 1280 and 390 px: no horizontal page scroll, receipts' thumbnails load, the copy-as-text
block opens, and the print rendering drops the navigation while keeping the meters and the receipts."""

import socket
import threading
import time

import pytest
import uvicorn
from conftest import spec_for
from test_viz_summary import build_recorded_run

from ontofill_console.web import create_app, settings_from_env

pytestmark = pytest.mark.ui
playwright = pytest.importorskip("playwright.sync_api")

RID = "run-libraries-0002"
PAGES = ("/cases/libraries/summary", "/cases/parks/summary", f"/cases/libraries/summary?run={RID}")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(cases_dir):
    build_recorded_run(cases_dir, RID)
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
def test_summary_fits_and_prints(server, width):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for url in PAGES:
            resp = page.goto(server + url)
            assert resp.status == 200, url
            page.wait_for_load_state("networkidle")
            sw = page.evaluate("Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)")
            assert sw <= width, (url, width, sw)
        page.goto(server + "/cases/libraries/summary")
        page.wait_for_load_state("networkidle")
        # every receipt thumbnail actually loaded from bronze
        loaded = page.evaluate(
            "[...document.querySelectorAll('.sum-shot img')].map(i => i.complete && i.naturalWidth > 0)"
        )
        assert loaded and all(loaded)
        # copy as text opens and shows the plain rendering
        page.locator(".sum-copy summary").click()
        assert "Definition of done" in page.locator(".sum-copy pre").inner_text()
        sw = page.evaluate("Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)")
        assert sw <= width, ("copy open", width, sw)
        # print: no navigation, no live chrome, the meters and receipts stay
        page.emulate_media(media="print")
        for sel in (".masthead", ".subnav", ".colophon", ".sum-copy"):
            assert not page.locator(sel).is_visible(), sel
        assert page.locator(".sum-receipt").first.is_visible()
        assert page.locator(".sum-crit .meter").first.is_visible()
        assert page.locator("#failures .strip").first.is_visible()
        if width == 1280:  # the print rendering renders to paper (headless Chromium)
            assert len(page.pdf(format="A4")) > 1000
        assert not errors, errors
        browser.close()
