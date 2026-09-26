"""Loopback-only HTTP control endpoint for browser cell lifecycles."""

from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from ontofill.sandbox.capture import CaptureError
from ontofill.sandbox.cells import CellError, CellManager


def make_handler(manager: CellManager, token: str) -> type[BaseHTTPRequestHandler]:
    if not token:
        raise ValueError("cell API requires a nonempty bearer token")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def _send(self, status: int, payload: dict) -> None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            if hmac.compare_digest(supplied, "Bearer " + token):
                return True
            self._send(401, {"error": "unauthorized"})
            return False

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    raise ValueError("body must be 1..32768 bytes")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise TypeError("body must be a JSON object")
                return body
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("invalid JSON body") from exc

        def _cell_path(self) -> tuple[str, str | None]:
            parts = urlsplit(self.path).path.strip("/").split("/")
            if len(parts) < 2 or parts[0] != "cells":
                raise ValueError("unknown endpoint")
            cell_id = unquote(parts[1])
            if "/" in cell_id or not cell_id.startswith("cell:"):
                raise ValueError("invalid cell id")
            return cell_id, parts[2] if len(parts) == 3 else None

        def do_GET(self) -> None:
            if not self._authorized():
                return
            try:
                cell_id, action = self._cell_path()
                if action is not None:
                    raise ValueError("unknown endpoint")
                self._send(200, manager.status(cell_id))
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            except CellError as exc:
                self._send(404, {"error": str(exc)})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            try:
                body = self._body()
                path = urlsplit(self.path).path
                if path == "/cells":
                    result = manager.create(
                        body["backend"],
                        body["allowed_domains"],
                        body.get("limits"),
                        body.get("placement", "sandbox_vm"),
                        skyvern=body.get("skyvern"),
                        brain_env=body.get("brain_env"),
                    )
                    self._send(201, result)
                else:
                    cell_id, action = self._cell_path()
                    if action == "steps":
                        self._send(200, {"steps": manager.record_step(cell_id)})
                    elif action == "task-result":
                        manager.report_task_result(cell_id, body["result"], ok=body["ok"])
                        self._send(200, {"ok": True})
                    else:
                        raise ValueError("unknown endpoint")
            except (ValueError, KeyError, TypeError) as exc:
                self._send(400, {"error": str(exc)})
            except (CellError, CaptureError) as exc:
                self._send(409, {"error": str(exc)})

        def do_DELETE(self) -> None:
            if not self._authorized():
                return
            try:
                cell_id, action = self._cell_path()
                if action is not None:
                    raise ValueError("unknown endpoint")
                self._send(200, manager.destroy(cell_id))
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            except CellError as exc:
                self._send(404, {"error": str(exc)})

    return Handler


def serve_cells(manager: CellManager, *, token: str, port: int = 8766) -> ThreadingHTTPServer:
    """Return an unstarted server bound only to control-plane loopback."""
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(manager, token))
