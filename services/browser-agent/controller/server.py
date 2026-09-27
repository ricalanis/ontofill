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

from controller.backend import CALL_ERRORS, ActResult
from controller.captures import LocalCaptureStore
from controller.cells import Cell, CellError, CellPool, pool_from_env
from controller.gateway import ScreenedGatewayClient
from controller.liveview import ExposedLiveView, LiveViewHub, RecordingCaptures, live_view_from_env
from controller.loop import Limits, Session
from shared import config
from shared.gateway_client import GatewayAdmin
from shared.steps import StepLog

STEPS_DIR_ENV = "BA_STEPS_DIR"  # where each session's trace-step JSONL goes
CAPTURES_DIR_ENV = "BA_CAPTURES_DIR"  # local bronze-layout capture root
CASE_DIR_ENV = "BA_CASE_DIR"  # the case package: action approvals go to <case>/05-actions/
CDP_URL_ENV = (
    "BA_CDP_URL"  # a browser pod's CDP endpoint (until the cell substrate hands one out per session)
)


def _default_backend(cdp_url: str | None, debug_port: bool = False):
    from backends.native.backend import NativeBackend

    return NativeBackend(cdp_url=cdp_url, debug_port=debug_port)


class Broker:
    """Holds live sessions. Factories are injectable so tests run without a gateway or a browser."""

    def __init__(
        self,
        *,
        admin=None,
        gateway_factory=None,
        backend_factory=None,
        steps_dir: Path | None = None,
        captures_dir: Path | None = None,
        case_dir: Path | None = None,
        pool: CellPool | None | bool = None,
        liveview: LiveViewHub | ExposedLiveView | None | bool = None,
    ):
        gateway_url = config.env(config.GATEWAY_URL_ENV)
        admin_token = os.environ.get(config.GATEWAY_ADMIN_TOKEN_ENV)
        if admin is None and admin_token:
            admin = GatewayAdmin(gateway_url, admin_token)
        self.admin = admin
        self.gateway_factory = gateway_factory or (lambda token: ScreenedGatewayClient(gateway_url, token))
        # Live view (read-only screencast per session); None = from env: BA_LIVEVIEW_EXPOSE=netbird publishes one
        # `netbird expose` per session, else the shared hub on BA_LIVEVIEW_PORT (default 8702, 0 = off).
        self.liveview = live_view_from_env() if liveview is None else (liveview or None)
        self.backend_factory = backend_factory or (
            lambda cdp_url: _default_backend(cdp_url, debug_port=self.liveview is not None and not cdp_url)
        )
        self.steps_dir = Path(steps_dir or os.environ.get(STEPS_DIR_ENV) or "runs/browser-agent/steps")
        self.captures_dir = Path(
            captures_dir or os.environ.get(CAPTURES_DIR_ENV) or "runs/browser-agent/captures"
        )
        case = case_dir or os.environ.get(CASE_DIR_ENV)
        self.case_dir = Path(case) if case else None
        # Cells (§13a): a pool leases one cell per session; None = the backend launches/connects a browser itself.
        self.pool = pool_from_env() if pool is None else (pool or None)
        self.cells: dict[str, object] = {}  # session_id -> leased Cell
        self.results: dict[str, list[dict]] = {}  # session_id -> act outcomes, reported to the cell at close
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
            raise RuntimeError(
                f"no gateway admin: set {config.GATEWAY_ADMIN_TOKEN_ENV} and {config.GATEWAY_URL_ENV}"
            )
        token = self.admin.open_session(
            session_id, ttl_s=int(lim.ttl_s), budget_usd=float(lim.budget_usd), run_id=run_id
        )
        steps = StepLog(
            self.steps_dir / f"{session_id}.jsonl",
            run_id=run_id,
            session_id=session_id,
            source_id=tdd.get("source_id"),
            objective_id=tdd.get("objective_id"),
            tdd_path=tdd.get("tdd_path"),
        )
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
            cell_info = {
                "cell_id": cell.cell_id,
                "isolation": cell.isolation,
                "placement": cell.placement,
                **{
                    k: v
                    for k, v in self.pool.timings(cell.cell_id).items()
                    if k.endswith("_ms") or k == "warm"
                },
            }
        backend = self.backend_factory(cdp_url)
        if cell is not None:
            backend = CellCountedBackend(backend, self.pool, cell)
        captures = RecordingCaptures(LocalCaptureStore(self.captures_dir))
        session = Session(
            session_id=session_id,
            backend=backend,
            gateway=self.gateway_factory(token),
            steps=steps,
            captures=captures,
            allowed_domains=allowed_domains,
            case_dir=case_dir,
            job_id=str(tdd.get("job_id") or f"job:{session_id}"),
            limits=lim,
            source_id=tdd.get("source_id"),
            artifact_paths=[tdd["tdd_path"]] if tdd.get("tdd_path") else None,
            admin=self.admin,
            vision_grounding=bool(tdd.get("vision_grounding")),
            start_url=tdd.get("start_url"),
            cdp_url=cdp_url,
            cell=cell_info,
        )
        try:
            opened = session.open()
        except Exception:
            session.close()
            self.admin.revoke(session_id)
            if cell is not None:
                self.pool.release(cell)
            raise
        live_view_url, live_view_error = None, None
        if (
            self.liveview is not None
        ):  # the URL (with its view token) goes to the caller only, never into steps
            live_view_url, source = self.liveview.register(
                session_id, cdp_url or getattr(backend, "cdp_endpoint", None)
            )
            captures.source = source
            live_view_error = (getattr(self.liveview, "errors", None) or {}).get(session_id)
        with self._lock:
            self.sessions[session_id] = session
            if cell is not None:
                self.cells[session_id] = cell
        return {
            "session_id": session_id,
            "live_view_url": live_view_url,
            "url": opened.get("url"),
            "steps_path": str(steps.path),
            **({"live_view": {"error": live_view_error}} if live_view_error and not live_view_url else {}),
            **({"cell_id": cell_info["cell_id"], "isolation": cell_info["isolation"]} if cell_info else {}),
        }

    def act(self, session_id: str, goal: str | None = None, action: dict | None = None) -> dict:
        if bool(goal) == bool(action):
            raise ValueError("pass exactly one of goal or action")
        session = self._get(session_id)
        result = session.run_goal(goal) if goal else session.run_action(action, None)
        with self._lock:
            self.results.setdefault(session_id, []).append(
                {"goal": goal, "status": result.get("status"), "url": result.get("url")}
            )
        return result

    def observe(self, session_id: str) -> dict:
        return self._get(session_id).observe()

    def close(self, session_id: str) -> dict:
        with self._lock:
            session = self.sessions.pop(session_id, None)
            cell = self.cells.pop(session_id, None)
        if session is None:
            return {"closed": False, "error": f"unknown session {session_id!r}"}
        if self.liveview is not None:  # the view dies with the session: screencast stopped, streams ended
            self.liveview.unregister(session_id)
        cell_result = None
        outcomes = self.results.pop(session_id, [])
        try:
            if cell is not None:  # the proof's task checkpoint gets the real result, never an inference
                ok = bool(outcomes) and all(o["status"] == "achieved" for o in outcomes if o["goal"])
                try:
                    self.pool.report_task_result(
                        cell,
                        {"outcomes": outcomes[-10:], "extracted_fields": sorted(session.extracted)[:50]},
                        ok,
                    )
                except CellError:
                    pass
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
                "cell": {
                    k: v
                    for k, v in self.pool.timings(cell_result["cell_id"]).items()
                    if k.endswith("_ms") or k == "warm"
                }
            }
        return result | {"token_revoked": revoked}

    def close_all(self) -> None:
        for session_id in list(self.sessions):
            self.close(session_id)
        if isinstance(self.liveview, ExposedLiveView):  # no expose child outlives the broker
            self.liveview.shutdown()
        if self.pool is not None:
            self.pool.shutdown()


