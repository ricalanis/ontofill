"""Approve-before-submit (CONTRACT §12 + v0.8.1): a HIGH action writes
case/05-actions/<request_id>/APPROVAL_PENDING.md and blocks until the approver's APPROVED file appears.

The approver answers with one APPROVED JSON: {approver, date, checkpoint: "action", decision: approve|deny, reason}.
Fail-safe: a timeout, an unreadable APPROVED or any decision other than "approve" is a deny. This module never
writes APPROVED; only a person does.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

from shared.steps import now


@dataclass
class ApprovalRequest:
    request_id: str
    dir: Path
    rel_dir: str  # relative to the case dir, e.g. 05-actions/act-…

    @property
    def pending_path(self) -> Path:
        return self.dir / "APPROVAL_PENDING.md"

    @property
    def approved_path(self) -> Path:
        return self.dir / "APPROVED"


@dataclass
class ApprovalDecision:
    decision: str  # approve | deny
    reason: str
    approver: str | None = None
    timed_out: bool = False

    @property
    def approved(self) -> bool:
        return self.decision == "approve"

    def as_dict(self) -> dict:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "approver": self.approver,
            "timed_out": self.timed_out,
        }


def request(
    case_dir: Path,
    *,
    intended_action: str,
    risk_tier: str,
    screenshot_key: str,
    job_id: str,
    reason: str,
    artifact_paths: list[str],
    generated_by: dict,
    detail: dict | None = None,
) -> ApprovalRequest:
    request_id = f"act-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    rel_dir = f"05-actions/{request_id}"
    req = ApprovalRequest(request_id, Path(case_dir) / rel_dir, rel_dir)
    req.dir.mkdir(parents=True, exist_ok=False)
    meta = {
        "phase": 5,
        "checkpoint": "action",
        "requested_at": now(),
        "reason": reason,
        "artifact_paths": artifact_paths or [f"{rel_dir}/APPROVAL_PENDING.md"],
        "intended_action": intended_action,
        "risk_tier": risk_tier,
        "job_id": job_id,
        "screenshot_key": screenshot_key,
        "generated_by": generated_by,
    }
    body = [
        f"# Approval pending: action\n\nThe browser agent wants to **{intended_action}**.\n",
        f"Risk tier **{risk_tier}**: {reason}.\n",
        (
            "Answer with an `APPROVED` file in this directory: "
            '`{"approver", "date", "checkpoint": "action", "decision": "approve"|"deny", "reason"}`. '
            "No answer before the timeout counts as a deny.\n"
        ),
    ]
    if detail:
        body.append("\n```json\n" + json.dumps(detail, indent=2, ensure_ascii=False) + "\n```\n")
    req.pending_path.write_text(
        f"---\n{yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)}---\n" + "\n".join(body)
    )
    return req


def read_decision(req: ApprovalRequest) -> ApprovalDecision | None:
    if not req.approved_path.exists():
        return None
    try:
        data = json.loads(req.approved_path.read_text())
    except (OSError, ValueError):
        return ApprovalDecision("deny", "APPROVED file is not valid JSON (fail-safe deny)")
    if not isinstance(data, dict):
        return ApprovalDecision("deny", "APPROVED file is not an object (fail-safe deny)")
    decision = data.get("decision")
    if decision == "approve" and data.get("checkpoint", "action") == "action":
        return ApprovalDecision("approve", str(data.get("reason") or "approved"), data.get("approver"))
    return ApprovalDecision(
        "deny", str(data.get("reason") or f"decision {decision!r} is not an approval"), data.get("approver")
    )


def wait(req: ApprovalRequest, timeout_s: float, poll_s: float = 0.5, cancelled=None) -> ApprovalDecision:
    deadline = time.monotonic() + timeout_s
    while True:
        found = read_decision(req)
        if found:
            return found
        if time.monotonic() >= deadline or (cancelled and cancelled()):
            return ApprovalDecision(
                "deny", f"no answer within {timeout_s:g} s (fail-safe deny)", timed_out=True
            )
        time.sleep(max(0.01, min(poll_s, deadline - time.monotonic())))
