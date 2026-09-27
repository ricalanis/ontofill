"""Synthetic tests for the disposable browser pod's navigation recorder."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from urllib.parse import urljoin


def test_redirect_location_is_retained_when_navigation_raises_before_target_request(
    tmp_path, monkeypatch
) -> None:
    target = "https://catalog.example.test/start"
    blocked_location = "/dependency/landing"
    blocked_target = urljoin(target, blocked_location)

    class PlaywrightError(Exception):
        pass

    class FakeRequest:
        def __init__(self, page) -> None:
            self.frame = page.main_frame
            self.url = target

        def is_navigation_request(self) -> bool:
            return True

    class FakeResponse:
        def __init__(self, request) -> None:
            self.request = request
            self.status = 302
            self.url = target

        async def header_value(self, name: str) -> str:
            assert name == "location"
            return blocked_location

    class FakePage:
        def __init__(self) -> None:
            self.main_frame = object()
            self.url = target
            self.handlers = {}

        def on(self, event: str, handler) -> None:
            self.handlers[event] = handler

        async def goto(self, url: str, **kwargs):
            request = FakeRequest(self)
            self.handlers["request"](request)
            self.handlers["response"](FakeResponse(request))
            # Chromium fails before emitting the redirected request event.
            raise PlaywrightError("blocked redirected navigation")

    page = FakePage()

    class FakeContext:
        async def route(self, pattern: str, handler) -> None:
            self.route_handler = handler

        async def new_page(self):
            return page

    class FakeBrowser:
        async def new_context(self, **kwargs):
            return FakeContext()

        async def close(self) -> None:
            pass

    class FakeChromium:
        async def launch(self, **kwargs):
            return FakeBrowser()

    class FakePlaywright:
        def __init__(self) -> None:
            self.chromium = FakeChromium()

    class FakePlaywrightContext:
        async def __aenter__(self):
            return FakePlaywright()

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            pass

    playwright_package = types.ModuleType("playwright")
    playwright_package.__path__ = []
    async_api = types.ModuleType("playwright.async_api")
    async_api.Error = PlaywrightError
    async_api.async_playwright = lambda: FakePlaywrightContext()
    monkeypatch.setitem(sys.modules, "playwright", playwright_package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)

    pod_dir = Path(__file__).resolve().parents[1] / "sandbox" / "agent-pod"
    monkeypatch.syspath_prepend(str(pod_dir))
    module_path = pod_dir / "capture.py"
    spec = importlib.util.spec_from_file_location("synthetic_agent_pod_capture", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    real_path = Path
    monkeypatch.setattr(
        module, "Path", lambda value: tmp_path if value == "/out" else real_path(value)
    )
    monkeypatch.setattr(module, "pod_identity", lambda: {"hostname": "synthetic-pod"})
    monkeypatch.setattr(module, "isolation_probes", lambda proxy: {"network": {}, "writes": {}})
    monkeypatch.setattr(
        module,
        "secret_probes",
        lambda: {
            "env_keys_found": 0,
            "files_with_keys": 0,
            "metadata_ip": "BLOCKED",
            "mesh": "BLOCKED",
        },
    )
    monkeypatch.setenv("CAPTURE_URL", target)
    monkeypatch.setenv("PROXY_URL", "http://egress:8888")
    monkeypatch.setenv("CAPTURE_MAX_STEPS", "3")

    asyncio.run(module.capture())

    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["navigation_error"] == "PlaywrightError"
    assert result["redirect_chain"] == [target, blocked_target]
