"""Control-plane MCP client for the browser controller's session tools."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

from ontofill.lake import FileLake, S3Lake
from ontofill.lake.storage import BRONZE_KEY
from ontofill.runfeed import RunFeed


class ObservationQuarantined(RuntimeError):
    """The controller did not provide an explicit safe screening verdict."""

    def __init__(self, message: str, *, screen_status: str = "uncertain") -> None:
        super().__init__(message)
        self.screen_status = screen_status


def screen_is_cleared(screen: object) -> bool:
    if not isinstance(screen, dict) or screen.get("flagged") is not False:
        return False
    verdict = screen.get("safety_verdict")
    if verdict == "unsafe":
        return False
    if verdict == "safe":
        return True
    if (
        verdict == "unavailable"
        and screen.get("by") == "gateway"
        and screen.get("reason") == "gateway X-BA-Gate: clean"
    ):
        return True
    confidence = screen.get("jev_confidence")
    return (
        verdict == "unavailable"
        and screen.get("jev_choice") == "benign"
        and type(confidence) in {int, float}
        and confidence >= 0.8
    )


class BrowserAgentClient:
    """Call session tools through MCP Streamable HTTP on the control plane."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 60,
        feed: RunFeed | None = None,
    ) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("browser controller endpoint must be HTTP(S)")
        self.endpoint = endpoint
        self.client = client or httpx.Client(timeout=timeout)
        self.feed = feed
        self._next_id = 1
        self._session_header: str | None = None
        self._initialized = False
        self._live_view_session_id: str | None = None

    @classmethod
    def from_env(
        cls, *, client: httpx.Client | None = None, feed: RunFeed | None = None
    ) -> BrowserAgentClient:
        endpoint = os.getenv("ONTOFILL_BROWSER_AGENT_MCP_URL", "")
        if not endpoint:
            raise RuntimeError("set ONTOFILL_BROWSER_AGENT_MCP_URL")
        return cls(endpoint, client=client, feed=feed)

    def _set_live_view(self, url: str | None) -> None:
        if self.feed is None:
            return
        status = self.feed.current_status
        if status is None:
            raise RuntimeError("run status must be initialized before opening a browser session")
        self.feed.update_status(
            state=status["state"],
            phase=status["phase"],
            checkpoint_pending=status["checkpoint_pending"],
            live_view_url=url,
        )

    def _post(self, body: dict) -> dict:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self._session_header:
            headers["Mcp-Session-Id"] = self._session_header
        response = self.client.post(self.endpoint, headers=headers, json=body)
        response.raise_for_status()
        self._session_header = response.headers.get("Mcp-Session-Id", self._session_header)
        if "id" not in body:
            return {}
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            messages = [
                json.loads(line[5:].strip())
                for line in response.text.splitlines()
                if line.startswith("data:") and line[5:].strip() != "[DONE]"
            ]
            matches = [item for item in messages if item.get("id") == body["id"]]
            if not matches:
                raise ValueError("MCP event stream contained no matching response")
            payload = matches[-1]
        else:
            payload = response.json()
        if payload.get("id") != body["id"] or payload.get("jsonrpc") != "2.0":
            raise ValueError("MCP response ID or version mismatch")
        if error := payload.get("error"):
            raise RuntimeError(f"browser controller MCP error: {error.get('code')}")
        return payload.get("result") or {}

    def _rpc(self, method: str, params: dict) -> dict:
        request_id = self._next_id
        self._next_id += 1
        return self._post({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})

    def _initialize(self) -> None:
        if self._initialized:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "ontofill", "version": "0.1"},
            },
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._initialized = True

    def _tool(self, name: str, arguments: dict) -> dict:
        self._initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(f"browser controller tool failed: {name}")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        for item in result.get("content", []):
            if item.get("type") == "text":
                parsed: Any = json.loads(item["text"])
                if isinstance(parsed, dict):
                    return parsed
        raise ValueError(f"browser controller tool returned no object: {name}")

    def session_open(
        self, tdd: dict, allowed_domains: list[str], limits: dict | None = None
    ) -> dict:
        if self.feed is not None and self.feed.current_status is None:
            raise RuntimeError("run status must be initialized before opening a browser session")
        result = self._tool(
            "session.open",
            {"tdd": tdd, "allowed_domains": allowed_domains, "limits": limits},
        )
        if not result.get("session_id"):
            raise ValueError("session.open returned no session_id")
        live_view_url = result.get("live_view_url")
        if live_view_url is not None and (
            not isinstance(live_view_url, str)
            or not live_view_url.startswith(("http://", "https://"))
        ):
            raise ValueError("session.open returned an invalid live_view_url")
        if live_view_url is not None:
            self._set_live_view(live_view_url)
            self._live_view_session_id = result["session_id"]
        return result

    def session_act(
        self, session_id: str, *, goal: str | None = None, action: dict | None = None
    ) -> dict:
        if bool(goal) == bool(action):
            raise ValueError("pass exactly one browser goal or action")
        return self._tool("session.act", {"session_id": session_id, "goal": goal, "action": action})

    def session_observe(self, session_id: str) -> dict:
        result = self._tool("session.observe", {"session_id": session_id})
        screen = result.get("screen")
        if not screen_is_cleared(screen):
            status = (
                "missing"
                if screen is None
                else "unsafe"
                if (
                    isinstance(screen, dict)
                    and (screen.get("flagged") is True or screen.get("safety_verdict") == "unsafe")
                )
                else "uncertain"
            )
            raise ObservationQuarantined(
                f"browser observation withheld for session {session_id}",
                screen_status=status,
            )
        return result

    def session_close(self, session_id: str) -> dict:
        try:
            return self._tool("session.close", {"session_id": session_id})
        finally:
            if self._live_view_session_id == session_id:
                self._set_live_view(None)
                self._live_view_session_id = None


