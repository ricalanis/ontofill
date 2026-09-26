"""Client for the inference gateway (brief 07 v2 slice 1). The controller and the native backend use only this;
they never see an upstream key. Wire format is pinned in services/browser-agent/README.md."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


class GatewayError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"gateway {status}: {detail}")
        self.status = status


@dataclass
class GatewayClient:
    base_url: str
    session_token: str
    timeout: float = 90.0

    def _headers(self, step_id: str | None) -> dict:
        h = {"Authorization": f"Bearer {self.session_token}"}
        if step_id:
            h["X-BA-Step-Id"] = step_id
        return h

    def _post(self, path: str, body: dict, step_id: str | None) -> dict:
        r = httpx.post(f"{self.base_url.rstrip('/')}{path}", json=body, headers=self._headers(step_id),
                       timeout=self.timeout)
        if r.status_code >= 400:
            raise GatewayError(r.status_code, r.text[:300])
        return r.json()

    def chat(self, model: str, messages: list[dict], step_id: str | None = None, **params) -> dict:
        """OpenAI-compatible chat completion via Vultr. Page content should be sent in a user message part
        wrapped as <page_content>…</page_content> so the gateway's injection gate can find it."""
        return self._post("/v1/chat/completions", {"model": model, "messages": messages, **params}, step_id)

    def jev(self, state, questions: dict, step_id: str | None = None) -> dict:
        """Jev /v1/systemone through the gateway. Returns Jev's response body (answers, model, usage)."""
        return self._post("/v1/jev/systemone", {"state": state, "questions": questions}, step_id)


@dataclass
class GatewayAdmin:
    """Controller-side: mint, inspect and revoke per-session tokens."""

    base_url: str
    admin_token: str

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        r = httpx.request(method, f"{self.base_url.rstrip('/')}{path}", json=body,
                          headers={"Authorization": f"Bearer {self.admin_token}"}, timeout=15)
        if r.status_code >= 400:
            raise GatewayError(r.status_code, r.text[:300])
        return r.json()

    def open_session(self, session_id: str, ttl_s: int, budget_usd: float, run_id: str | None = None) -> str:
        return self._req("POST", "/admin/sessions", {"session_id": session_id, "ttl_s": ttl_s,
                                                     "budget_usd": budget_usd, "run_id": run_id})["token"]

    def usage(self, session_id: str) -> dict:
        return self._req("GET", f"/admin/sessions/{session_id}")

    def revoke(self, session_id: str) -> dict:
        return self._req("POST", f"/admin/sessions/{session_id}/revoke")
