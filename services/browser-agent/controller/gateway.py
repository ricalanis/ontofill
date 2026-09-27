"""Controller-side gateway client that keeps the gateway's injection verdict (response header X-BA-Gate).

shared.gateway_client.GatewayClient returns only the JSON body; this subclass records the header of the last
call in `last_gate` ("clean" | "flagged" | None) so the loop can log it and raise scrutiny.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from shared.gateway_client import GatewayClient, GatewayError

GATE_HEADER = "X-BA-Gate"
GATE_DETAIL_HEADER = "X-BA-Gate-Detail"


def parse_gate_detail(raw: str | None) -> dict | None:
    """The gateway's X-BA-Gate-Detail JSON, keeping only well-typed fields."""
    try:
        d = json.loads(raw) if raw else None
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    conf = d.get("jev_confidence")
    return {
        "jev_choice": d["jev_choice"] if isinstance(d.get("jev_choice"), str) else None,
        "jev_confidence": float(conf) if isinstance(conf, (int, float)) and 0 <= conf <= 1 else None,
        "safety_verdict": d["safety_verdict"]
        if d.get("safety_verdict") in ("safe", "unsafe")
        else "unavailable",
        "safety_model": d["safety_model"][:80] if isinstance(d.get("safety_model"), str) else None,
    }


@dataclass
class ScreenedGatewayClient(GatewayClient):
    last_gate: str | None = None
    last_gate_detail: dict | None = None  # per-screener verdicts of a flagged call (no page text)

    def _post(self, path: str, body: dict, step_id: str | None) -> dict:
        self.last_gate = None
        self.last_gate_detail = None
        r = httpx.post(
            f"{self.base_url.rstrip('/')}{path}",
            json=body,
            headers=self._headers(step_id),
            timeout=self.timeout,
        )
        gate = r.headers.get(GATE_HEADER)
        self.last_gate = gate.strip().lower() if gate else None
        self.last_gate_detail = parse_gate_detail(r.headers.get(GATE_DETAIL_HEADER))
        if r.status_code >= 400:
            raise GatewayError(r.status_code, r.text[:300])
        return r.json()
