"""Headless browser smoke: cases list → run view → approval page, as a signed-in approver (header injected)."""

import threading
import time

import pytest
import uvicorn
from conftest import spec_for

from ontofill_console.web import create_app, settings_from_env

pytestmark = pytest.mark.ui
playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def server(cases_dir):
    app = create_app(settings_from_env({"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir)}))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8491, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    yield "http://127.0.0.1:8491"
    srv.should_exit = True
    thread.join(timeout=5)


def test_approver_flow_and_phone_width(server):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(extra_http_headers={"X-NetBird-User": "ana@example.org"})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server}/")
        page.click("text=Public library access") if page.locator("text=Public library access").count() else page.click(
            "a[href='/cases/libraries']"
        )
        page.click("a[href$='/runs/run-libraries-0001']")
        page.wait_for_selector("#steps li")
        assert page.locator("details").count() >= 1  # the loop thread
        page.goto(f"{server}/cases/libraries/approvals/01-scope")
        assert page.locator("text=Signing as").count() == 1
        assert page.locator("input[name^='artifact_sha256.']").count() == 2
        page.fill("#reason", "target has no basis")
        page.click("button[value='deny']")
        page.wait_for_url("**/approvals?done=01-scope")
        assert page.locator("text=Decision history").count() == 1
        for url in (
            "/",
            "/cases/libraries",
            "/cases/libraries/runs/run-libraries-0001",
            "/cases/libraries/approvals/02-ontology",
            "/evidence",
        ):
            page.set_viewport_size({"width": 390, "height": 800})
            page.goto(server + url)
            width = page.evaluate("document.documentElement.scrollWidth")
            assert width <= 390, (url, width)
        assert not errors, errors
        browser.close()
