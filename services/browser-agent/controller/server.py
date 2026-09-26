"""MCP façade (CONTRACT §13): session.open / session.act / session.observe / session.close.

The broker mints a per-session gateway token (budget + TTL) on open and revokes it on close; the session's loop
and backend only ever hold that token. Tool functions are plain callables (the engine's MCP client reaches them
over stdio or streamable HTTP; tests call them directly).
"""

from __future__ import annotations

import argparse
import os
import threading
import uuid
from pathlib import Path

import anyio

from controller.backend import CALL_ERRORS
from controller.captures import LocalCaptureStore
from controller.cells import CellError, CellPool, pool_from_env
from controller.gateway import ScreenedGatewayClient
from controller.loop import Limits, Session
from shared import config
from shared.gateway_client import GatewayAdmin
from shared.steps import StepLog

STEPS_DIR_ENV = "BA_STEPS_DIR"  # where each session's trace-step JSONL goes
CAPTURES_DIR_ENV = "BA_CAPTURES_DIR"  # local bronze-layout capture root
CASE_DIR_ENV = "BA_CASE_DIR"  # the case package: action approvals go to <case>/05-actions/
CDP_URL_ENV = "BA_CDP_URL"  # a browser pod's CDP endpoint (until the cell substrate hands one out per session)


def _default_backend(cdp_url: str | None):
    from backends.native.backend import NativeBackend

    return NativeBackend(cdp_url=cdp_url)


class Broker:
    """Holds live sessions. Factories are injectable so tests run without a gateway or a browser."""

    def __init__(self, *, admin=None, gateway_factory=None, backend_factory=None, steps_dir: Path | None = None,
                 captures_dir: Path | None = None, case_dir: Path | None = None, pool: CellPool | None | bool = None):
        gateway_url = config.env(config.GATEWAY_URL_ENV)
        admin_token = os.environ.get(config.GATEWAY_ADMIN_TOKEN_ENV)
        if admin is None and admin_token:
            admin = GatewayAdmin(gateway_url, admin_token)
        self.admin = admin
        self.gateway_factory = gateway_factory or (lambda token: ScreenedGatewayClient(gateway_url, token))
        self.backend_factory = backend_factory or _default_backend
        self.steps_dir = Path(steps_dir or os.environ.get(STEPS_DIR_ENV) or "runs/browser-agent/steps")
        self.captures_dir = Path(captures_dir or os.environ.get(CAPTURES_DIR_ENV) or "runs/browser-agent/captures")
        case = case_dir or os.environ.get(CASE_DIR_ENV)
        self.case_dir = Path(case) if case else None
        # Cells (§13a): a pool leases one cell per session; None = the backend launches/connects a browser itself.
        self.pool = pool_from_env() if pool is None else (pool or None)
        self.cells: dict[str, object] = {}  # session_id -> leased Cell
        self.sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def _get(self, session_id: str) -> Session:
        with self._lock:
            session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(f"unknown session {session_id!r}")
        return session

    def open(self, tdd: dict, allowed_domains: list[str], limits: dict | None = None) -> dict:
        if not allowed_domains:
            raise ValueError("allowed_domains is required (the TDD's allowlist)")
        tdd = dict(tdd or {})
        lim = Limits.from_dict(limits)
        session_id = f"bas-{uuid.uuid4().hex[:16]}"
        run_id = str(tdd.get("run_id") or "adhoc")
        if self.admin is None:
            raise RuntimeError(f"no gateway admin: set {config.GATEWAY_ADMIN_TOKEN_ENV} and {config.GATEWAY_URL_ENV}")
        token = self.admin.open_session(session_id, ttl_s=int(lim.ttl_s), budget_usd=float(lim.budget_usd),
                                        run_id=run_id)
        steps = StepLog(self.steps_dir / f"{session_id}.jsonl", run_id=run_id, session_id=session_id,
                        source_id=tdd.get("source_id"), objective_id=tdd.get("objective_id"),
                        tdd_path=tdd.get("tdd_path"))
        case_dir = Path(tdd["case_dir"]) if tdd.get("case_dir") else self.case_dir
        cdp_url = tdd.get("cdp_url") or os.environ.get(CDP_URL_ENV)
        cell = None
        if self.pool is not None:  # no fallback to a host browser when cells are configured
            try:
                cell = self.pool.lease("native", allowed_domains, limits or {})
            except (CellError, ValueError) as exc:
                self.admin.revoke(session_id)
                raise RuntimeError(f"could not lease a cell for {session_id}: {exc}") from exc
            cdp_url = cell.cdp_url
        cell_info = None
        if cell is not None:
            cell_info = {"cell_id": cell.cell_id, "isolation": cell.isolation, "placement": cell.placement,
                         **{k: v for k, v in self.pool.timings(cell.cell_id).items() if k.endswith("_ms") or k == "warm"}}
        session = Session(session_id=session_id, backend=self.backend_factory(cdp_url),
                          gateway=self.gateway_factory(token), steps=steps,
                          captures=LocalCaptureStore(self.captures_dir), allowed_domains=allowed_domains,
                          case_dir=case_dir, job_id=str(tdd.get("job_id") or f"job:{session_id}"), limits=lim,
                          source_id=tdd.get("source_id"),
                          artifact_paths=[tdd["tdd_path"]] if tdd.get("tdd_path") else None, admin=self.admin,
                          vision_grounding=bool(tdd.get("vision_grounding")), start_url=tdd.get("start_url"),
                          cdp_url=cdp_url, cell=cell_info)
        try:
            opened = session.open()
        except Exception:
            session.close()
            self.admin.revoke(session_id)
            if cell is not None:
                self.pool.release(cell)
            raise
        with self._lock:
            self.sessions[session_id] = session
            if cell is not None:
                self.cells[session_id] = cell
        return {"session_id": session_id, "live_view_url": None, "url": opened.get("url"),
                "steps_path": str(steps.path),
                **({"cell_id": cell_info["cell_id"], "isolation": cell_info["isolation"]} if cell_info else {})}

    def act(self, session_id: str, goal: str | None = None, action: dict | None = None) -> dict:
        if bool(goal) == bool(action):
            raise ValueError("pass exactly one of goal or action")
        session = self._get(session_id)
        return session.run_goal(goal) if goal else session.run_action(action, None)

    def observe(self, session_id: str) -> dict:
        return self._get(session_id).observe()

    def close(self, session_id: str) -> dict:
        with self._lock:
            session = self.sessions.pop(session_id, None)
            cell = self.cells.pop(session_id, None)
        if session is None:
            return {"closed": False, "error": f"unknown session {session_id!r}"}
        cell_result = None
        try:
            result = session.close()
        finally:
            try:
                self.admin.revoke(session_id)
                revoked = True
            except CALL_ERRORS:
                revoked = False
            if cell is not None:  # recycle = destroy + recreate; the cell is never reused
                cell_result = self.pool.release(cell)
        if cell_result is not None:
            result = result | {"cell": cell_result}
            result["metrics"] = dict(result.get("metrics") or {}) | {
                "cell": {k: v for k, v in self.pool.timings(cell_result["cell_id"]).items()
                         if k.endswith("_ms") or k == "warm"}}
        return result | {"token_revoked": revoked}

    def close_all(self) -> None:
        for session_id in list(self.sessions):
            self.close(session_id)
        if self.pool is not None:
            self.pool.shutdown()


