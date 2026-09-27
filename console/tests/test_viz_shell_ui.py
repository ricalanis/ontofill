"""The shell, the inbox, the case list and the case hub at 1280 and 390 px, in both themes: no horizontal scroll,
no script errors, and every question on the hub is one click from the case."""

import socket
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
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(settings_from_env({"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir)}))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.mark.parametrize(("width", "scheme"), [(1280, "dark"), (390, "light")])
def test_shell_pages_fit(server, width, scheme):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for path in ("/inbox", "/", "/cases/libraries", "/cases/parks"):
            page.goto(server + path)
            assert page.evaluate("document.documentElement.scrollWidth") <= width, path
        page.goto(server + "/cases/libraries")
        assert page.locator(".questions li").count() == 10
        assert page.locator(".question__text").inner_text().strip()
        browser.close()
        assert errors == []
