"""Cells (CONTRACT §13a): the provider interface the controller leases browser "hands" from, and the warm pool.

A cell provider has exactly three calls, in the shape Codex's substrate will expose:

    create(backend: "native"|"skyvern", allowed_domains, limits, placement: "sandbox_vm"|"throwaway_vx1")
        -> {cell_id, cdp_url, brain_url?, live_view_port}   (a dict or a Cell)
    destroy(cell_id) -> None
    status(cell_id) -> dict

The pool keeps K warm cells per backend and leases one per session. A cell's allowed domains are fixed when it is
created (the egress proxy is configured then), so a warm cell is only handed out when its domain set and limits
match the lease; warm cells are pre-created for the most recent (domains, limits) seen per backend, and a lease
that does not match creates a fresh cell. Release = destroy, then a warm replacement is created in the background:
a cell is never reused across sessions.

Provider selection: `BA_CELL_PROVIDER` = `none` (default: the backend launches or connects to a browser itself),
`docker-stub` (cells.docker_stub, local stand-in, NOT a sandbox) or `ontofill` (the engine's substrate module).
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

PROVIDER_ENV = "BA_CELL_PROVIDER"
K_NATIVE_ENV = "BA_CELL_K_NATIVE"
K_SKYVERN_ENV = "BA_CELL_K_SKYVERN"
BACKENDS = ("native", "skyvern")
PLACEMENTS = ("sandbox_vm", "throwaway_vx1")


@dataclass
class Cell:
    cell_id: str
    backend: str
    cdp_url: str | None
    brain_url: str | None = None
    live_view_port: int | None = None
    placement: str = "sandbox_vm"
    isolation: dict = field(default_factory=dict)  # {runtime, tier, egress, ...} as the provider reports it
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds"))
    allowed_domains: tuple[str, ...] = ()
    meta: dict = field(default_factory=dict)  # provider-specific extras (e.g. the stub's network name)

    @classmethod
    def from_any(cls, value: Any, backend: str, allowed_domains, placement: str) -> Cell:
        """Accept a Cell or the contract's dict {cell_id, cdp_url, brain_url?, live_view_port, ...}."""
        if isinstance(value, Cell):
            return value
        if not isinstance(value, dict) or not value.get("cell_id"):
            raise CellError(f"provider returned no cell_id: {type(value).__name__}")
        known = {k: value[k] for k in ("cdp_url", "brain_url", "live_view_port", "isolation", "created_at")
                 if value.get(k) is not None}
        extra = {k: v for k, v in value.items() if k not in cls.__dataclass_fields__}
        return cls(cell_id=str(value["cell_id"]), backend=value.get("backend", backend),
                   placement=value.get("placement", placement), allowed_domains=_domains(allowed_domains),
                   meta=extra, **{"cdp_url": None, **known})

    def as_dict(self) -> dict:
        d = asdict(self)
        d["allowed_domains"] = list(self.allowed_domains)
        return d


class CellError(RuntimeError):
    """A cell could not be created, leased or destroyed."""


class CellProvider(Protocol):
    def create(self, backend: str, allowed_domains: list[str], limits: dict | None = None,
               placement: str = "sandbox_vm") -> Cell | dict: ...

    def destroy(self, cell_id: str) -> None: ...

    def status(self, cell_id: str) -> dict: ...


def _domains(allowed_domains) -> tuple[str, ...]:
    return tuple(sorted({str(d).lower().strip().lstrip(".") for d in allowed_domains or []}))


def _limits_key(limits: dict | None) -> tuple:
    """Only the caps a provider applies at create time matter for matching a warm cell."""
    limits = limits or {}
    return tuple((k, limits.get(k)) for k in ("memory_mb", "cpus", "pids", "timeout_s") if limits.get(k) is not None)


def _ms(t0: float) -> float:
    return round((time.monotonic() - t0) * 1000, 1)


