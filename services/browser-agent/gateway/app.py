"""Inference gateway: OpenAI-compatible proxy to Vultr + Jev proxy, per-session tokens, call log, prompt screening.

    uv run ba-gateway            # 127.0.0.1:8700 (BA_GATEWAY_HOST / BA_GATEWAY_PORT)

The HTTP API is pinned in services/browser-agent/README.md. This process is the only holder of the real
Vultr and Jev keys; clients authenticate with a per-session token minted by the controller.
"""

from __future__ import annotations

import hmac
import json
import os
import time
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from shared import config
from shared.prices import jev_usd, vultr_usd

from .calllog import CallLog
from .screening import Screener
from .sessions import SessionError, SessionStore
from .upstream import UpstreamError, Upstreams


@dataclass
class Settings:
    vultr_base: str
    jev_base: str
    vultr_key: str | None = field(default=None, repr=False)
    jev_key: str | None = field(default=None, repr=False)
    admin_token: str | None = field(default=None, repr=False)
    log_path: str = ".cache/gateway-calls.jsonl"
    jev_model: str = config.JEV_MODEL
    safety_model: str = config.SAFETY_MODEL

    @classmethod
    def from_env(cls) -> Settings:
        return cls(vultr_base=config.env(config.VULTR_BASE_ENV), jev_base=config.env(config.JEV_BASE_ENV),
                   vultr_key=os.environ.get(config.VULTR_KEY_ENV), jev_key=os.environ.get(config.JEV_KEY_ENV),
                   admin_token=os.environ.get(config.GATEWAY_ADMIN_TOKEN_ENV),
                   log_path=os.environ.get(config.GATEWAY_LOG_ENV) or ".cache/gateway-calls.jsonl")



def screen_summary(gate: dict | None) -> dict | None:
    """CONTRACT §12a `screen` object for the first flagged chunk of a call (no page text), or None."""
    for c in (gate or {}).get("chunks") or []:
        if c.get("flagged"):
            jev, safety = c.get("jev") or {}, c.get("safety") or {}
            verdict = safety.get("verdict")
            return {"flagged": True, "jev_choice": jev.get("choice"), "jev_confidence": jev.get("confidence"),
                    "safety_verdict": verdict if verdict in ("safe", "unsafe") else "unavailable",
                    "reason": c.get("reason") or ("injection" if jev.get("choice") == "injection" else "unsafe"),
                    "by": "gateway", "ts": time.time()}
    return None

def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    return auth[7:].strip() if auth.lower().startswith("bearer ") else None


async def _json(request: Request):
    try:
        return await request.json()
    except ValueError:
        raise HTTPException(400, "invalid JSON body") from None


def _vultr_tokens(usage: dict) -> tuple[int, int]:
    return int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0), \
        int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)


def _prompt_chars(messages) -> int:
    total = 0
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
        elif isinstance(c, list):
            total += sum(len(p.get("text") or "") for p in c if isinstance(p, dict))
    return total


