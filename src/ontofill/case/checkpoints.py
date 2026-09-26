"""Human approval markers for PRD, factors, ontology, and source authority."""

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


def checkpoint_revisions(directory: Path, checkpoint: str, artifact_names: list[str]) -> list[dict]:
    """Archive a denied draft and return the complete, durable human revision history."""
    approved = directory / "APPROVED"
    archive_root = directory / "revisions"
    existing = (
        sorted((p for p in archive_root.iterdir() if p.is_dir()), key=lambda p: int(p.name))
        if archive_root.exists()
        else []
    )
    if approved.exists():
        marker = load_json(approved)
        validate_document("approved", marker)
        if marker.get("checkpoint", checkpoint) != checkpoint:
            raise ValueError(f"wrong checkpoint in {approved}")
        if marker.get("decision", "approve") == "deny":
            if not (directory / artifact_names[0]).exists():
                raise ValueError(f"cannot deny missing {checkpoint} artifact")
            target = archive_root / str(len(existing) + 1)
            target.mkdir(parents=True, exist_ok=False)
            for name in [*artifact_names, "APPROVAL_PENDING.md", "APPROVED"]:
                source = directory / name
                if source.exists():
                    source.rename(target / name)
            existing.append(target)
    revisions = []
    for index, archive in enumerate(existing, start=1):
        marker = load_json(archive / "APPROVED")
        validate_document("approved", marker)
        if marker.get("checkpoint", checkpoint) != checkpoint or marker.get("decision") != "deny":
            raise ValueError(f"invalid {checkpoint} denial in {archive}")
        revisions.append(
            {"n": index, **{key: marker[key] for key in ("decision", "reason", "approver", "date")}}
        )
    return revisions


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
    source_fingerprint: str | None = None,
) -> bool:
    """Return False and leave a review file until a valid APPROVED marker exists."""
    directory.mkdir(parents=True, exist_ok=True)
    approved = directory / "APPROVED"
    if approved.exists() and generated_by["backend"] == "vultr":
        document = load_json(approved)
        validate_document("approved", document)
        if document.get("checkpoint", checkpoint) != checkpoint:
            raise ValueError(f"wrong checkpoint in {approved}")
        if document.get("decision", "approve") != "deny" and (
            checkpoint != "source" or document.get("source_fingerprint") == source_fingerprint
        ):
            return True
    metadata = {
        "phase": phase,
        "checkpoint": checkpoint,
        "requested_at": datetime.now(UTC).isoformat(),
        "reason": f"Human review required for {checkpoint} before continuing",
        "artifact_paths": artifact_paths,
        "generated_by": generated_by,
    }
    if checkpoint == "source":
        if source_fingerprint is None:
            raise ValueError("source checkpoint needs a candidate fingerprint")
        metadata["source_fingerprint"] = source_fingerprint
    validate_document("approval-pending", metadata)
    pending = directory / "APPROVAL_PENDING.md"
    links = "\n".join(f"- `{path}`" for path in artifact_paths)
    review_details = ""
    if checkpoint in {"prd", "factors", "ontology"}:
        artifact = directory / Path(artifact_paths[0]).with_suffix(".json").name
        if artifact.exists():
            document = load_json(artifact)
            revisions = document.get("revisions", [])
            if revisions:
                review_details += (
                    "\n## Human revisions\n\n"
                    + "\n".join(
                        f"- {item['n']}. {item['reason']} ({item['approver']}, {item['date']})"
                        for item in revisions
                    )
                    + "\n"
                )
            if checkpoint == "prd":
                review_details += (
                    "\n## Definition of done basis\n\n"
                    + "\n".join(
                        f"- {item['metric']} {item['operator']} {item['target']}: **{item['basis']}**"
                        + (
                            f" — {item['rationale']}; {item['feasibility']}"
                            if item["basis"] == "proposed"
                            else ""
                        )
                        for item in document["definition_of_done"]
                    )
                    + "\n"
                )
    pending.write_text(
        "---\n"
        + yaml.safe_dump(metadata, sort_keys=False)
        + "---\n"
        + f"# Approval pending: {checkpoint}\n\n"
        + f"Review these artifacts:\n\n{links}\n\n"
        + "To approve, create `APPROVED` next to this file with JSON content "
        + f'like `{{"approver":"name","date":"YYYY-MM-DD","checkpoint":"{checkpoint}"'
        + (f',"source_fingerprint":"{source_fingerprint}"' if checkpoint == "source" else "")
        + "}`. To deny, set `decision` to `deny` and include a non-empty `reason`.\n"
        + review_details,
        encoding="utf-8",
    )
    return False
