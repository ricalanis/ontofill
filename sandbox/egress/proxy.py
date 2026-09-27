"""Small explicit HTTP proxy that only forwards to TDD-approved domains."""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ALLOWED = frozenset(
    domain.strip().lower().rstrip(".")
    for domain in os.environ.get("ALLOWED_DOMAINS", "").split(",")
    if domain.strip()
)
EXACT_ALLOWED_HOSTS = frozenset(
    host.strip().lower().rstrip(".")
    for host in os.environ.get("EXACT_ALLOWED_HOSTS", "").split(",")
    if host.strip()
)
SIBLING_REGISTRABLE_DOMAINS = frozenset(
    domain.strip().lower().rstrip(".")
    for domain in os.environ.get("SIBLING_REGISTRABLE_DOMAINS", "").split(",")
    if domain.strip()
)
SAME_JURISDICTION_PUBLIC_SUFFIXES = frozenset(
    suffix.strip().lower().rstrip(".")
    for suffix in os.environ.get("SAME_JURISDICTION_PUBLIC_SUFFIXES", "").split(",")
    if suffix.strip()
)
PAGE_ASSET_TYPES = frozenset({"font", "image", "media", "script", "stylesheet"})
STATEFUL_RESOURCE_TYPES = frozenset({"eventsource", "websocket"})
DYNAMIC_ALLOWED_HOSTS: dict[str, str] = {}
_DYNAMIC_HOSTS_LOCK = threading.Lock()
_MAX_DYNAMIC_HOSTS = 16
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
MESH = ipaddress.ip_network("100.64.0.0/10")
DEPLOYMENT_VPC = ipaddress.ip_network("10.42.0.0/24")
MAX_RESPONSE_BYTES = 24 * 1024 * 1024


def _forbidden_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip in MESH
        or ip in DEPLOYMENT_VPC
    )


def _resolved_address(host: str, port: int) -> str | None:
    """Pin a DNS result and refuse hosts with any protected address."""
    return _resolve_address_detail(host, port)[0]


def _resolve_address_detail(host: str, port: int) -> tuple[str | None, str | None]:
    """Return a pinned public address or the reason DNS could not authorize it."""
    test_host_ip = None
    if host == "host.docker.internal" and host in ALLOWED:
        # One-shot local Docker tests explicitly add this host-gateway mapping.
        # A DNS answer alone never authorizes a private destination.
        for line in Path("/etc/hosts").read_text(encoding="utf-8").splitlines():
            parts = line.split("#", 1)[0].split()
            if len(parts) > 1 and host in parts[1:]:
                try:
                    candidate = ipaddress.ip_address(parts[0])
                except ValueError:
                    break
                if (
                    candidate.is_private
                    and not candidate.is_loopback
                    and not candidate.is_link_local
                    and candidate not in MESH
                    and candidate not in DEPLOYMENT_VPC
                ):
                    test_host_ip = parts[0]
                break
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return None, "dns_failed"
    if not addresses:
        return None, "dns_failed"
    try:
        rejected = any(
            _forbidden_ip(item[4][0]) and item[4][0] != test_host_ip for item in addresses
        )
    except (IndexError, ValueError):
        return None, "invalid_request"
    if rejected:
        return None, "address_rejected"
    return addresses[0][4][0], None


def _under_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _jurisdictional_public_suffix(suffix: str) -> bool:
    """Keep generic one-label TLDs out of the shared-asset exception."""
    labels = suffix.split(".")
    return len(labels) > 1 and len(labels[-1]) == 2 and labels[-1].isalpha()


def _valid_dns_host(host: str) -> bool:
    if not host or not host.isascii() or len(host) > 253 or "." not in host:
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        return False
    return all(
        1 <= len(label) <= 63
        and label[0].isalnum()
        and label[-1].isalnum()
        and all(character.isalnum() or character == "-" for character in label)
        for label in host.split(".")
    )


