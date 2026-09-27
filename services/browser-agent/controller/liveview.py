"""Read-only live view of a session's browser (architecture §8: per-cell live-view URLs that die with the cell).

One small HTTP server on control-plane loopback serves, per session:

- `GET /live/<session_id>?t=<view token>`: a minimal page showing the stream;
- `GET /live/<session_id>/stream?t=...`: MJPEG (`multipart/x-mixed-replace`);
- `GET /live/<session_id>/frame.jpg?t=...`: the latest frame.

Frames come from a second CDP client on the session's own browser (the cell's `cdp_url`, or the local dev browser's
debugging port): `Page.startScreencast` on the newest page, each frame acked, only the latest JPEG kept. The viewer
only ever receives images: no input is forwarded and no CDP endpoint is exposed. Without a CDP endpoint the view
falls back to the session's latest screenshot capture ("latest capture, not live"). Every route requires the
session's random view token; an unknown or closed session, or a wrong token, is a 404. Closing the session stops the
screencast, disconnects the viewer's CDP client and ends any open stream within about a second. A public URL comes
from `netbird expose` on the control plane (`BA_LIVEVIEW_PUBLIC_BASE`); the token never goes into trace steps.
"""

from __future__ import annotations

import base64
import contextlib
import hmac
import html
import logging
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger(__name__)

PORT_ENV = "BA_LIVEVIEW_PORT"  # default 8702; 0 disables the live view
PUBLIC_BASE_ENV = "BA_LIVEVIEW_PUBLIC_BASE"  # e.g. the `netbird expose` URL for this control plane
# Bind address: loopback by default. `netbird expose` dials the peer's NetBird IP, so a published live view binds that
# IP (still not a public interface).
HOST_ENV = "BA_LIVEVIEW_HOST"
DEFAULT_PORT = 8702
BOUNDARY = "ba-live-frame"


class FrameSource:
    """Latest frame for one session, from a CDP screencast (live) or from the session's captures (fallback)."""

    def __init__(self, session_id: str, cdp_url: str | None, poll_s: float = 0.1):
        self.session_id = session_id
        self.cdp_url = cdp_url
        self.mode = "live" if cdp_url else "capture"
        self.poll_s = poll_s
        self.frame: bytes | None = None
        self.content_type = "image/jpeg"
        self.seq = 0
        self.frames = 0
        self.error: str | None = None
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- producers ----------------------------------------------------------------------------------------------
    def put(self, data: bytes, content_type: str) -> None:
        with self._cond:
            self.frame, self.content_type = data, content_type
            self.seq += 1
            self.frames += 1
            self._cond.notify_all()

    def start(self) -> None:
        if self.mode == "live" and self._thread is None:
            self._thread = threading.Thread(target=self._screencast, name=f"ba-live-{self.session_id[:12]}",
                                            daemon=True)
            self._thread.start()

    def _screencast(self) -> None:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(self.cdp_url, timeout=15000)
                try:
                    self._follow(browser)
                finally:
                    with contextlib.suppress(PlaywrightError):
                        browser.close()  # a CDP connection: disconnects this viewer only, the browser keeps running
        except Exception as exc:  # noqa: BLE001 - the view is best effort; the session never depends on it
            if not self._stop.is_set():
                self.error = f"{type(exc).__name__}: {str(exc)[:160]}"
                log.info("live view for %s stopped: %s", self.session_id, self.error)

    def _follow(self, browser) -> None:
        """Screencast the newest page; switch when the session opens a newer one (popup, new tab, new context)."""
        page = cdp = None
        while not self._stop.is_set():
            pages = [p for c in browser.contexts for p in c.pages if not p.is_closed()]
            # A cell's browser starts with its own blank tab; the session's page is the newest one with content.
            loaded = [p for p in pages if p.url not in ("", "about:blank")]
            newest = (loaded or pages)[-1] if pages else None
            if newest is not None and newest is not page:
                if cdp is not None:
                    with contextlib.suppress(Exception):
                        cdp.send("Page.stopScreencast")
                        cdp.detach()
                page, cdp = newest, newest.context.new_cdp_session(newest)
                cdp.on("Page.screencastFrame", lambda ev, s=cdp: self._on_frame(s, ev))
                with contextlib.suppress(Exception):  # seed a frame at once; screencast only sends on change
                    shot = cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 60})
                    self.put(base64.b64decode(shot["data"]), "image/jpeg")
                cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 60, "maxWidth": 1280,
                                                  "everyNthFrame": 1})
            if page is not None:
                page.wait_for_timeout(self.poll_s * 1000)  # pumps CDP events on this thread
            else:
                self._stop.wait(self.poll_s)

    def _on_frame(self, cdp, ev: dict) -> None:
        with contextlib.suppress(Exception):
            cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        self.put(base64.b64decode(ev["data"]), "image/jpeg")

    # --- consumers ----------------------------------------------------------------------------------------------
    def latest(self) -> tuple[bytes | None, str]:
        with self._cond:
            return self.frame, self.content_type

    def wait_next(self, seq: int, timeout: float) -> tuple[int, bytes | None, str]:
        with self._cond:
            if self.seq == seq and not self._stop.is_set():
                self._cond.wait(timeout)
            return self.seq, self.frame, self.content_type

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def stop(self, join_s: float = 5.0) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(join_s)


