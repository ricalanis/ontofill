"""The recorded real-shaped run in a real browser: no horizontal page scroll at 1280 and 390 px on every console page
for that run (the run page, Operation, Failures and the entity graph are the heaviest), and no script errors.

Set REALRUN_SHOTS=<dir> to also save full-page screenshots (1280 dark, 390 light) of the heaviest pages."""

import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from test_viz_realrun import CASE, RUN, _entity_id, spec_with_realrun

from ontofill_console.web import create_app, settings_from_env

pytestmark = pytest.mark.ui
playwright = pytest.importorskip("playwright.sync_api")

HEAVY = {
    "run": f"/cases/{CASE}/runs/{RUN}",
    "operation": f"/cases/{CASE}/operation",
    "failures": f"/cases/{CASE}/failures",
    "graph": f"/cases/{CASE}/graph",
    "inbox": "/inbox",
}
PAGES = [
    *HEAVY.values(),
    f"/cases/{CASE}",
    f"/cases/{CASE}/runs",
    f"/cases/{CASE}/definition",
    f"/cases/{CASE}/discovery",
    f"/cases/{CASE}/pages",
    f"/cases/{CASE}/sites",
    f"/cases/{CASE}/output",
    f"/cases/{CASE}/entities",
    f"/cases/{CASE}/entities/{_entity_id('L-103')}",
    f"/cases/{CASE}/cost",
    f"/cases/{CASE}/learning",
    f"/cases/{CASE}/compare",
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(cases_dir):
    app = create_app(
        settings_from_env(
            {"ONTOFILL_CONSOLE_CASES": spec_with_realrun(cases_dir), "ONTOFILL_CONSOLE_IDENTITY": "local"}
        )
    )
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
def test_real_run_pages_fit(server, width):
    shots = os.environ.get("REALRUN_SHOTS")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": width, "height": 900}, color_scheme="dark" if width == 1280 else "light"
        )
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for url in PAGES:
            resp = page.goto(server + url)
            assert resp.status == 200, url
            sw = page.evaluate("Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)")
            assert sw <= width, (url, width, sw)
        if shots:
            out = Path(shots)
            out.mkdir(parents=True, exist_ok=True)
            for name, url in HEAVY.items():
                page.goto(server + url)
                page.screenshot(path=str(out / f"{name}-{width}.png"), full_page=True)
        assert not errors, errors
        browser.close()


def test_approvals_list_fits_phone_with_real_action_dirs(server):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 900})
        page.goto(f"{server}/cases/{CASE}/approvals")
        sw = page.evaluate("Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)")
        browser.close()
        assert sw <= 390, sw
