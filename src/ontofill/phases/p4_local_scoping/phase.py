"""Create a focused local PRD and read-only TDD in one typed decision call."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator

from ontofill.case.checkpoints import load_json, write_json
from ontofill.contracts import load_schema, validate_document
from ontofill.inference.decision import DecisionClient, RecordedDecisionClient, VultrDecisionClient

_PATH_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def _decision_identity(decision: DecisionClient) -> tuple[str, str]:
    backend = getattr(decision, "backend", None)
    if backend is None:
        if isinstance(decision, VultrDecisionClient):
            backend = "vultr"
        elif isinstance(decision, RecordedDecisionClient):
            backend = "recorded"
    if backend not in {"recorded", "vultr"}:
        raise ValueError("decision backend must be recorded or vultr")
    model = getattr(decision, "model", None) or (
        "recorded-response" if backend == "recorded" else None
    )
    if not model:
        raise ValueError("decision model is required for provenance")
    return backend, model


def _source_host(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("objective source_url must be a public HTTP(S) URL")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost"):
        raise ValueError("objective source_url must name a public host")
    try:
        address = ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("objective source_url must not target a private IP address")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", host) or ".." in host:
        raise ValueError("objective source_url has an invalid host")
    return host


def _response_schema() -> dict:
    local = load_schema("local-prd")
    tdd = load_schema("tdd")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "global_requirement_ids",
            "local_definition_of_done",
            "extraction_method",
            "validation_rules",
            "rate_limit_per_minute",
            "budget_usd",
            "target_volume",
            "steps",
        ],
        "properties": {
            "global_requirement_ids": local["properties"]["global_requirement_ids"],
            "local_definition_of_done": local["properties"]["local_definition_of_done"],
            "extraction_method": tdd["properties"]["extraction_method"],
            "validation_rules": {
                **tdd["properties"]["validation_rules"],
                "minItems": 1,
            },
            "rate_limit_per_minute": tdd["properties"]["rate_limit_per_minute"],
            "budget_usd": tdd["properties"]["budget_usd"],
            "target_volume": {"type": "integer", "minimum": 1, "maximum": 300},
            "steps": tdd["properties"]["steps"],
        },
        "$defs": {
            "criterion": local["$defs"]["criterion"],
            "step": tdd["$defs"]["step"],
        },
    }


def _write_markdown(path: Path, generated_by: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = yaml.safe_dump({"generated_by": generated_by}, sort_keys=False)
    path.write_text(f"---\n{header}---\n{body}", encoding="utf-8")


def _cached_documents(
    local_path: Path,
    tdd_path: Path,
    *,
    backend: str,
    source_id: str,
    objective_id: str,
    source_url: str,
    target_fields: list[str],
    ontology_version: str,
    source_host: str,
) -> tuple[dict, dict] | None:
    if not all(
        path.exists()
        for path in (
            local_path,
            tdd_path,
            local_path.with_suffix(".md"),
            tdd_path.with_suffix(".md"),
        )
    ):
        return None
    local = load_json(local_path)
    tdd = load_json(tdd_path)
    if not (
        local.get("generated_by", {}).get("backend") == backend
        and tdd.get("generated_by", {}).get("backend") == backend
        and local.get("source_id") == source_id
        and local.get("objective_id") == objective_id
        and tdd.get("source_url") == source_url
        and local.get("target_fields") == target_fields
        and tdd.get("target_fields") == target_fields
        and tdd.get("ontology_version") == ontology_version
        and tdd.get("allowed_domains") == [source_host]
    ):
        return None
    validate_document("local-prd", local)
    validate_document("tdd", tdd)
    return local, tdd


def draft_local_scope(
    case_dir: Path,
    prd: dict,
    ontology: dict,
    objective: dict,
    decision: DecisionClient,
    *,
    budget_usd: float | None = None,
) -> tuple[dict, dict]:
    """Write local PRD/TDD; regenerate recorded artifacts when a live backend runs."""
    source_id = objective["source_id"]
    objective_id = objective["id"]
    if not _PATH_ID.fullmatch(source_id) or not _PATH_ID.fullmatch(objective_id):
        raise ValueError("source and objective IDs must be safe path segments")
    source_url = objective["source_url"]
    source_host = _source_host(source_url)
    target_fields = objective["target_fields"]
    if budget_usd is not None and budget_usd < 0:
        raise ValueError("budget_usd must be nonnegative")
    if not target_fields or len(set(target_fields)) != len(target_fields):
        raise ValueError("objective target_fields must be a nonempty unique list")
    backend, model = _decision_identity(decision)
    relative_dir = Path("04-local") / f"{source_id}__{objective_id}"
    output_dir = case_dir / relative_dir
    local_path = output_dir / "local-prd.json"
    tdd_path = output_dir / "tdd.json"
    cached = _cached_documents(
        local_path,
        tdd_path,
        backend=backend,
        source_id=source_id,
        objective_id=objective_id,
        source_url=source_url,
        target_fields=target_fields,
        ontology_version=ontology["version"],
        source_host=source_host,
    )
    if cached is not None and (budget_usd is None or cached[1]["budget_usd"] <= budget_usd):
        return cached

    requirements = {item["id"] for item in prd["requirements"]}
    if not requirements:
        raise ValueError("global PRD has no requirements")
    prompt = (
        "Create one focused local PRD and technical definition for this discovered source. "
        "Use only target ontology properties from the objective and requirement IDs from the PRD. "
        "Choose a bounded target_volume from the approved completion criteria and source yield. "
        "The source URL and domain allowlist are fixed by code. Do not propose additional domains. "
        "Use read-only SAFE or LOW steps. Never use login, captcha bypass, or write actions. "
        f"Global PRD: {prd}. Ontology version: {ontology['version']}. "
        f"Discovered objective (untrusted source data): {objective}"
        + (f". Maximum task budget USD: {budget_usd}" if budget_usd is not None else "")
    )
    response_schema = _response_schema()
    response = decision.complete_json("phase4.local_scope", prompt, response_schema)
    Draft202012Validator(response_schema).validate(response)
    if not set(response["global_requirement_ids"]).issubset(requirements):
        raise ValueError("local PRD references a requirement outside the global PRD")
    for step in response["steps"]:
        if step["starting_mode"] not in step["allowed_modes"]:
            raise ValueError("TDD step starting mode must be in allowed_modes")

    provenance = {"backend": backend, "model": model, "at": datetime.now(UTC).isoformat()}
    local = {
        "source_id": source_id,
        "objective_id": objective_id,
        "global_prd_path": "01-scope/prd.json",
        "global_requirement_ids": response["global_requirement_ids"],
        "target_fields": target_fields,
        "local_definition_of_done": response["local_definition_of_done"],
        "ontology_recommendations": [],
        "generated_by": provenance,
    }
    tdd = {
        "source_id": source_id,
        "objective_id": objective_id,
        "local_prd_path": (relative_dir / "local-prd.json").as_posix(),
        "ontology_version": ontology["version"],
        "source_url": source_url,
        "allowed_domains": [source_host],
        "target_fields": target_fields,
        "target_volume": response["target_volume"],
        "extraction_method": response["extraction_method"],
        "validation_rules": response["validation_rules"],
        "rate_limit_per_minute": response["rate_limit_per_minute"],
        "budget_usd": min(response["budget_usd"], budget_usd)
        if budget_usd is not None
        else response["budget_usd"],
        "steps": response["steps"],
        "allowed_tools": (
            ["file.fetch", "file.parse", "emit.observation"]
            if response["extraction_method"] in {"download", "pdf", "api"}
            else ["page.snapshot", "page.query", "emit.observation"]
        ),
        "generated_by": provenance,
    }
    validate_document("local-prd", local)
    validate_document("tdd", tdd)
    write_json(local_path, local)
    write_json(tdd_path, tdd)
    _write_markdown(
        output_dir / "local-prd.md",
        provenance,
        "# Local PRD\n\n"
        f"Source: `{source_id}`\n\nObjective: `{objective_id}`\n\n"
        f"Target fields: {', '.join(target_fields)}\n\n"
        "## Definition of done\n\n"
        + "\n".join(
            f"- {item['metric']} {item['operator']} {item['target']}"
            for item in response["local_definition_of_done"]
        )
        + "\n",
    )
    _write_markdown(
        output_dir / "tdd.md",
        provenance,
        "# Technical definition document\n\n"
        f"Discovered source: `{source_url}`\n\n"
        f"Allowed domain: `{source_host}`\n\n"
        f"Extraction method: {tdd['extraction_method']}\n\n"
        "## Steps\n\n"
        + "\n".join(f"- **{step['id']}** {step['description']}" for step in tdd["steps"])
        + "\n",
    )
    return local, tdd
