"""Headless browser: the Inference views fit 1280 and 390 px without horizontal page scroll, with and without the
gateway log, and the run page's Inference link leads to the view."""

import socket
import threading
import time

import pytest
import uvicorn
from test_viz_inference import LIB_RUN, world  # noqa: F401  (the fixture is reused)

from ontofill_console.viz import inference
from ontofill_console.web import create_app, settings_from_env

pytestmark = pytest.mark.ui
playwright = pytest.importorskip("playwright.sync_api")

PAGES = [
    "/cases/libraries/inference",
    "/cases/parks/inference",
    "/cases/clean/inference",
    "/cases/dirty/inference",
    "/inference",
    f"/cases/libraries/inference?run={LIB_RUN}",
    f"/cases/libraries/runs/{LIB_RUN}",
    "/cases/libraries/cost",
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(spec: str):
    app = create_app(settings_from_env({"ONTOFILL_CONSOLE_CASES": spec}))
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    return srv, thread, f"http://127.0.0.1:{port}"


@pytest.mark.parametrize("mounted", [True, False])
def test_inference_fits_desktop_and_phone(world, monkeypatch, mounted):  # noqa: F811
    if mounted:
        monkeypatch.setenv(inference.LOG_ENV, str(world["log"].parent))
    else:
        monkeypatch.delenv(inference.LOG_ENV, raising=False)
    srv, thread, base = _serve(world["spec"])
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            for width in (1280, 390):
                page.set_viewport_size({"width": width, "height": 900})
                for url in PAGES:
                    resp = page.goto(base + url)
                    assert resp.status == 200, url
                    scroll = page.evaluate("document.documentElement.scrollWidth")
                    assert scroll <= width, (url, width, scroll, mounted)
            page.set_viewport_size({"width": 1280, "height": 900})
            page.goto(f"{base}/cases/libraries/runs/{LIB_RUN}")
            page.click("text=Inference:")
            assert "/cases/libraries/inference" in page.url
            assert page.locator("h1").first.inner_text().startswith("Who reasoned")
            assert not errors, errors
            browser.close()
    finally:
        srv.should_exit = True
        thread.join(timeout=5)