class RecordingCaptures:
    """Wraps a CaptureStore: every screenshot the session stores also becomes the fallback view's latest frame."""

    def __init__(self, inner):
        self.inner = inner
        self._source: FrameSource | None = None
        self._last: tuple[bytes, str] | None = None  # screenshots taken before the view was registered

    def __getattr__(self, name):
        return getattr(self.inner, name)

    @property
    def source(self) -> FrameSource | None:
        return self._source

    @source.setter
    def source(self, source: FrameSource | None) -> None:
        self._source = source
        if source is not None and source.mode == "capture" and self._last is not None:
            source.put(*self._last)

    def put(self, data: bytes, *, content_type: str, **kw) -> str:
        key = self.inner.put(data, content_type=content_type, **kw)
        if content_type.startswith("image/"):
            self._last = (data, content_type)
            if self._source is not None and self._source.mode == "capture":
                self._source.put(data, content_type)
        return key


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Live view · {sid}</title>
<style>
  html,body{{margin:0;background:#16181a;color:#c9cdd1;font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace}}
  p{{margin:0;padding:8px 12px;border-bottom:1px solid #2a2d31}}
  img{{display:block;width:100%;height:auto}}
</style></head>
<body><p>{caption}</p><img src="{stream}" alt="Browser of session {sid}"></body></html>
"""


class LiveViewHub:
    """The loopback server and the per-session registry. Shared per port within a process."""

    _shared: ClassVar[dict[int, LiveViewHub]] = {}
    _shared_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, port: int = DEFAULT_PORT, host: str = "127.0.0.1", public_base: str | None = None):
        self.host = host
        self.requested_port = port
        self.public_base = (public_base or os.environ.get(PUBLIC_BASE_ENV) or "").rstrip("/") or None
        self.sessions: dict[str, tuple[str, FrameSource]] = {}  # session_id -> (view token, source)
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self.port: int | None = None

    @classmethod
    def from_env(cls) -> LiveViewHub | None:
        raw = os.environ.get(PORT_ENV, str(DEFAULT_PORT)).strip()
        port = int(raw) if raw else DEFAULT_PORT
        if port == 0:
            return None
        with cls._shared_lock:
            hub = cls._shared.get(port)
            if hub is None:
                hub = cls._shared[port] = cls(port, host=os.environ.get(HOST_ENV, "").strip() or "127.0.0.1")
            return hub

    # --- server -------------------------------------------------------------------------------------------------
    def start(self) -> bool:
        with self._lock:
            if self._server is not None:
                return True
            try:
                server = ThreadingHTTPServer((self.host, self.requested_port), _handler(self))
            except OSError as exc:  # port in use: the session runs without a live view
                log.warning("live view disabled: cannot bind %s:%s (%s)", self.host, self.requested_port, exc)
                return False
            server.daemon_threads = True
            self._server, self.port = server, server.server_address[1]
        threading.Thread(target=server.serve_forever, name="ba-liveview", daemon=True).start()
        return True

    def shutdown(self) -> None:
        for sid in list(self.sessions):
            self.unregister(sid)
        with self._lock:
            server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()

    # --- sessions -----------------------------------------------------------------------------------------------
    def register(self, session_id: str, cdp_url: str | None) -> tuple[str | None, FrameSource | None]:
        """(live_view_url, source), or (None, None) when the server cannot run."""
        if not self.start():
            return None, None
        token = secrets.token_urlsafe(16)
        source = FrameSource(session_id, cdp_url)
        with self._lock:
            self.sessions[session_id] = (token, source)
        source.start()
        base = self.public_base or f"http://{self.host}:{self.port}"
        return f"{base}/live/{session_id}?t={token}", source

    def unregister(self, session_id: str) -> None:
        with self._lock:
            entry = self.sessions.pop(session_id, None)
        if entry is not None:
            entry[1].stop()

    def lookup(self, session_id: str, token: str | None) -> FrameSource | None:
        with self._lock:
            entry = self.sessions.get(session_id)
        if entry is None or not token or not hmac.compare_digest(entry[0].encode(), token.encode()):
            return None
        return entry[1]


def _handler(hub: LiveViewHub) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args) -> None:  # URLs carry the view token: never log them
            pass

        def _headers(self, status: int, content_type: str, length: int | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")  # the token is in the URL
            self.send_header("X-Content-Type-Options", "nosniff")
            if length is not None:
                self.send_header("Content-Length", str(length))
            else:
                self.send_header("Connection", "close")
            self.end_headers()

        def _not_found(self) -> None:
            body = b"not found\n"
            self._headers(404, "text/plain; charset=utf-8", len(body))
            self.wfile.write(body)

        def do_GET(self) -> None:
            parts = urlsplit(self.path)
            segs = parts.path.strip("/").split("/")
            token = (parse_qs(parts.query).get("t") or [None])[0]
            if len(segs) not in (2, 3) or segs[0] != "live":
                return self._not_found()
            source = hub.lookup(segs[1], token)
            if source is None:
                return self._not_found()
            action = segs[2] if len(segs) == 3 else ""
            if action == "":
                return self._page(segs[1], token, source)
            if action == "frame.jpg":
                frame, ctype = source.latest()
                if frame is None:
                    return self._not_found()
                self._headers(200, ctype, len(frame))
                self.wfile.write(frame)
                return None
            if action == "stream":
                return self._stream(source)
            return self._not_found()

        def _page(self, sid: str, token: str, source: FrameSource) -> None:
            mode = "" if source.mode == "live" else " · latest capture, not live"
            caption = f"Live view · {html.escape(sid)} · read-only · ends when the session closes{mode}"
            body = PAGE.format(sid=html.escape(sid), caption=caption,
                               stream=html.escape(f"/live/{sid}/stream?t={token}")).encode()
            self._headers(200, "text/html; charset=utf-8", len(body))
            self.wfile.write(body)

        def _stream(self, source: FrameSource) -> None:
            self._headers(200, f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            seq = -1
            try:
                while not source.stopped:
                    seq_now, frame, ctype = source.wait_next(seq, timeout=1.0)
                    if source.stopped:
                        break
                    if frame is None or seq_now == seq:
                        continue
                    seq = seq_now
                    self.wfile.write(f"--{BOUNDARY}\r\nContent-Type: {ctype}\r\nContent-Length: {len(frame)}"
                                     f"\r\n\r\n".encode() + frame + b"\r\n")
                    self.wfile.flush()
                    time.sleep(0.03)  # at most ~30 frames/s to one viewer
            except (BrokenPipeError, ConnectionResetError):
                return
            with contextlib.suppress(OSError):
                self.wfile.write(f"--{BOUNDARY}--\r\n".encode())
            self.close_connection = True

    return Handler
