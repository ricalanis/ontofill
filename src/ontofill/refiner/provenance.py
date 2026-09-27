"""Inference provenance that cannot be silently relabeled at export."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

_AUTHORITY_TIERS = {"primary", "secondary", "low", "review", "unknown"}
_PUBLISHER_BASES = {"approved_policy", "captured_page"}


def observation_source_metadata(objective: Mapping[str, object]) -> dict[str, str | None]:
    """Return tier and publisher identity from the confirmed P3 objective metadata."""
    publisher = objective.get("publisher_of_record")
    basis = publisher.get("basis") if isinstance(publisher, Mapping) else None
    if not isinstance(basis, str) or basis not in _PUBLISHER_BASES:
        publisher = None

    tier = objective.get("authority_tier")
    if not isinstance(tier, str) or tier not in _AUTHORITY_TIERS:
        tier = "unknown"
    if tier == "unknown" and publisher is not None:
        publisher_tier = publisher.get("tier")
        if isinstance(publisher_tier, str) and publisher_tier in _AUTHORITY_TIERS - {"unknown"}:
            tier = publisher_tier

    publisher_id: str | None = None
    if publisher is not None:
        # The publisher name joins approved mirrors and aliases; the domain is a fallback.
        kind = publisher.get("kind")
        if isinstance(kind, str):
            normalized_kind = " ".join(kind.casefold().split())
            if normalized_kind:
                publisher_id = f"name:{normalized_kind}"
        if publisher_id is None:
            domain = publisher.get("domain")
            if isinstance(domain, str):
                normalized_domain = domain.strip().casefold().rstrip(".")
                if (
                    normalized_domain
                    and len(normalized_domain) <= 253
                    and not any(char.isspace() for char in normalized_domain)
                    and not any(char in normalized_domain for char in "/\\?#@:")
                ):
                    publisher_id = f"domain:{normalized_domain}"

    return {"authority_tier": tier, "publisher_id": publisher_id}


def validate_generated_by(generated_by: dict[str, str]) -> dict[str, str]:
    if not isinstance(generated_by, dict) or set(generated_by) != {"backend", "model", "at"}:
        raise ValueError("generated_by requires backend, model, and at")
    if generated_by["backend"] not in {"recorded", "vultr", "jev"}:
        raise ValueError("generated_by backend must be recorded, vultr, or jev")
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
    if valid["backend"] == "jev":
        raise ValueError("Jev cannot be the primary run backend")
    if valid["backend"] == "recorded" and not run_id.startswith("mock-"):
        raise ValueError("recorded output requires a mock- run ID")
    if valid["backend"] == "vultr" and run_id.startswith("mock-"):
        raise ValueError("mock- run IDs are reserved for recorded inference")
    return valid
