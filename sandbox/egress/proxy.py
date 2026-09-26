"""Small explicit HTTP proxy that only forwards to TDD-approved domains."""

from __future__ import annotations

import http.client
import json
import os
import select
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ALLOWED = frozenset(
    domain.strip().lower().rstrip(".")
    for domain in os.environ.get("ALLOWED_DOMAINS", "").split(",")
    if domain.strip()
)
HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def allowed(host: str | None) -> bool:
    """Match a full DNS name or one of its subdomains, never a string suffix."""
    if not host:
        return False
    normalized = host.lower().rstrip(".")
    return any(normalized == domain or normalized.endswith("." + domain) for domain in ALLOWED)


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        # The structured decision line below is the log consumed by the controller.
        pass

    def decision(self, outcome: str, host: str, method: str) -> None:
        print(json.dumps({"decision": outcome, "host": host, "method": method}), flush=True)

    def reject(self, host: str) -> None:
        self.decision("block", host, self.command)
        self.send_error(403, "domain is not allowed by the TDD")

    def do_CONNECT(self) -> None:
        authority = urlsplit("//" + self.path)
        host = authority.hostname or ""
        try:
            port = authority.port or 443
        except ValueError:
            self.send_error(400, "invalid port")
            return
        if not allowed(host) or port != 443:
            self.reject(host)
            return
        try:
            upstream = socket.create_connection((host, port), timeout=20)
        except OSError:
            self.send_error(502, "upstream connection failed")
            return
        self.decision("allow", host, self.command)
        self.send_response(200, "Connection Established")
        self.end_headers()
        self.close_connection = True
        try:
            sockets = [self.connection, upstream]
            while True:
                readable, _, _ = select.select(sockets, [], [], 30)
                if not readable:
                    break
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    destination = upstream if source is self.connection else self.connection
                    destination.sendall(data)
        except OSError:
            pass
        finally:
            upstream.close()

    def do_GET(self) -> None:
        self.forward()

    def do_HEAD(self) -> None:
        self.forward()

    def do_OPTIONS(self) -> None:
        self.forward()

    def do_POST(self) -> None:
        self.decision("block", urlsplit(self.path).hostname or "", self.command)
        self.send_error(405, "write methods are disabled")

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def forward(self) -> None:
        parsed = urlsplit(self.path)
        host = parsed.hostname or ""
        if parsed.scheme != "http" or not allowed(host):
            self.reject(host)
            return
        try:
            port = parsed.port or 80
        except ValueError:
            self.send_error(400, "invalid port")
            return
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        headers = {
            name: value for name, value in self.headers.items() if name.lower() not in HOP_HEADERS
        }
        headers["Host"] = parsed.netloc
        try:
            upstream = http.client.HTTPConnection(host, port, timeout=20)
            upstream.request(self.command, path, headers=headers)
            response = upstream.getresponse()
            body = response.read()
        except (OSError, http.client.HTTPException):
            self.send_error(502, "upstream request failed")
            return
        finally:
            if "upstream" in locals():
                upstream.close()
        self.decision("allow", host, self.command)
        self.send_response(response.status, response.reason)
        for name, value in response.getheaders():
            if name.lower() not in HOP_HEADERS | {"content-length"}:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8888), ProxyHandler).serve_forever()
