"""Live, backend-neutral lake feed for the investigation run view."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from threading import Event, Lock, Thread
from typing import Protocol, Self

from ontofill.refiner.export import _validators
from ontofill.refiner.provenance import validate_generated_by, validate_run_provenance


class FeedLake(Protocol):
    def write_key(self, key: str, data: bytes) -> None: ...

    def read_key(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...


def _json_bytes(document: dict) -> bytes:
    return (json.dumps(document, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


_UNSET = object()


class RunFeed:
    """Append steps immediately and refresh status on transitions or every interval."""

    def __init__(
        self,
        lake: FeedLake,
        case_id: str,
        run_id: str,
        generated_by: dict[str, str],
        *,
        preview: bool = False,
        interval_seconds: float = 10,
        clock: Callable[[], datetime] | None = None,
        start_heartbeat: bool = True,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("status interval must be positive")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", case_id) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*", run_id
        ):
            raise ValueError("case_id and run_id must be safe lake path segments")
        self.lake = lake
        self.case_id = case_id
        self.run_id = run_id
        self.generated_by = validate_run_provenance(run_id, generated_by)
        self.preview = preview
        self.interval = timedelta(seconds=interval_seconds)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.prefix = f"runs/{case_id}/{run_id}"
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._start_heartbeat = start_heartbeat
        self._last_status_write: datetime | None = None
        self._status: dict | None = None
        self._background_error: Exception | None = None
        self._closed = False
        self._validators = _validators()

    def _check_ready(self) -> None:
        if self._closed:
            raise RuntimeError("run feed is closed")
        if self._background_error is not None:
            raise RuntimeError("run feed heartbeat failed") from self._background_error

    def append_trace(self, step: dict, *, screenshot_key: str | None = None) -> None:
        """Append one schema-valid trace step, optionally with its captured screenshot."""
        record = step.copy()
        if screenshot_key is not None:
            record["screenshot_key"] = screenshot_key
        self._validators["trace-step"].validate(record)
        if record["run_id"] != self.run_id:
            raise ValueError("trace step run_id differs from feed")
        step_backend = validate_generated_by(record["generated_by"])["backend"]
        run_backend = self.generated_by["backend"]
        if step_backend != run_backend and not (run_backend == "vultr" and step_backend == "jev"):
            raise ValueError("trace step inference backend differs from feed")
        if step_backend == "jev" and record["value_ids"]:
            raise ValueError("Jev trace cannot ground gold values")
        if record.get("screenshot_key") and not self.lake.exists(record["screenshot_key"]):
            raise ValueError("trace screenshot is absent from bronze")
        with self._lock:
            self._check_ready()
            key = f"{self.prefix}/trace.live.jsonl"
            existing = self.lake.read_key(key) if self.lake.exists(key) else b""
            self.lake.write_key(key, existing + _json_bytes(record))
            self._write_status_if_due(self.clock())

    def append_step(self, step: dict, *, screenshot_key: str | None = None) -> None:
        """Workflow-facing name for appending an observed execution step."""
        self.append_trace(step, screenshot_key=screenshot_key)

    def update_status(
        self,
        *,
        state: str,
        phase: int,
        checkpoint_pending: str | None = None,
        reason: str | None = None,
        sources: Sequence[Mapping] | None = None,
        metrics: Mapping | None = None,
        live_view_url: str | None | object = _UNSET,
    ) -> dict:
        """Write immediately on state/phase/checkpoint changes, then at least every 10 s."""
        if state not in {"running", "paused", "done", "failed"}:
            raise ValueError("invalid run state")
        if phase not in range(1, 6):
            raise ValueError("phase must be 1..5")
        if checkpoint_pending not in {None, "prd", "factors", "ontology", "source", "action"}:
            raise ValueError("invalid checkpoint")
        with self._lock:
            self._check_ready()
            previous = self._status
            self._status = {
                "run_id": self.run_id,
                "state": state,
                "phase": phase,
                "checkpoint_pending": checkpoint_pending,
                "updated_at": self.clock().isoformat(),
                "sources": (
                    [dict(source) for source in sources]
                    if sources is not None
                    else (previous["sources"] if previous else [])
                ),
                "metrics": (
                    dict(metrics)
                    if metrics is not None
                    else (previous["metrics"] if previous else {})
                ),
                "generated_by": self.generated_by.copy(),
                "preview": self.preview,
            }
            if reason is not None:
                self._status["reason"] = reason
            if live_view_url is not _UNSET:
                if live_view_url is not None:
                    self._status["live_view_url"] = live_view_url
            elif previous and "live_view_url" in previous:
                self._status["live_view_url"] = previous["live_view_url"]
            changed = (
                previous is None
                or any(
                    previous[key] != self._status[key]
                    for key in ("state", "phase", "checkpoint_pending")
                )
                or (
                    previous is not None
                    and previous.get("live_view_url") != self._status.get("live_view_url")
                )
            )
            self._write_status_if_due(self.clock(), force=changed)
            if self._start_heartbeat and self._thread is None and state == "running":
                self._thread = Thread(target=self._heartbeat_loop, daemon=True)
                self._thread.start()
            return self._status.copy()

    def heartbeat(self) -> None:
        """Explicit heartbeat for event loops and deterministic tests."""
        with self._lock:
            self._check_ready()
            self._write_status_if_due(self.clock())

    @property
    def current_status(self) -> dict | None:
        with self._lock:
            return self._status.copy() if self._status is not None else None

    def _write_status_if_due(self, now: datetime, *, force: bool = False) -> None:
        if self._status is None:
            return
        if (
            not force
            and self._last_status_write is not None
            and now - self._last_status_write < self.interval
        ):
            return
        status = self._status.copy()
        status["updated_at"] = now.isoformat()
        validator = self._validators.get("run-status")
        if validator is not None:
            validator.validate(status)
        first_write = self._last_status_write is None
        self.lake.write_key(f"{self.prefix}/status.json", _json_bytes(status))
        self._status = status
        self._last_status_write = now
        if first_write and self.generated_by["backend"] == "vultr" and not self.preview:
            self.lake.write_key(
                f"runs/{self.case_id}/latest.json", _json_bytes({"run_id": self.run_id})
            )

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.interval.total_seconds()):
            try:
                with self._lock:
                    self._write_status_if_due(self.clock())
            except Exception as exc:  # noqa: BLE001 - propagate worker errors on foreground calls
                self._background_error = exc
                return

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval.total_seconds() + 1)
        with self._lock:
            self._check_ready()
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