_broker: Broker | None = None


def broker() -> Broker:
    global _broker
    if _broker is None:
        _broker = Broker()
    return _broker


def set_broker(b: Broker | None) -> None:
    global _broker
    _broker = b


# --- the four tools (plain functions; the MCP server wraps them) ----------------------------------------------
def session_open(tdd: dict, allowed_domains: list[str], limits: dict | None = None) -> dict:
    """Open a browser session for one (source, objective) TDD. Returns {session_id, live_view_url}."""
    return broker().open(tdd, allowed_domains, limits)


def session_act(session_id: str, goal: str | None = None, action: dict | None = None) -> dict:
    """Run the step loop toward a goal, or one explicit action {tool, args} through the same guard and checks."""
    return broker().act(session_id, goal, action)


def session_observe(session_id: str) -> dict:
    """The current page (summary, visible text excerpt, elements), extracted values and session metrics."""
    return broker().observe(session_id)


def session_close(session_id: str) -> dict:
    """Close the browser, revoke the session's gateway token, return final metrics."""
    return broker().close(session_id)


def build_server():
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("ontofill-browser-agent", instructions=(
        "Read-only browser sessions for Ontofill. session.open with the TDD and its allowed domains, then "
        "session.act with a goal or one action, session.observe, and session.close. HIGH-risk actions wait for a "
        "human approval under the case's 05-actions/."))

    async def _open(tdd: dict, allowed_domains: list[str], limits: dict | None = None) -> dict:
        return await anyio.to_thread.run_sync(session_open, tdd, allowed_domains, limits)

    async def _act(session_id: str, goal: str | None = None, action: dict | None = None) -> dict:
        return await anyio.to_thread.run_sync(session_act, session_id, goal, action)

    async def _observe(session_id: str) -> dict:
        return await anyio.to_thread.run_sync(session_observe, session_id)

    async def _close(session_id: str) -> dict:
        return await anyio.to_thread.run_sync(session_close, session_id)

    for name, fn, doc in (("session.open", _open, session_open.__doc__), ("session.act", _act, session_act.__doc__),
                          ("session.observe", _observe, session_observe.__doc__),
                          ("session.close", _close, session_close.__doc__)):
        server.add_tool(fn, name=name, description=doc)
    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ba-controller", description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8701)
    args = parser.parse_args(argv)
    server = build_server()
    try:
        if args.transport == "stdio":
            server.run("stdio")
        else:
            server.run("streamable-http", host=args.host, port=args.port)
    finally:
        broker().close_all()


if __name__ == "__main__":
    main()
