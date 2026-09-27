"""Upstream clients (Vultr Serverless Inference, Jev). The only place real keys are used, as request headers."""

from __future__ import annotations

import time

import httpx


class UpstreamError(Exception):
    def __init__(self, detail: str, status: int | None = None):
        super().__init__(detail)
        self.detail = detail
        self.status = status


class Upstreams:
    def __init__(
        self,
        vultr_base: str,
        vultr_key: str | None,
        jev_base: str,
        jev_key: str | None,
        vultr_timeout: float = 90.0,
        jev_timeout: float = 20.0,
    ):
        self.vultr_base = vultr_base.rstrip("/")
        self.jev_base = jev_base.rstrip("/")
        self._vultr_key = vultr_key
        self._jev_key = jev_key
        self.vultr_timeout = vultr_timeout
        self.jev_timeout = jev_timeout

    def __repr__(self) -> str:  # never show keys
        return f"Upstreams(vultr={self.vultr_base}, jev={self.jev_base})"

    def _vultr_headers(self) -> dict:
        if not self._vultr_key:
            raise UpstreamError("Vultr inference key not configured")
        return {"Authorization": f"Bearer {self._vultr_key}", "Content-Type": "application/json"}

    def chat(self, body: dict) -> tuple[dict, float]:
        t0 = time.monotonic()
        try:
            r = httpx.post(
                f"{self.vultr_base}/chat/completions",
                json=body,
                headers=self._vultr_headers(),
                timeout=self.vultr_timeout,
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"vultr unreachable: {type(exc).__name__}") from None
        ms = (time.monotonic() - t0) * 1000
        if r.status_code >= 400:
            raise UpstreamError(f"vultr {r.status_code}: {r.text[:200]}", r.status_code)
        return r.json(), ms

    def chat_stream(self, body: dict):
        """Context-managed streaming response (caller iterates bytes)."""
        client = httpx.Client(timeout=self.vultr_timeout)
        req = client.build_request(
            "POST", f"{self.vultr_base}/chat/completions", json=body, headers=self._vultr_headers()
        )
        try:
            resp = client.send(req, stream=True)
        except httpx.HTTPError as exc:
            client.close()
            raise UpstreamError(f"vultr unreachable: {type(exc).__name__}") from None
        if resp.status_code >= 400:
            text = resp.read().decode(errors="replace")[:200]
            resp.close()
            client.close()
            raise UpstreamError(f"vultr {resp.status_code}: {text}", resp.status_code)
        return client, resp

    def models(self) -> dict:
        try:
            r = httpx.get(f"{self.vultr_base}/models", headers=self._vultr_headers(), timeout=30)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"vultr unreachable: {type(exc).__name__}") from None
        if r.status_code >= 400:
            raise UpstreamError(f"vultr {r.status_code}", r.status_code)
        return r.json()

    def jev(self, body: dict) -> tuple[dict, float, str | None]:
        if not self._jev_key:
            raise UpstreamError("Jev key not configured")
        t0 = time.monotonic()
        try:
            r = httpx.post(
                f"{self.jev_base}/v1/systemone",
                json=body,
                headers={"Authorization": f"Bearer {self._jev_key}", "Content-Type": "application/json"},
                timeout=self.jev_timeout,
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"jev unreachable: {type(exc).__name__}") from None
        ms = (time.monotonic() - t0) * 1000
        if r.status_code >= 400:
            raise UpstreamError(f"jev {r.status_code}: {r.text[:200]}", r.status_code)
        return r.json(), ms, r.headers.get("x-typesafe-request-id")