class CellPool:
    """Warm pool per backend; lease one cell per session; recycle = destroy + recreate, never reuse."""

    def __init__(self, provider: CellProvider, k_native: int = 1, k_skyvern: int = 0, placement: str = "sandbox_vm",
                 background: bool = True):
        if placement not in PLACEMENTS:
            raise ValueError(f"placement must be one of {PLACEMENTS}")
        self.provider = provider
        self.k = {"native": max(0, k_native), "skyvern": max(0, k_skyvern)}
        self.placement = placement
        self.background = background
        self._warm: dict[str, list[tuple[Cell, tuple]]] = {b: [] for b in BACKENDS}
        self._target: dict[str, tuple | None] = {b: None for b in BACKENDS}  # (domains, limits_key, limits)
        self._leased: dict[str, Cell] = {}
        self._retired: set[str] = set()  # every cell id ever released or destroyed: never handed out again
        self._pending: dict[str, int] = {b: 0 for b in BACKENDS}
        self.records: dict[str, dict] = {}  # cell_id -> {create_ms, lease_ms, destroy_ms, warm, backend}
        self.errors: list[str] = []
        self._lock = threading.RLock()
        self._closed = False
        self._exec = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ba-cells")
        self._futures: list[Future] = []

    # --- provider calls with timing -----------------------------------------------------------------------
    def _create(self, backend: str, domains: tuple[str, ...], limits: dict | None) -> Cell:
        t0 = time.monotonic()
        raw = self.provider.create(backend, list(domains), dict(limits or {}), self.placement)
        cell = Cell.from_any(raw, backend, domains, self.placement)
        if not cell.allowed_domains:
            cell.allowed_domains = domains
        with self._lock:
            if cell.cell_id in self._retired or cell.cell_id in self._leased:
                raise CellError(f"provider returned a cell id already used: {cell.cell_id}")
            self.records[cell.cell_id] = {"backend": backend, "create_ms": _ms(t0), "lease_ms": None,
                                          "destroy_ms": None, "warm": None}
        return cell

    def _destroy(self, cell: Cell) -> float:
        t0 = time.monotonic()
        with self._lock:
            self._retired.add(cell.cell_id)
        try:
            self.provider.destroy(cell.cell_id)
        except Exception as exc:  # noqa: BLE001 - any provider failure is recorded; a sweep/reaper cleans up
            self.errors.append(f"destroy {cell.cell_id}: {exc}")
        ms = _ms(t0)
        with self._lock:
            self.records.setdefault(cell.cell_id, {})["destroy_ms"] = ms
        return ms

    # --- warm pool ----------------------------------------------------------------------------------------
    def prewarm(self, backend: str, allowed_domains, limits: dict | None = None) -> None:
        """Set the (domains, limits) that warm cells are made for, and top the pool up to K."""
        self._check_backend(backend)
        domains = _domains(allowed_domains)
        with self._lock:
            self._target[backend] = (domains, _limits_key(limits), dict(limits or {}))
            stale = [(c, key) for c, key in self._warm[backend] if key != (domains, _limits_key(limits))]
            self._warm[backend] = [(c, key) for c, key in self._warm[backend] if (c, key) not in stale]
        for cell, _key in stale:  # made for an older domain set: recycle
            self._submit(self._destroy, cell)
        self._replenish(backend)

    def _replenish(self, backend: str) -> None:
        with self._lock:
            target = self._target[backend]
            if self._closed or target is None:
                return
            need = self.k[backend] - len(self._warm[backend]) - self._pending[backend]
            self._pending[backend] += max(0, need)
        for _ in range(max(0, need)):
            self._submit(self._warm_one, backend, target)

    def _warm_one(self, backend: str, target: tuple) -> None:
        domains, lkey, limits = target
        try:
            cell = self._create(backend, domains, limits)
        except Exception as exc:  # noqa: BLE001 - a failed warm-up is recorded; the next lease creates on demand
            self.errors.append(f"warm {backend}: {exc}")
            with self._lock:
                self._pending[backend] -= 1
            return
        with self._lock:
            self._pending[backend] -= 1
            if self._closed or self._target[backend] is None or self._target[backend][:2] != (domains, lkey):
                keep = False
            else:
                self._warm[backend].append((cell, (domains, lkey)))
                keep = True
        if not keep:
            self._destroy(cell)

    def _submit(self, fn, *args) -> None:
        if self.background and not self._closed:
            fut = self._exec.submit(fn, *args)
            with self._lock:
                self._futures = [f for f in self._futures if not f.done()] + [fut]
        else:
            fn(*args)

    def wait(self, timeout: float | None = 60) -> None:
        """Block until background creates/destroys finish (tests, shutdown)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                futures = [f for f in self._futures if not f.done()]
            if not futures:
                return
            for f in futures:
                f.result(timeout=None if deadline is None else max(0.0, deadline - time.monotonic()))

    # --- lease / release ----------------------------------------------------------------------------------
    def lease(self, backend: str, allowed_domains, limits: dict | None = None) -> Cell:
        self._check_backend(backend)
        if self._closed:
            raise CellError("cell pool is shut down")
        t0 = time.monotonic()
        domains = _domains(allowed_domains)
        key = (domains, _limits_key(limits))
        cell = None
        with self._lock:
            for i, (c, ckey) in enumerate(self._warm[backend]):
                if ckey == key and c.cell_id not in self._retired:
                    cell = self._warm[backend].pop(i)[0]
                    break
        warm = cell is not None
        if cell is None:
            try:
                cell = self._create(backend, domains, limits)
            except CellError:
                raise
            except Exception as exc:
                raise CellError(f"could not create a {backend} cell: {exc}") from exc
        with self._lock:
            self._leased[cell.cell_id] = cell
            rec = self.records.setdefault(cell.cell_id, {"backend": backend})
            rec.update(lease_ms=_ms(t0), warm=warm)
        self.prewarm(backend, domains, limits)  # the most recent domain set is what the next session likely needs
        return cell

    def release(self, cell: Cell | str) -> dict:
        """Destroy the cell now (teardown is part of the session's proof), then replenish in the background."""
        cell_id = cell if isinstance(cell, str) else cell.cell_id
        with self._lock:
            leased = self._leased.pop(cell_id, None)
        if leased is None:
            return {"cell_id": cell_id, "released": False, "error": "not leased from this pool"}
        destroy_ms = self._destroy(leased)
        self._replenish(leased.backend)
        return {"cell_id": cell_id, "released": True, "destroy_ms": destroy_ms}

    def timings(self, cell_id: str) -> dict:
        with self._lock:
            return dict(self.records.get(cell_id) or {})

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            warm = [c for b in BACKENDS for c, _ in self._warm[b]]
            leased = list(self._leased.values())
            for b in BACKENDS:
                self._warm[b] = []
            self._leased.clear()
        self.wait(timeout=120)
        for cell in warm + leased:
            self._destroy(cell)
        self._exec.shutdown(wait=True)

    def _check_backend(self, backend: str) -> None:
        if backend not in BACKENDS:
            raise ValueError(f"backend must be one of {BACKENDS}")


class ModuleProvider:
    """Adapts a module exposing create/destroy/status functions (the engine's substrate) to CellProvider."""

    def __init__(self, module):
        missing = [n for n in ("create", "destroy", "status") if not callable(getattr(module, n, None))]
        if missing:
            raise CellError(f"{module.__name__} lacks {', '.join(missing)} (CONTRACT §13a cell API)")
        self.module = module

    def create(self, backend, allowed_domains, limits=None, placement="sandbox_vm"):
        return self.module.create(backend=backend, allowed_domains=allowed_domains, limits=limits,
                                  placement=placement)

    def destroy(self, cell_id):
        return self.module.destroy(cell_id)

    def status(self, cell_id):
        return self.module.status(cell_id)


def provider_from_env(name: str | None = None) -> CellProvider | None:
    name = (name if name is not None else os.environ.get(PROVIDER_ENV, "none")).strip().lower() or "none"
    if name == "none":
        return None
    if name == "docker-stub":
        from cells.docker_stub import DockerStubProvider

        return DockerStubProvider()
    if name == "ontofill":
        try:
            import ontofill.cells as substrate  # the engine's cell substrate (CONTRACT §13a), when installed
        except ImportError as exc:
            raise CellError("BA_CELL_PROVIDER=ontofill but the engine's cell substrate (ontofill.cells) is not "
                            "installed in this environment") from exc
        return ModuleProvider(substrate)
    raise CellError(f"unknown {PROVIDER_ENV}={name!r} (none | docker-stub | ontofill)")


def pool_from_env() -> CellPool | None:
    provider = provider_from_env()
    if provider is None:
        return None
    return CellPool(provider, k_native=int(os.environ.get(K_NATIVE_ENV, "1")),
                    k_skyvern=int(os.environ.get(K_SKYVERN_ENV, "0")))