def _static_allowed(host: str) -> bool:
    if EXACT_ALLOWED_HOSTS:
        return host in EXACT_ALLOWED_HOSTS and host in ALLOWED
    if host in ALLOWED:
        return True
    return any(
        host.endswith("." + domain)
        and not any(_under_domain(domain, root) for root in SIBLING_REGISTRABLE_DOMAINS)
        for domain in ALLOWED
    )


def requested_host_policy(
    host: str | None,
    method: str,
    resource_type: str,
    page_host: str | None,
) -> tuple[bool, str | None]:
    """Decide if one browser request may add an exact host to this job."""
    if not host:
        return False, None
    normalized = host.lower().rstrip(".")
    page = (page_host or "").lower().rstrip(".")
    method = method.upper()
    kind = resource_type.casefold()
    if method not in {"GET", "HEAD"} or kind in STATEFUL_RESOURCE_TYPES:
        return False, None
    if _static_allowed(normalized):
        return True, None
    if EXACT_ALLOWED_HOSTS:
        return False, None
    sibling = any(_under_domain(normalized, root) for root in SIBLING_REGISTRABLE_DOMAINS)
    if sibling:
        if not any(_under_domain(page, root) for root in SIBLING_REGISTRABLE_DOMAINS):
            return False, None
    else:
        same_jurisdiction_asset = (
            kind in PAGE_ASSET_TYPES
            and any(_under_domain(page, root) for root in SIBLING_REGISTRABLE_DOMAINS)
            and any(
                _jurisdictional_public_suffix(suffix)
                and normalized != suffix
                and normalized.endswith("." + suffix)
                for suffix in SAME_JURISDICTION_PUBLIC_SUFFIXES
            )
        )
        if not same_jurisdiction_asset:
            return False, None
    with _DYNAMIC_HOSTS_LOCK:
        return True, DYNAMIC_ALLOWED_HOSTS.get(normalized, "domain_allowed")


