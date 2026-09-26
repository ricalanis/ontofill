"""Human approval markers for PRD, factors, and ontology."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ontofill.contracts import validate_document


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_markdown(path: Path, body: str, generated_by: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    front_matter = yaml.safe_dump({"generated_by": generated_by}, sort_keys=False)
    path.write_text(f"---\n{front_matter}---\n{body}", encoding="utf-8")


def require_approval(
    directory: Path,
    *,
    phase: int,
    checkpoint: str,
    artifact_paths: list[str],
    generated_by: dict[str, str],
) -> bool:
    """Return False and leave a review file until a valid APPROVED marker exists."""
    directory.mkdir(parents=True, exist_ok=True)
    approved = directory / "APPROVED"
    if approved.exists() and generated_by["backend"] == "vultr":
        document = load_json(approved)
        validate_document("approved", document)
        if document.get("checkpoint", checkpoint) != checkpoint:
            raise ValueError(f"wrong checkpoint in {approved}")
        return True
    metadata = {
        "phase": phase,
        "checkpoint": checkpoint,
        "requested_at": datetime.now(UTC).isoformat(),
        "reason": f"Human review required for {checkpoint} before continuing",
        "artifact_paths": artifact_paths,
        "generated_by": generated_by,
    }
    validate_document("approval-pending", metadata)
    pending = directory / "APPROVAL_PENDING.md"
    links = "\n".join(f"- `{path}`" for path in artifact_paths)
    pending.write_text(
        "---\n"
        + yaml.safe_dump(metadata, sort_keys=False)
        + "---\n"
        + f"# Approval pending: {checkpoint}\n\n"
        + f"Review these artifacts:\n\n{links}\n\n"
        + "To approve, create `APPROVED` next to this file with JSON content "
        + f'like `{{"approver":"name","date":"YYYY-MM-DD","checkpoint":"{checkpoint}"}}`.\n',
        encoding="utf-8",
    )
    return False
