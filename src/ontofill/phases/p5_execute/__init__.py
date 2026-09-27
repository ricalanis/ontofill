"""Read-only S1 execution for a discovered source."""

from .phase import ExecutionResult, execute_objective, execute_objectives, normalize_identifier

__all__ = [
    "ExecutionResult",
    "execute_objective",
    "execute_objectives",
    "normalize_identifier",
]