def allowed(host: str | None) -> bool:
    """Match TDD hosts or exact hosts admitted for a page request in this job."""
    if not host:
        return False
    normalized = host.lower().rstrip(".")
    if _static_allowed(normalized):
        return True
    with _DYNAMIC_HOSTS_LOCK:
        return normalized in DYNAMIC_ALLOWED_HOSTS


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        # The structured decision line below is the log consumed by the controller.
        pass

    def decision(self, outcome: str, host: str, method: str, *, reason: str | None = None) -> None:
        normalized_host = host.strip().lower().rstrip(".")
        try:
            ipaddress.ip_address(normalized_host.strip("[]"))
            normalized_host = "ip-address"
        except ValueError:
            if (
                not normalized_host
                or len(normalized_host) > 253
                or any(marker in normalized_host for marker in ("/", "?", "#", "@"))
            ):
                normalized_host = "unknown-host"
        if reason is None:
            reason = "domain_allowed" if outcome == "allow" else "domain_not_allowed"
        if reason not in {
            "domain_allowed",
            "domain_not_allowed",
            "dns_failed",
            "address_rejected",
            "write_method_blocked",
            "invalid_request",
        }:
            reason = "invalid_request"
        clean_method = "".join(character for character in method.upper() if character.isalpha())[
            :16
        ]
        print(
            json.dumps(
                {
                    "decision": "allow" if outcome == "allow" else "block",
                    "host": normalized_host,
                    "method": clean_method or "OTHER",
                    "reason": reason,
                }
            ),
            flush=True,
        )

    def reject(self, host: str, reason: str = "domain_not_allowed") -> None:
        self.decision("block", host, self.command, reason=reason)
        status, message = {
            "domain_not_allowed": (403, "domain is not allowed by the TDD"),
            "dns_failed": (502, "DNS resolution failed"),
            "address_rejected": (403, "resolved address is not allowed"),
            "write_method_blocked": (405, "write methods are disabled"),
            "invalid_request": (400, "invalid proxy request"),
        }.get(reason, (403, "domain is not allowed by the TDD"))
        self.send_error(status, message)

    def _admit_page_host(self) -> None:
        """Admit one exact page-requested host after a bounded read-only check."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 4096:
            self.send_error(400, "invalid admission request")
            return
        try:
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise TypeError
            host = str(request.get("host") or "").lower().rstrip(".")
            method = str(request.get("method") or "").upper()
            resource_type = str(request.get("resource_type") or "").casefold()
            page_host = str(request.get("page_host") or "").lower().rstrip(".")
        except (TypeError, ValueError, json.JSONDecodeError):
            self.send_error(400, "invalid admission request")
            return
        if not _valid_dns_host(host) or (page_host and not _valid_dns_host(page_host)):
            self.decision("block", host, method, reason="invalid_request")
            self.send_error(400, "invalid admission host")
            return
        admitted, reason = requested_host_policy(host, method, resource_type, page_host)
        if not admitted:
            denial_reason = (
                "write_method_blocked"
                if method not in {"GET", "HEAD"} or resource_type in STATEFUL_RESOURCE_TYPES
                else "domain_not_allowed"
            )
            self.decision("block", host, method, reason=denial_reason)
            self.send_error(403, "host is outside the page egress policy")
            return
        if reason is None:
            self.send_response(204)
            self.end_headers()
            return
        with _DYNAMIC_HOSTS_LOCK:
            is_new = host not in DYNAMIC_ALLOWED_HOSTS
            if is_new and len(DYNAMIC_ALLOWED_HOSTS) >= _MAX_DYNAMIC_HOSTS:
                self.decision("block", host, method, reason="domain_not_allowed")
                self.send_error(403, "per-job host admission limit reached")
                return
            DYNAMIC_ALLOWED_HOSTS.setdefault(host, reason)
        if is_new:
            self.decision("allow", host, method, reason=reason)
        self.send_response(204)
        self.end_headers()

    def do_CONNECT(self) -> None:
        authority = urlsplit("//" + self.path)
        host = authority.hostname or ""
        try:
            port = authority.port or 443
        except ValueError:
            self.send_error(400, "invalid port")
            return
        if authority.username or authority.password or port != 443:
            self.reject(host, "invalid_request")
            return
        if not allowed(host):
            self.reject(host, "domain_not_allowed")
            return
        address, resolve_reason = _resolve_address_detail(host, port)
        if address is None:
            self.reject(host, resolve_reason or "invalid_request")
            return
        try:
            upstream = socket.create_connection((address, port), timeout=20)
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
        self.decision(
            "block",
            urlsplit(self.path).hostname or "",
            self.command,
            reason="write_method_blocked",
        )
        self.send_error(405, "only GET and HEAD requests are enabled")

    def do_POST(self) -> None:
        if self.path == "/_ontofill/admit":
            self._admit_page_host()
            return
        self.decision(
            "block",
            urlsplit(self.path).hostname or "",
            self.command,
            reason="write_method_blocked",
        )
        self.send_error(405, "write methods are disabled")

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def forward(self) -> None:
        parsed = urlsplit(self.path)
        host = parsed.hostname or ""
        if parsed.scheme != "http" or parsed.username or parsed.password:
            self.reject(host, "invalid_request")
            return
        if not allowed(host):
            self.reject(host, "domain_not_allowed")
            return
        try:
            port = parsed.port or 80
        except ValueError:
            self.send_error(400, "invalid port")
            return
        address, resolve_reason = _resolve_address_detail(host, port)
        if address is None:
            self.reject(host, resolve_reason or "invalid_request")
            return
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        headers = {
            name: value for name, value in self.headers.items() if name.lower() not in HOP_HEADERS
        }
        headers["Host"] = parsed.netloc
        try:
            upstream = http.client.HTTPConnection(address, port, timeout=20)
            upstream.request(self.command, path, headers=headers)
            response = upstream.getresponse()
            content_length = response.getheader("Content-Length")
            if self.command != "HEAD" and content_length:
                try:
                    if int(content_length) > MAX_RESPONSE_BYTES:
                        self.send_error(413, "upstream response exceeds the capture limit")
                        return
                except ValueError:
                    pass
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                self.send_error(413, "upstream response exceeds the capture limit")
                return
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
