"""The engine's native cell substrate over its loopback JSON API (ontofill `serve_cells`, CONTRACT §13a).

`POST /cells` {backend, allowed_domains, limits, placement} → {cell_id, cdp_url, brain_url, live_view_port, ...};
`GET /cells/{id}`; `DELETE /cells/{id}` → {cell_id, state, job_record}; `POST /cells/{id}/steps` (count one browser
action; the substrate stops the cell at max_steps); `POST /cells/{id}/task-result` {result, ok}. Bearer token on every
route; the API binds to control-plane loopback only. Cells are gVisor Chromium hands behind an allowlist egress
proxy, reached over a CDP WebSocket on control-plane loopback; teardown writes the six-checkpoint job record.
"""

from __future__ import annotations

import os
from urllib.parse import quote

import httpx

from controller.cells import CellError

URL_ENV = "BA_CELLS_URL"  # default http://127.0.0.1:8766
TOKEN_ENV = "BA_CELLS_TOKEN"  # the cell API's bearer token (control plane only)
# The substrate's documented defaults (ontofill SandboxLimits); a session's own limits override them.
SUBSTRATE_DEFAULTS = {"memory_mb": 1024, "cpus": 1, "pids": 256, "timeout_s": 90, "max_steps": 32}
SUBSTRATE_LIMITS = tuple(SUBSTRATE_DEFAULTS)


class OntofillHttpProvider:
    name = "ontofill-http"

    def __init__(self, base_url: str | None = None, token: str | None = None, http: httpx.Client | None = None,
                 timeout_s: float | None = None):
        # A first create on a fresh sandbox host builds the hands/egress images there: allow minutes, not seconds.
        timeout_s = timeout_s or float(os.environ.get("BA_CELLS_TIMEOUT_S", "600"))
        self.base_url = (base_url or os.environ.get(URL_ENV) or "http://127.0.0.1:8766").rstrip("/")
        token = token or os.environ.get(TOKEN_ENV)
        if not token:
            raise CellError(f"{TOKEN_ENV} is not set (the engine's cell API requires a bearer token)")
        self._auth = {"Authorization": f"Bearer {token}"}
        self.http = http or httpx.Client(timeout=timeout_s)

    def __repr__(self) -> str:  # never show the token
        return f"OntofillHttpProvider({self.base_url!r})"

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            r = self.http.request(method, self.base_url + path, headers=self._auth, json=body)
        except httpx.HTTPError as exc:
            raise CellError(f"cell API unreachable: {type(exc).__name__}") from exc
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400:
            raise CellError(f"cell API {method} {path.split('/')[1]} → {r.status_code}: {data.get('error', '')[:200]}")
        return data

    @staticmethod
    def _id(cell_id: str) -> str:
        return quote(cell_id, safe=":")

    def create(self, backend: str, allowed_domains: list[str], limits: dict | None = None,
               placement: str = "sandbox_vm") -> dict:
        given = {k: limits[k] for k in SUBSTRATE_LIMITS if limits and limits.get(k) is not None}
        body = {"backend": backend, "allowed_domains": list(allowed_domains), "placement": placement,
                "limits": SUBSTRATE_DEFAULTS | given}  # the substrate wants the complete mapping
        cell = self._call("POST", "/cells", body)
        cell.setdefault("isolation", {"provider": self.name, "runtime": cell.get("runtime"),
                                      "egress": "allowlist proxy"})
        return cell

    def destroy(self, cell_id: str) -> dict:
        return self._call("DELETE", f"/cells/{self._id(cell_id)}")

    def status(self, cell_id: str) -> dict:
        return self._call("GET", f"/cells/{self._id(cell_id)}")

    def record_step(self, cell_id: str) -> int:
        return int(self._call("POST", f"/cells/{self._id(cell_id)}/steps", {}).get("steps") or 0)

    def report_task_result(self, cell_id: str, result: dict, ok: bool) -> None:
        self._call("POST", f"/cells/{self._id(cell_id)}/task-result", {"result": result, "ok": bool(ok)})
