"""Headless browser: the failures, cost, learning and compare pages fit 1280 and 390 px without horizontal page scroll,
for the full case, the brief-only case, and a second run (so the run diff renders)."""

import socket
import threading
import time

import pytest
import uvicorn
from conftest import spec_for
from test_viz_health import R2, two_runs  # noqa: F401 (fixture)

from ontofill_console.web import create_app, settings_from_env

pytestmark = pytest.mark.ui
playwright = pytest.importorskip("playwright.sync_api")

PAGES = [
    "/cases/libraries/failures",
    "/cases/libraries/cost",
    "/cases/libraries/learning",
    "/cases/libraries/compare",
    f"/cases/libraries/failures?run={R2}",
    f"/cases/libraries/cost?run={R2}",
    "/cases/parks/failures",
    "/cases/parks/cost",
    "/cases/parks/learning",
    "/cases/parks/compare",
    "/compare",
    "/compare?a=parks&b=libraries",
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(two_runs):  # noqa: F811
    app = create_app(settings_from_env({"ONTOFILL_CONSOLE_CASES": spec_for(two_runs)}))
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


def test_health_pages_fit_desktop_and_phone(server):
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
        page.set_viewport_size({"width": 390, "height": 900})
        page.goto(server + "/compare")
        cols = page.eval_on_selector_all(".hv-cols > section", "els => els.map(e => e.getBoundingClientRect().left)")
        assert len(cols) == 2 and abs(cols[0] - cols[1]) < 1  # the two case columns stack on a phone
        assert not errors, errors
        browser.close()
