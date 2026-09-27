"""Human approval markers for PRD, factors, ontology, and source authority."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml
from jsonschema import ValidationError

from ontofill.contracts import validate_document


class ApprovalArtifactMismatch(ValueError):
    """The review is bound to different artifact bytes."""

    def __init__(self, checkpoint: str | None = None) -> None:
        self.checkpoint = checkpoint
        super().__init__("approval is for a different artifact version")


def verify_approval_artifacts(case_dir: Path, document: dict, expected_paths: list[str]) -> None:
    """Check the exact case-relative files the reviewer saw, without writing anything."""
    digests = document.get("artifact_sha256")
    if not isinstance(digests, dict) or set(digests) != set(expected_paths):
        raise ApprovalArtifactMismatch
    root = case_dir.resolve()
    for relative in expected_paths:
        path = Path(relative)
        if path.is_absolute() or not path.parts or any(part in {".", ".."} for part in path.parts):
            raise ApprovalArtifactMismatch
        candidate = case_dir / path
        try:
            if not candidate.resolve().is_relative_to(root) or not candidate.is_file():
                raise ApprovalArtifactMismatch
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError as exc:
            raise ApprovalArtifactMismatch from exc
        if digest != digests[relative]:
            raise ApprovalArtifactMismatch


def load_verified_approval(
    marker: Path, case_dir: Path, expected_paths: list[str], checkpoint: str
) -> dict:
    """Validate a marker and bind protected checkpoint decisions to current bytes."""
    document = load_json(marker)
    try:
        validate_document("approved", document)
    except ValidationError as exc:
        if checkpoint in {"prd", "factors", "ontology", "action"}:
            raise ApprovalArtifactMismatch(checkpoint) from exc
        raise
    if document.get("checkpoint") != checkpoint:
        raise ValueError(f"wrong checkpoint in {marker}")
    if checkpoint in {"prd", "factors", "ontology", "action"}:
        try:
            verify_approval_artifacts(case_dir, document, expected_paths)
        except ApprovalArtifactMismatch as exc:
            raise ApprovalArtifactMismatch(checkpoint) from exc
    return document


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def checkpoint_revisions(
    directory: Path,
    checkpoint: str,
    artifact_names: list[str],
    *,
    archive_denial: bool = True,
    case_dir: Path | None = None,
) -> list[dict]:
    """Return denied revisions, optionally deferring the active denial's archive."""
    approved = directory / "APPROVED"
    archive_root = directory / "revisions"
    existing = (
        sorted((p for p in archive_root.iterdir() if p.is_dir()), key=lambda p: int(p.name))
        if archive_root.exists()
        else []
    )
    active_denial = None
    if approved.exists():
        root = case_dir or (directory.parents[1] if checkpoint == "factors" else directory.parent)
        relative = (directory / artifact_names[0]).relative_to(root).as_posix()
        marker = load_verified_approval(approved, root, [relative], checkpoint)
        if marker.get("decision", "approve") == "deny":
            if not (directory / artifact_names[0]).exists():
                raise ValueError(f"cannot deny missing {checkpoint} artifact")
            active_denial = marker
            if archive_denial:
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
        # Archived denials are audit history, not decisions to act on. Older
        # revisions predate digest-bound approval files and remain readable.
        if marker.get("checkpoint", checkpoint) != checkpoint or marker.get("decision") != "deny":
            raise ValueError(f"invalid {checkpoint} denial in {archive}")
        if any(
            not isinstance(marker.get(key), str) or not marker[key]
            for key in ("reason", "approver", "date")
        ):
            raise ValueError(f"incomplete {checkpoint} denial in {archive}")
        revisions.append(
            {"n": index, **{key: marker[key] for key in ("decision", "reason", "approver", "date")}}
        )
    if active_denial is not None and not archive_denial:
        revisions.append(
            {
                "n": len(revisions) + 1,
                **{key: active_denial[key] for key in ("decision", "reason", "approver", "date")},
            }
        )
    return revisions


def write_markdown(path: Path, body: str, generated_by: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    front_matter = yaml.safe_dump({"generated_by": generated_by}, sort_keys=False)
    path.write_text(f"---\n{front_matter}---\n{body}", encoding="utf-8")


def write_prd_budget_pending(directory: Path, generated_by: dict[str, str]) -> None:
    """Pause cleanly when the loop budget is exhausted before a draft exists."""
    directory.mkdir(parents=True, exist_ok=True)
    metadata = {
        "phase": 1,
        "checkpoint": "prd",
        "requested_at": datetime.now(UTC).isoformat(),
        "reason": "PRD loop budget exhausted before a draft was produced",
        "artifact_paths": ["brief.md"],
        "generated_by": generated_by,
    }
    validate_document("approval-pending", metadata)
    (directory / "APPROVAL_PENDING.md").write_text(
        "---\n"
        + yaml.safe_dump(metadata, sort_keys=False)
        + "---\n# PRD draft unavailable\n\n"
        + "The PRD budget ended before the model produced a draft. Increase the run budget "
        + "and rerun this phase; there is no PRD to approve yet.\n",
        encoding="utf-8",
    )


def require_approval(
    directory: Path,
    *,
    phase: int,
    checkpoint: str,
    artifact_paths: list[str],
    generated_by: dict[str, str],
    source_fingerprint: str | None = None,
    case_dir: Path | None = None,
) -> bool:
    """Return False and leave a review file until a valid APPROVED marker exists."""
    approved = directory / "APPROVED"
    if approved.exists():
        root = case_dir or (directory.parents[1] if checkpoint == "factors" else directory.parent)
        document = load_verified_approval(approved, root, artifact_paths, checkpoint)
        if (
            generated_by["backend"] == "vultr"
            and document.get("decision", "approve") != "deny"
            and (checkpoint != "source" or document.get("source_fingerprint") == source_fingerprint)
        ):
            return True
    directory.mkdir(parents=True, exist_ok=True)
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
                if document.get("open_issues"):
                    review_details += (
                        "\n## Open issues for human review\n\n"
                        + "\n".join(f"- {issue}" for issue in document["open_issues"])
                        + "\n"
                    )
                review_details += (
                    "\n## Authority policy\n\n"
                    + "\n".join(
                        f"- {item['kind']} [{item.get('tier', 'primary')}]: {item['rationale']}"
                        for item in document["authority_policy"]["trusted_publishers"]
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
