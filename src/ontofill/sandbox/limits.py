"""Validated resource budget shared by one-shot captures and browser sessions."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SandboxLimits:
    memory_mb: int = 1024
    cpus: float = 1.0
    pids: int = 256
    timeout_s: int = 90
    max_steps: int = 32

    def __post_init__(self) -> None:
        if type(self.memory_mb) is not int or not 128 <= self.memory_mb <= 16384:
            raise ValueError("memory_mb must be between 128 and 16384")
        if (
            type(self.cpus) not in {int, float}
            or not math.isfinite(self.cpus)
            or not 0 < self.cpus <= 16
        ):
            raise ValueError("cpus must be between 0 and 16")
        if type(self.pids) is not int or not 16 <= self.pids <= 4096:
            raise ValueError("pids must be between 16 and 4096")
        if type(self.timeout_s) is not int or not 1 <= self.timeout_s <= 3600:
            raise ValueError("timeout_s must be between 1 and 3600")
        if type(self.max_steps) is not int or not 1 <= self.max_steps <= 10000:
            raise ValueError("max_steps must be between 1 and 10000")

    @classmethod
    def from_value(cls, value: SandboxLimits | Mapping[str, object] | None) -> SandboxLimits:
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("limits must contain memory_mb, cpus, pids, timeout_s, max_steps")
        return cls(**value)

    def as_dict(self) -> dict:
        return asdict(self)

    def docker_args(self) -> tuple[str, ...]:
        memory = f"{self.memory_mb}m"
        return (
            "--memory",
            memory,
            "--memory-swap",
            memory,
            "--cpus",
            str(self.cpus),
            "--pids-limit",
            str(self.pids),
        )