class CellCountedBackend:
    """Counts each browser action with the cell substrate before it runs (the substrate enforces max_steps and
    stops the cell; a stopped cell refuses the action instead of running it on a dead browser)."""

    def __init__(self, inner, pool: CellPool, cell: Cell):
        self.inner, self.pool, self.cell = inner, pool, cell

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def act(self, action):
        try:
            self.pool.record_step(self.cell)
        except CellError as exc:
            return ActResult(False, None, f"stopped by the cell: {exc}")
        return self.inner.act(action)


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

    server = MCPServer(
        "ontofill-browser-agent",
        instructions=(
            "Read-only browser sessions for Ontofill. session.open with the TDD and its allowed domains, then "
            "session.act with a goal or one action, session.observe, and session.close. HIGH-risk actions wait for a "
            "human approval under the case's 05-actions/."
        ),
    )

    async def _open(tdd: dict, allowed_domains: list[str], limits: dict | None = None) -> dict:
        return await anyio.to_thread.run_sync(session_open, tdd, allowed_domains, limits)

    async def _act(session_id: str, goal: str | None = None, action: dict | None = None) -> dict:
        return await anyio.to_thread.run_sync(session_act, session_id, goal, action)

    async def _observe(session_id: str) -> dict:
        return await anyio.to_thread.run_sync(session_observe, session_id)

    async def _close(session_id: str) -> dict:
        return await anyio.to_thread.run_sync(session_close, session_id)

    for name, fn, doc in (
        ("session.open", _open, session_open.__doc__),
        ("session.act", _act, session_act.__doc__),
        ("session.observe", _observe, session_observe.__doc__),
        ("session.close", _close, session_close.__doc__),
    ):
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
