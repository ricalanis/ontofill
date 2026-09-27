"""Screen a captured page through the inference gateway (CONTRACT §12a, R9/R9b).

The engine reaches Vultr only through the gateway; the gateway screens every
`<page_content>` span for tagged service principals and answers with `X-BA-Gate`.
This module reuses the engine's own marker (`ontofill.inference.page_content`).
"""

from __future__ import annotations

import os

import httpx
from ontofill.inference.page_content import screened_page_content


class Gateway:
    """One screening call against the configured inference gateway."""

    def __init__(self, *, client: httpx.Client | None = None, timeout: float = 60) -> None:
        self.base = os.environ.get("VULTR_INFERENCE_BASE_URL", "").rstrip("/")
        self.token = os.environ.get("ONTOFILL_GATEWAY_TOKEN") or os.environ.get(
            "VULTR_INFERENCE_API_KEY", ""
        )
        if not self.base or not self.token:
            raise RuntimeError("set VULTR_INFERENCE_BASE_URL and ONTOFILL_GATEWAY_TOKEN")
        self.client = client or httpx.Client(timeout=timeout)
        self._model: str | None = None

    @property
    def model(self) -> str:
        if self._model is None:
            configured = os.environ.get("VULTR_INFERENCE_MODEL", "")
            response = self.client.get(
                f"{self.base}/models", headers={"Authorization": f"Bearer {self.token}"}
            )
            response.raise_for_status()
            available = [item["id"] for item in response.json().get("data", []) if item.get("id")]
            if not available:
                raise RuntimeError("gateway returned no models")
            self._model = configured if configured in available else available[0]
        return self._model

    def screen_page(self, html: str, *, step_id: str) -> dict:
        """Send the captured page as one `<page_content>` span; return the §12a screen object.

        Fails closed: a missing or `clean` gate is an error here (the hostile fixture must flag).
        """
        headers = {"Authorization": f"Bearer {self.token}", "X-BA-Step-Id": step_id}
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": screened_page_content(html)}],
            "max_completion_tokens": 16,
            "temperature": 0,
        }
        response = self.client.post(f"{self.base}/chat/completions", headers=headers, json=body)
        response.raise_for_status()
        usage = response.json().get("usage") or {}
        gate = response.headers.get("X-BA-Gate", "").lower()
        if gate == "flagged":
            screen = {
                "flagged": True,
                "jev_choice": None,
                "jev_confidence": None,
                "safety_verdict": "unavailable",
                "reason": "gateway X-BA-Gate: flagged (quarantined in the prompt)",
                "by": "gateway",
            }
        elif gate == "clean":
            raise RuntimeError("the gateway did not flag the hostile fixture")
        else:
            raise RuntimeError("the gateway returned no X-BA-Gate verdict")
        return {"screen": screen, "usage": usage, "model": self.model, "gate": gate}
