"""Synthetic tests for the disposable browser pod's navigation recorder."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit

import pytest


def _import_capture_module(monkeypatch):
    class PlaywrightError(Exception):
        pass

    playwright_package = types.ModuleType("playwright")
    playwright_package.__path__ = []
    async_api = types.ModuleType("playwright.async_api")
    async_api.Error = PlaywrightError
    async_api.async_playwright = lambda: None
    monkeypatch.setitem(sys.modules, "playwright", playwright_package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)
    pod_dir = Path(__file__).resolve().parents[1] / "sandbox" / "agent-pod"
    monkeypatch.syspath_prepend(str(pod_dir))
    spec = importlib.util.spec_from_file_location(
        "synthetic_agent_pod_capture_helpers", pod_dir / "capture.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
            raise PlaywrightError(
                "net::ERR_BLOCKED_BY_CLIENT at https://catalog.example.test/start?token=synthetic-secret\n"
                "second line contains secret=do-not-copy"
            )

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
    assert len(result["navigation_attempts"]) == 1
    attempt = result["navigation_attempts"][0]
    assert attempt["http_status"] == 302
    assert attempt["elapsed_ms"] >= 0
    assert attempt["error"]["type"] == "PlaywrightError"
    assert "ERR_BLOCKED_BY_CLIENT" in attempt["error"]["message"]
    assert "synthetic-secret" not in attempt["error"]["message"]
    assert "second line" not in attempt["error"]["message"]


def test_navigation_error_redacts_bearer_and_quoted_json_tokens(monkeypatch):
    module = _import_capture_module(monkeypatch)
    message = module._safe_error_message(
        RuntimeError('Authorization: Bearer pod-secret "token":"json-secret"')
    )
    assert "pod-secret" not in message
    assert "json-secret" not in message
    assert "<redacted>" in message


def test_document_rescue_rejects_403_and_bounds_unknown_length_responses(tmp_path, monkeypatch):
    module = _import_capture_module(monkeypatch)

    class Context:
        async def cookies(self, url):
            return []

    class Response:
        status = 403

        def __init__(self):
            self.headers = {"content-type": "text/plain"}

    assert not module._is_document_response(Response())

    class CsvResponse:
        status = 200

        def __init__(self):
            self.headers = {"content-type": "text/csv; charset=utf-8"}

    assert module._is_document_response(CsvResponse())

    module._MAX_DOCUMENT_BYTES = 4

    class NoLengthResponse:
        def __init__(self):
            self.headers = {"content-type": "application/pdf"}
            self.read_limit = None

        def getcode(self):
            return 200

        def read(self, limit):
            self.read_limit = limit
            return b"12345"

        def close(self):
            pass

    response = NoLengthResponse()

    class Opener:
        def open(self, request, timeout):
            assert timeout == 30
            return response

    monkeypatch.setattr(module, "build_opener", lambda *handlers: Opener())
    result = asyncio.run(
        module._capture_document(
            Context(),
            "https://catalog.example.test/annex.pdf",
            tmp_path,
            proxy_url="http://egress:8888",
        )
    )
    assert result["capture_reason"] == "document_too_large"
    assert result["attempt"]["http_status"] == 200
    assert response.read_limit == 5
    assert not (tmp_path / "document.bin").exists()


def test_document_rescue_retains_one_follow_on_redirect_without_fetching_it(tmp_path, monkeypatch):
    module = _import_capture_module(monkeypatch)
    target = "https://catalog.example.test/download"
    follow_on = "https://identity.example.test/file?token=synthetic-secret"

    class Context:
        async def cookies(self, url):
            return []

    monkeypatch.setattr(
        module,
        "_read_document_response",
        lambda request, proxy: {
            "status": 302,
            "location": follow_on,
            "content_type": "application/pdf",
            "size_bytes": 0,
            "body": b"",
            "headers": {"content-type": "application/pdf", "location": follow_on},
        },
    )
    result = asyncio.run(
        module._capture_document(
            Context(), target, tmp_path, proxy_url="http://egress:8888", download=True
        )
    )
    assert result["capture_reason"] == "document_redirect_not_followed"
    assert result["redirect_target"] == follow_on
    assert result["attempt"]["http_status"] == 302
    assert not (tmp_path / "document.bin").exists()
    handler = module._NoRedirectHandler()
    assert handler.redirect_request(None, None, 302, "found", {}, follow_on) is None


@pytest.mark.parametrize("scheme, method", [("http", "GET"), ("https", "CONNECT")])
def test_fetch_forces_no_proxy_target_through_job_proxy_and_records_failure(
    monkeypatch, scheme, method
):
    module = _import_capture_module(monkeypatch)
    target_host = "approved.example.test"
    target = f"{scheme}://{target_host}/PorSitRFC21.xls"
    events: list[dict] = []

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            host = urlsplit(self.path).hostname
            events.append(
                {
                    "host": host,
                    "method": "GET",
                    "decision": "block",
                    "reason": "dns_failed",
                }
            )
            self.send_response(502)
            self.end_headers()

        def do_CONNECT(self) -> None:
            events.append(
                {
                    "host": self.path.split(":", 1)[0],
                    "method": "CONNECT",
                    "decision": "block",
                    "reason": "dns_failed",
                }
            )
            self.send_response(502)
            self.end_headers()

        def log_message(self, _format: str, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proxy_url = f"http://127.0.0.1:{server.server_port}"

    monkeypatch.setenv("CAPTURE_URL", target)
    monkeypatch.setenv("PROXY_URL", proxy_url)
    monkeypatch.setenv("NO_PROXY", target_host)
    monkeypatch.setenv("no_proxy", target_host)
    monkeypatch.setenv("CAPTURE_MAX_STEPS", "3")
    monkeypatch.setattr(module, "pod_identity", lambda: {"hostname": "synthetic-pod"})
    monkeypatch.setattr(module, "isolation_probes", lambda _proxy: {"network": {}})
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

    try:
        with pytest.raises((HTTPError, URLError), match="502"):
            module.fetch()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert events == [
        {
            "host": target_host,
            "method": method,
            "decision": "block",
            "reason": "dns_failed",
        }
    ]
    assert (
        module._NoRedirectHandler().redirect_request(
            None, None, 302, "found", {}, "http://unapproved.example.test/elsewhere"
        )
        is None
    )