def create_app(settings: Settings | None = None, store: SessionStore | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store or SessionStore()
    up = Upstreams(settings.vultr_base, settings.vultr_key, settings.jev_base, settings.jev_key)
    screener = Screener(up, settings.jev_model, settings.safety_model)
    log = CallLog(settings.log_path)
    app = FastAPI(title="browser-agent inference gateway", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.log, app.state.screener = store, log, screener

    @app.exception_handler(SessionError)
    def _session_error(request: Request, exc: SessionError):
        return JSONResponse({"error": exc.detail}, status_code=exc.status)

    def session_for(request: Request):
        return store.authorize(_bearer(request))

    def reporter(session, step_id):
        def report(*, upstream, purpose, model, status, usage, latency_ms, gate=None, prompt_chars=None,
                   est_tokens=False):
            if upstream == "jev":
                inp, out = int((usage or {}).get("input_tokens") or 0), int((usage or {}).get("output_tokens") or 0)
                usd = jev_usd(inp)
            else:
                inp, out = _vultr_tokens(usage or {})
                usd = vultr_usd(model, inp, out)
            if status == 200:
                store.charge(session.session_id, usd, flagged=bool(gate and gate.get("flagged")),
                             flag=screen_summary(gate))
            log.write(session_id=session.session_id, run_id=session.run_id, step_id=step_id, upstream=upstream,
                      purpose=purpose, model=model, status=status, input_tokens=inp, output_tokens=out,
                      est_usd=round(usd, 8), est_tokens=est_tokens or None, gate=gate,
                      latency_ms=round(latency_ms, 1) if latency_ms is not None else None, prompt_chars=prompt_chars)
        return report

    # --- session-token endpoints -------------------------------------------------------------------------------

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        session = session_for(request)
        step_id = request.headers.get("x-ba-step-id")
        body = await _json(request)
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list) or not body.get("model"):
            raise HTTPException(400, "model and messages are required")
        report = reporter(session, step_id)
        messages, gate = await run_in_threadpool(screener.screen, body["messages"], report)
        forward = {**body, "messages": messages}
        gate_header = "flagged" if gate.get("flagged") else "clean"
        chars = _prompt_chars(body["messages"])
        if body.get("stream"):
            return await _stream(forward, body["model"], report, gate, gate_header, chars)
        try:
            data, ms = await run_in_threadpool(up.chat, forward)
        except UpstreamError as exc:
            report(upstream="vultr", purpose="chat", model=body["model"], status=exc.status or 502, usage={},
                   latency_ms=None, gate=gate, prompt_chars=chars)
            return JSONResponse({"error": exc.detail}, status_code=502, headers={"X-BA-Gate": gate_header})
        report(upstream="vultr", purpose="chat", model=body["model"], status=200, usage=data.get("usage") or {},
               latency_ms=ms, gate=gate, prompt_chars=chars)
        return JSONResponse(data, headers={"X-BA-Gate": gate_header})

    async def _stream(forward, model, report, gate, gate_header, chars):
        try:
            client, resp = await run_in_threadpool(up.chat_stream, forward)
        except UpstreamError as exc:
            report(upstream="vultr", purpose="chat", model=model, status=exc.status or 502, usage={},
                   latency_ms=None, gate=gate, prompt_chars=chars)
            return JSONResponse({"error": exc.detail}, status_code=502, headers={"X-BA-Gate": gate_header})
        t0 = time.monotonic()

        def gen():
            usage, out_chars, buf = None, 0, b""
            try:
                for chunk in resp.iter_bytes():
                    buf += chunk
                    yield chunk
                for line in buf.decode(errors="replace").splitlines():
                    if not line.startswith("data:") or line.strip() == "data: [DONE]":
                        continue
                    try:
                        obj = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if obj.get("usage"):
                        usage = obj["usage"]
                    for ch in obj.get("choices") or []:
                        out_chars += len(((ch.get("delta") or {}).get("content")) or "")
            finally:
                resp.close()
                client.close()
                est = usage is None
                usage = usage or {"prompt_tokens": chars // 4, "completion_tokens": out_chars // 4}
                report(upstream="vultr", purpose="chat", model=model, status=200, usage=usage,
                       latency_ms=(time.monotonic() - t0) * 1000, gate=gate, prompt_chars=chars, est_tokens=est)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"X-BA-Gate": gate_header})

    @app.get("/v1/models")
    def models(request: Request):
        session_for(request)
        try:
            return up.models()
        except UpstreamError as exc:
            return JSONResponse({"error": exc.detail}, status_code=502)

    @app.post("/v1/jev/systemone")
    async def jev(request: Request):
        session = session_for(request)
        step_id = request.headers.get("x-ba-step-id")
        body = await _json(request)
        if not isinstance(body, dict) or "state" not in body or not body.get("questions"):
            raise HTTPException(400, "state and questions are required")
        report = reporter(session, step_id)
        try:
            data, ms, _rid = await run_in_threadpool(up.jev, {"model": settings.jev_model, "state": body["state"],
                                     "questions": body["questions"]})
        except UpstreamError as exc:
            report(upstream="jev", purpose="decision", model=settings.jev_model, status=exc.status or 502, usage={},
                   latency_ms=None)
            return JSONResponse({"error": exc.detail}, status_code=502)
        report(upstream="jev", purpose="decision", model=data.get("model") or settings.jev_model, status=200,
               usage=data.get("usage") or {}, latency_ms=ms)
        return data

    # --- admin endpoints (controller only) ---------------------------------------------------------------------

    def admin(request: Request):
        if not settings.admin_token:
            raise HTTPException(503, "admin token not configured")
        token = _bearer(request) or ""
        if not hmac.compare_digest(token.encode(), settings.admin_token.encode()):
            raise HTTPException(401, "admin token required")

    @app.post("/admin/sessions")
    async def open_session(request: Request):
        admin(request)
        body = await _json(request)
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object required")
        try:
            sid = str(body["session_id"])
            ttl = float(body.get("ttl_s", 900))
            budget = float(body.get("budget_usd", 1.0))
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "session_id, ttl_s, budget_usd required") from None
        token, s = store.create(sid, ttl, budget, body.get("run_id"))
        return {"session_id": sid, "token": token, "expires_at": s.expires_at}

    @app.get("/admin/sessions/{session_id}")
    def session_info(request: Request, session_id: str):
        admin(request)
        s = store.get(session_id)
        if not s:
            raise HTTPException(404, "no such session")
        return s.view()

    @app.post("/admin/sessions/{session_id}/revoke")
    def revoke(request: Request, session_id: str):
        admin(request)
        if not store.revoke(session_id):
            raise HTTPException(404, "no such session")
        return {"revoked": True}

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "vultr_key": bool(settings.vultr_key), "jev_key": bool(settings.jev_key)}

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=os.environ.get("BA_GATEWAY_HOST", "127.0.0.1"),
                port=int(os.environ.get("BA_GATEWAY_PORT", "8700")))


if __name__ == "__main__":
    main()
