"""Controller-side gateway client that keeps the gateway's injection verdict (response header X-BA-Gate).

shared.gateway_client.GatewayClient returns only the JSON body; this subclass records the header of the last
call in `last_gate` ("clean" | "flagged" | None) so the loop can log it and raise scrutiny.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from shared.gateway_client import GatewayClient, GatewayError

GATE_HEADER = "X-BA-Gate"


@dataclass
class ScreenedGatewayClient(GatewayClient):
    last_gate: str | None = None

    def _post(self, path: str, body: dict, step_id: str | None) -> dict:
        self.last_gate = None
        r = httpx.post(
            f"{self.base_url.rstrip('/')}{path}",
            json=body,
            headers=self._headers(step_id),
            timeout=self.timeout,
        )
        gate = r.headers.get(GATE_HEADER)
        self.last_gate = gate.strip().lower() if gate else None
        if r.status_code >= 400:
            raise GatewayError(r.status_code, r.text[:300])
        return r.json()
