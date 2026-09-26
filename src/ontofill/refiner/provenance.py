"""Inference provenance that cannot be silently relabeled at export."""

from __future__ import annotations

from datetime import datetime


def validate_generated_by(generated_by: dict[str, str]) -> dict[str, str]:
    if not isinstance(generated_by, dict) or set(generated_by) != {"backend", "model", "at"}:
        raise ValueError("generated_by requires backend, model, and at")
    if generated_by["backend"] not in {"recorded", "vultr"}:
        raise ValueError("generated_by backend must be recorded or vultr")
    if not isinstance(generated_by["model"], str) or not generated_by["model"].strip():
        raise ValueError("generated_by model must be nonempty")
    if not isinstance(generated_by["at"], str):
        raise TypeError("generated_by at must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(generated_by["at"])
    except ValueError as exc:
        raise ValueError("generated_by at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("generated_by at requires a timezone")
    return generated_by.copy()


def validate_run_provenance(run_id: str, generated_by: dict[str, str]) -> dict[str, str]:
    valid = validate_generated_by(generated_by)
    if valid["backend"] == "recorded" and not run_id.startswith("mock-"):
        raise ValueError("recorded output requires a mock- run ID")
    if valid["backend"] == "vultr" and run_id.startswith("mock-"):
        raise ValueError("mock- run IDs are reserved for recorded inference")
    return valid
