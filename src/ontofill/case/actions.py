"""Persist a human decision before a high-risk browser action executes."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ontofill.case.checkpoints import (
    ApprovalArtifactMismatch,
    load_json,
    load_verified_approval,
    write_json,
)
from ontofill.contracts import validate_document

_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def action_gate(
    case_dir: Path,
    request_id: str,
    *,
    action: str,
    risk_tier: str,
    job_id: str,
    screenshot_key: str,
    generated_by: dict,
) -> dict:
    """Return a §12 gate result; HIGH actions require an action-specific APPROVED file."""
    if not _REQUEST_ID.fullmatch(request_id):
        raise ValueError("action request ID must be a safe path segment")
    if risk_tier not in {"SAFE", "LOW", "HIGH"}:
        raise ValueError("action risk tier must be SAFE, LOW or HIGH")
    if not action or not job_id:
        raise ValueError("action and job ID are required")
    if risk_tier != "HIGH":
        return {
            "action": action,
            "risk_tier": risk_tier,
            "decided_by": "code",
            "outcome": "allowed",
            "approval_path": None,
        }

    relative = Path("05-actions") / request_id
    directory = case_dir / relative
    request_path = directory / "request.json"
    approved_path = directory / "APPROVED"
    request = {
        "intended_action": action,
        "risk_tier": risk_tier,
        "job_id": job_id,
        "screenshot_key": screenshot_key,
        "generated_by": generated_by,
    }
    if request_path.exists():
        previous = load_json(request_path)
        bound = ("intended_action", "risk_tier", "job_id", "screenshot_key")
        if (
            any(previous.get(key) != request[key] for key in bound)
            or previous.get("generated_by", {}).get("backend") != generated_by["backend"]
        ):
            raise ValueError("action request ID is already bound to different content")
        request = previous
    else:
        if approved_path.exists():
            raise ApprovalArtifactMismatch("action")
        write_json(request_path, request)
    if approved_path.exists():
        approved = load_verified_approval(
            approved_path, case_dir, [(relative / "request.json").as_posix()], "action"
        )
        if generated_by["backend"] != "vultr":
            approved = None
    else:
        approved = None
    if approved is not None:
        return {
            "action": action,
            "risk_tier": risk_tier,
            "decided_by": "code",
            "outcome": "allowed" if approved["decision"] == "approve" else "denied",
            "approval_path": (relative / "APPROVED").as_posix(),
        }

    metadata = {
        "phase": 5,
        "checkpoint": "action",
        "requested_at": datetime.now(UTC).isoformat(),
        "reason": "Human approval required before this high-risk action",
        "artifact_paths": [(relative / "request.json").as_posix()],
        "generated_by": request["generated_by"],
        "screenshot_key": screenshot_key,
        "intended_action": action,
        "risk_tier": risk_tier,
        "job_id": job_id,
    }
    validate_document("approval-pending", metadata)
    pending_path = directory / "APPROVAL_PENDING.md"
    pending_path.write_text(
        "---\n"
        + yaml.safe_dump(metadata, sort_keys=False)
        + "---\n"
        + "# Action approval pending\n\n"
        + f"Action: `{action}`\n\n"
        + "Create `APPROVED` here with JSON `decision: approve` or `decision: deny` "
        + "and a reason for denial.\n",
        encoding="utf-8",
    )
    return {
        "action": action,
        "risk_tier": risk_tier,
        "decided_by": "code",
        "outcome": "pending_approval",
        "approval_path": (relative / "APPROVAL_PENDING.md").as_posix(),
    }
