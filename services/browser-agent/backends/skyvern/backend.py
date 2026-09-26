"""Skyvern backend: goal-level S2 fallback on a leased skyvern cell, mapped to §12 trace steps.

The backend takes a Cell (brain_url + the Skyvern API key the brain minted) from the controller's CellPool; it never
starts or destroys containers. Skyvern plans, acts and checks its goal inside one task, so it cannot run through our
per-action guard. It is therefore used only for read-only goals, and the backend enforces that around the task:
- before: the start URL must be inside the session's allowed domains (code floor);
- the goal text always carries the read-only instruction;
- after: every Skyvern action is audited; an action type outside the read-only set is a `hard_stop` step
  (flagged for a human), never silently accepted.
Egress from the hands is the substrate's job (per-cell allowlist); the gateway screens every prompt.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from shared.steps import StepLog

log = logging.getLogger(__name__)

READ_ONLY_SUFFIX = (" This is a read-only task: do not submit forms, do not log in, do not upload or download files, "
                    "do not solve captchas. Stop if the page needs any of those.")
# Skyvern action types that fit the read-only archetype. Anything else is a hard stop for a human to review.
READ_ONLY_ACTIONS = {"click", "scroll", "wait", "null_action", "complete", "extract", "terminate", "goto_url",
                     "keypress", "hover", "input_text", "select_option"}
TERMINAL = {"completed", "failed", "terminated", "timed_out", "canceled"}


class Cell(Protocol):
    cell_id: str
    brain_url: str | None
    api_key: str

    def fetch_artifact(self, file_url: str) -> bytes: ...


def host_allowed(url: str, allowed_domains: list[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in (x.lower().lstrip(".") for x in allowed_domains))


class SkyvernClient:
    """Skyvern v1 task API (v1.0.54 field names). `data_extraction_schema` is silently ignored by Skyvern; the
    schema must go in `extracted_information_schema` together with a `data_extraction_goal`."""

    def __init__(self, brain_url: str, api_key: str, http: httpx.Client | None = None, poll_s: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.base, self._key = brain_url.rstrip("/"), api_key
        self.http = http or httpx.Client(timeout=30)
        self.poll_s, self.sleep = poll_s, sleep

    def __repr__(self) -> str:
        return f"SkyvernClient({self.base!r})"

    def _h(self) -> dict:
        return {"x-api-key": self._key}

    def run_task(self, url: str, goal: str, extraction_goal: str | None = None, schema: dict | None = None,
                 max_steps: int = 4, deadline_s: float = 300) -> dict:
        body = {"url": url, "navigation_goal": goal, "max_steps_per_run": max_steps, "proxy_location": "NONE"}
        if extraction_goal or schema:
            body["data_extraction_goal"] = extraction_goal or "Extract the requested fields."
        if schema:
            body["extracted_information_schema"] = schema
        t0 = time.monotonic()
        r = self.http.post(f"{self.base}/tasks", json=body, headers=self._h())
        if r.status_code >= 400:
            return {"status": "rejected", "http_status": r.status_code, "failure_reason": r.text[:300],
                    "actions": [], "screenshots": [], "elapsed_s": 0.0}
        task_id = r.json()["task_id"]
        task: dict = {}
        while time.monotonic() - t0 < deadline_s:
            task = self.http.get(f"{self.base}/tasks/{task_id}", headers=self._h()).json()
            if task.get("status") in TERMINAL:
                break
            self.sleep(self.poll_s)
        else:
            self.http.post(f"{self.base}/tasks/{task_id}/cancel", headers=self._h())
            task["status"] = "timed_out"
        actions = self.http.get(f"{self.base}/tasks/{task_id}/actions", headers=self._h()).json()
        if isinstance(actions, dict):
            actions = actions.get("actions", [])
        return {"task_id": task_id, "status": task.get("status"), "extracted": task.get("extracted_information"),
                "failure_reason": task.get("failure_reason"), "step_count": task.get("step_count"),
                "actions": [{"action_type": a.get("action_type"), "element_id": a.get("element_id"),
                             "reasoning": (a.get("reasoning") or "")[:500], "status": a.get("status")}
                            for a in actions or []],
                "screenshots": [u for u in (task.get("action_screenshot_urls") or []) + [task.get("screenshot_url")]
                                if u],
                "elapsed_s": round(time.monotonic() - t0, 2)}


class SkyvernBackend:
    """open(session_id) → run_goal(goal, url, schema)* → close(). Runs on one leased cell per controller session;
    the pool owns the cell's lifecycle (recycle = destroy + recreate after the session)."""

    name = "skyvern"

    def __init__(self, cell: Cell, steps: StepLog, allowed_domains: list[str], gateway_session_id: str | None = None,
                 capture: Callable[[bytes], str] | None = None, usage: Callable[[str], dict] | None = None,
                 model: str = "qwen3.8-flash-next", client: SkyvernClient | None = None):
        self.cell, self.steps, self.allowed = cell, steps, allowed_domains
        self.gateway_session_id = gateway_session_id
        self.capture = capture  # bytes → content-addressed key (e.g. "sha256:<hex>"), stored in bronze
        self.usage = usage  # gateway admin usage(gateway_session_id) → {spent_usd, calls, flagged, ...}
        self.model = model
        self.client = client
        self.session_id: str | None = None

    def open(self, session_id: str) -> dict:
        if not self.cell.brain_url:
            raise RuntimeError("cell has no brain_url")
        self.client = self.client or SkyvernClient(self.cell.brain_url, self.cell.api_key)
        self.session_id = session_id
        return {"session_id": session_id, "backend": self.name, "cell_id": self.cell.cell_id,
                "gateway_session_id": self.gateway_session_id}

    def run_goal(self, goal: str, url: str, schema: dict | None = None, extraction_goal: str | None = None,
                 max_steps: int = 4, parent_step_id: str | None = None) -> dict:
        if not self.client:
            raise RuntimeError("backend not open")
        requested = {"tool": "skyvern.task", "goal": goal, "url": url, "max_steps": max_steps}
        gen = {"backend": "vultr", "model": self.model, "agent": "skyvern", "cell_id": self.cell.cell_id}
        if not host_allowed(url, self.allowed):
            step = self.steps.emit(mode="S2", observed={"url": url}, requested=requested,
                                   executed={"tool": "skyvern.task", "status": "not_run"},
                                   evaluated={"outcome": "blocked", "reason": "start URL outside allowed domains"},
                                   parent_step_id=parent_step_id, event="hard_stop", generated_by=gen)
            return {"status": "blocked", "ok": False, "steps": [step]}
        flagged_before = (self._usage() or {}).get("flagged") or 0
        res = self.client.run_task(url, goal + READ_ONLY_SUFFIX, extraction_goal, schema, max_steps)
        after = self._usage() or {}
        quarantined = None
        if (after.get("flagged") or 0) > flagged_before:  # §12a: the gateway quarantined page text in Skyvern's prompt
            screen = {k: v for k, v in (after.get("last_flag") or {"flagged": True, "by": "gateway",
                                                                   "safety_verdict": "unavailable"}).items() if k != "ts"}
            screen.setdefault("reason", None)
            quarantined = self.steps.emit(
                mode="S2", observed={"url": url}, requested={"tool": "skyvern.task", "goal": goal},
                executed={"tool": "gateway.screen", "flagged_calls": after["flagged"] - flagged_before},
                evaluated={"status": "quarantined_continue",
                           "note": "page text wrapped as untrusted data in Skyvern's prompts; captures kept"},
                parent_step_id=parent_step_id, event="quarantine", generated_by=gen, screen=screen)
        shots = []
        if self.capture:
            for u in res.get("screenshots", [])[-2:]:  # last action + final page are enough as evidence
                try:
                    shots.append(self.capture(self.cell.fetch_artifact(u)))
                except (RuntimeError, OSError) as exc:  # evidence capture must not kill the step
                    log.warning("skyvern artifact not captured: %s", exc)
        emitted = [quarantined] if quarantined else []
        prev = quarantined["step_id"] if quarantined else parent_step_id
        for i, a in enumerate(res.get("actions", [])):
            kind = a.get("action_type")
            stop = kind not in READ_ONLY_ACTIONS
            s = self.steps.emit(mode="S2", observed={"url": url},
                                requested={"tool": "skyvern.action", "action_type": kind, "index": i},
                                executed={"tool": "skyvern.task", "task_id": res.get("task_id"),
                                          "action_type": kind, "element_id": a.get("element_id"),
                                          "status": a.get("status")},
                                evaluated={"outcome": "flagged" if stop else "ok", "reasoning": a.get("reasoning"),
                                           **({"reason": f"action type {kind!r} is outside the read-only set"}
                                              if stop else {})},
                                parent_step_id=prev, event="hard_stop" if stop else None, generated_by=gen)
            emitted.append(s)
            prev = s["step_id"]
        status = res.get("status")
        ok = status == "completed" and not any(e["event"] == "hard_stop" for e in emitted)
        final = self.steps.emit(
            mode="S2", observed={"url": url}, requested=requested,
            executed={"tool": "skyvern.task", "task_id": res.get("task_id"), "status": status,
                      "step_count": res.get("step_count"), "elapsed_s": res.get("elapsed_s")},
            evaluated={"outcome": "pass" if ok else "fail", "status": status, "extracted": res.get("extracted"),
                       "failure_reason": res.get("failure_reason")},
            parent_step_id=prev, event=None if ok else "failure", generated_by=gen,
            screenshot_key=shots[-1] if shots else None)
        return {**res, "ok": ok, "screenshot_keys": shots, "steps": [*emitted, final]}

    def _usage(self) -> dict | None:
        if not (self.usage and self.gateway_session_id):
            return None
        try:
            return self.usage(self.gateway_session_id)
        except (httpx.HTTPError, RuntimeError):  # GatewayError is a RuntimeError
            return None

    def close(self) -> dict:
        """Report usage; the pool destroys and recreates the cell (and revokes the token) after this."""
        usage = None
        if self.usage and self.gateway_session_id:
            try:
                usage = self.usage(self.gateway_session_id)
            except (httpx.HTTPError, RuntimeError):  # GatewayError is a RuntimeError
                usage = None
        self.client = None
        return {"closed": True, "cell_id": self.cell.cell_id, "gateway_session_id": self.gateway_session_id,
                "usage": usage}