class BrowserTraceBridge:
    """Copy controller captures and append new MCP session steps to the lake feed."""

    def __init__(
        self,
        *,
        session_id: str,
        steps_path: Path,
        steps_root: Path,
        captures_root: Path,
        feed: RunFeed,
        lake: FileLake | S3Lake,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", session_id):
            raise ValueError("invalid browser session ID")
        if (
            not steps_path.is_absolute()
            or not steps_root.is_absolute()
            or not captures_root.is_absolute()
        ):
            raise ValueError("browser trace and capture roots must be absolute")
        self.steps_path = steps_path.resolve()
        self.steps_root = steps_root.resolve()
        self.captures_root = captures_root.resolve()
        if (
            not self.steps_path.is_relative_to(self.steps_root)
            or self.steps_path.name != f"{session_id}.jsonl"
        ):
            raise ValueError("browser steps path is outside its configured session root")
        self.session_id = session_id
        self.feed = feed
        self.lake = lake
        self._offset = 0

    def _mirror_bronze(self, key: str) -> None:
        if not BRONZE_KEY.fullmatch(key):
            raise ValueError("browser step has an invalid bronze key")
        if self.lake.exists(key):
            return
        hexdigest = key.removeprefix("sha256:")
        path = (self.captures_root / "bronze" / "sha256" / hexdigest).resolve()
        sidecar = path.with_name(hexdigest + ".meta.json").resolve()
        if not path.is_relative_to(self.captures_root) or not sidecar.is_relative_to(
            self.captures_root
        ):
            raise ValueError("browser capture is outside its configured root")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != hexdigest:
            raise ValueError("browser capture content does not match its key")
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise TypeError("browser capture metadata must be an object")
        written = self.lake.put_bytes(data, metadata=metadata)
        if written != key:
            raise ValueError("browser capture key changed while mirroring")

    def drain(self) -> int:
        """Ingest complete new JSONL rows; an incomplete last line waits for the next call."""
        if not self.steps_path.exists():
            return 0
        data = self.steps_path.read_bytes()
        if len(data) < self._offset:
            raise ValueError("browser step log was truncated")
        new = 0
        for line in data[self._offset :].splitlines(keepends=True):
            if not line.endswith(b"\n"):
                break
            step = json.loads(line)
            if step.get("session_id") != self.session_id:
                raise ValueError("browser step belongs to another session")
            for key in (
                step.get("screenshot_key"),
                (step.get("verify") or {}).get("screenshot_key"),
                (step.get("repair") or {}).get("code_key"),
                (step.get("repair") or {}).get("diff_key"),
            ):
                if key is not None:
                    self._mirror_bronze(key)
            self.feed.append_step(step)
            self._offset += len(line)
            new += 1
        return new
