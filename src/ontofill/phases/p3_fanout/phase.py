"""Gap-driven source discovery with captured-result selection and authority review."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from ontofill_scrape import SearchClient, source_discover

from ontofill.case.checkpoints import load_json, require_approval, write_json
from ontofill.contracts import validate_document
from ontofill.inference import DecisionClient, generated_by
from ontofill.phases.p3_fanout.authority import authority_result, source_class, source_fingerprint


def _dod_properties(ontology: dict) -> tuple[str, ...]:
    selected = tuple(item["id"] for item in ontology["properties"] if item["dod"])
    return selected or tuple(item["id"] for item in ontology["properties"])


def _fingerprint_request(
    brief: str, ontology: dict, prd: dict, gaps: tuple[str, ...], decision, client
) -> str:
    providers = [item.name for item in getattr(client, "providers", [])]
    providers = providers or [getattr(client, "name", type(client).__name__)]
    data = [brief, ontology, prd.get("authority_policy"), sorted(gaps), providers, decision.backend]
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _queries(brief: str, gap_labels: tuple[str, ...], decision: DecisionClient) -> tuple[str, ...]:
    summary = " ".join(
        line.strip()
        for line in brief.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )[:140]
    if decision.backend == "recorded":
        return (" ".join((summary, *gap_labels)),)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["queries"],
        "properties": {
            "queries": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "items": {"type": "string", "minLength": 5, "maxLength": 240},
            }
        },
    }
    result = decision.complete_json(
        "phase3.plan_search",
        "Propose public web search queries from these ontology gaps. Use the brief's geography. "
        "Do not propose or invent any source URL. "
        f"Brief: {summary}. Gaps: {list(gap_labels)}.",
        schema,
    )
    Draft202012Validator(schema).validate(result)
    return tuple(result["queries"])


def _metadata(client, url: str) -> dict:
    found = getattr(client, "result_metadata", {}).get(url)
    if found:
        return found
    return {
        "provider": getattr(client, "name", "injected_search"),
        "capture_key": getattr(client, "capture_key", None),
        "trusted_origin": getattr(client, "trusted_origin", None),
    }


def _candidate(
    candidate, metadata: dict, gaps: tuple[str, ...], ontology: dict, prd: dict
) -> tuple[dict, dict, bool]:
    provider = metadata["provider"]
    source_type = source_class(candidate.title, candidate.snippet, ontology["source_classes"])
    policy = prd["authority_policy"]
    trusted, reason = authority_result(candidate.url, policy=policy)
    fingerprint = source_fingerprint(
        url=candidate.url,
        title=candidate.title,
        snippet=candidate.snippet,
        provider=provider,
        capture_key=metadata.get("capture_key"),
        authority_policy=policy,
    )
    source_id = "source-" + hashlib.sha256(candidate.url.encode()).hexdigest()[:12]
    primary = next(item for item in ontology["classes"] if item["id"] == ontology["primary_class"])
    targets = list(
        dict.fromkeys([*gaps, primary["identifier_property"], primary["title_property"]])
    )
    objective_id = (
        "objective-"
        + hashlib.sha256((source_id + ":" + ",".join(targets)).encode()).hexdigest()[:12]
    )
    property_labels = {item["id"]: item["label"].casefold() for item in ontology["properties"]}
    observed = f"{candidate.title} {candidate.snippet}".casefold()
    predicted = [property_id for property_id in gaps if property_labels[property_id] in observed]
    discovered_by = {"provider": provider, "at": datetime.now(UTC).isoformat()}
    objective = {
        "id": objective_id,
        "source_id": source_id,
        "source_url": candidate.url,
        "source_type": source_type,
        "source_fingerprint": fingerprint,
        "discovery_provider": provider,
        "discovered_by": discovered_by,
        "target_fields": targets,
        "priority": 1,
        "expected_contribution": round(len(predicted) / len(gaps), 3),
    }
    manifest = {
        "source_id": source_id,
        "url": candidate.url,
        "title": candidate.title,
        "snippet": candidate.snippet,
        "provider": provider,
        "discovered_by": discovered_by,
        "capture_key": metadata.get("capture_key"),
        "source_type": source_type,
        "authority": "auto" if trusted else "review",
        "authority_reason": reason,
        "fingerprint": fingerprint,
    }
    return objective, manifest, trusted


def _choose(entries: list[tuple], gaps: tuple[str, ...], decision, max_sources: int) -> list[tuple]:
    def score(item: tuple) -> int:
        _, objective, _, trusted = item
        return int(100 * objective["expected_contribution"]) + (8 if trusted else 0)

    ranked = sorted(entries, key=score, reverse=True)
    if decision.backend == "recorded":
        selected, types = [], set()
        for entry in ranked:
            kind = entry[1]["source_type"]
            if kind not in types:
                selected.append(entry)
                types.add(kind)
            if len(selected) >= max_sources:
                break
        return selected
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["indexes", "reason"],
        "properties": {
            "indexes": {
                "type": "array",
                "minItems": 1,
                "maxItems": max_sources,
                "uniqueItems": True,
                "items": {"type": "integer", "minimum": 0, "maximum": len(ranked) - 1},
            },
            "reason": {"type": "string", "minLength": 1},
        },
    }
    listing = [
        {
            "index": i,
            "title": item[0].title,
            "url": item[0].url,
            "snippet": item[0].snippet,
            "source_type": item[1]["source_type"],
            "authority": item[2]["authority"],
        }
        for i, item in enumerate(ranked)
    ]
    result = decision.complete_json(
        "phase3.select_sources",
        "Select relevant captured sources by index only; do not invent URLs. "
        f"Gaps: {gaps}. Candidates: {json.dumps(listing, ensure_ascii=False)}",
        schema,
    )
    Draft202012Validator(schema).validate(result)
    return [ranked[index] for index in result["indexes"]]


def discover_objectives(
    case_dir: Path,
    ontology: dict,
    decision: DecisionClient,
    search_client: SearchClient,
    *,
    gaps: tuple[str, ...] | None = None,
    max_sources: int = 4,
) -> dict:
    """Accumulate objectives across gap rounds and review unrecognized authorities.

    A client that runs the bounded discovery loop (``discover_sources``) owns the
    whole round: lead providers, sandbox confirmation and authority review.
    """
    if callable(getattr(search_client, "discover_sources", None)):
        return search_client.discover_sources(
            case_dir, ontology, decision, gaps=gaps, max_sources=max_sources
        )
    allowed = {item["id"] for item in ontology["properties"]}
    gaps = tuple(dict.fromkeys(gaps or _dod_properties(ontology)))
    if not gaps or any(field not in allowed for field in gaps):
        raise ValueError("discovery gaps must be ontology properties")
    if max_sources not in range(1, 9):
        raise ValueError("max_sources must be between 1 and 8")
    path = case_dir / "03-fanout/objectives.json"
    surface = path.parent / "surface-map/discovery.json"
    brief = (case_dir / "brief.md").read_text(encoding="utf-8")
    prd = load_json(case_dir / "01-scope/prd.json")
    request_key = _fingerprint_request(brief, ontology, prd, gaps, decision, search_client)
    previous = load_json(path) if path.exists() else None
    ledger = load_json(surface) if surface.exists() else {}
    if (
        previous
        and ledger.get("request_fingerprint") == request_key
        and previous.get("generated_by", {}).get("backend") == decision.backend
    ):
        validate_document("objectives", previous)
        return previous

    candidates = {}
    labels = {item["id"]: item["label"] for item in ontology["properties"]}
    queries = _queries(brief, tuple(labels[property_id] for property_id in gaps), decision)
    for query in queries:
        for found in source_discover(brief, search_client, limit=30, query=query):
            candidates.setdefault(found.url, found)
    entries = []
    for found in candidates.values():
        objective, manifest, trusted = _candidate(
            found, _metadata(search_client, found.url), gaps, ontology, prd
        )
        entries.append((found, objective, manifest, trusted))
    if not entries:
        raise ValueError("search captured no usable source candidates")
    selected = _choose(entries, gaps, decision, max_sources)
    provenance = generated_by(decision)
    existing = (
        previous
        if previous and previous.get("generated_by", {}).get("backend") == decision.backend
        else {}
    )
    by_id = {item["id"]: item for item in existing.get("objectives", [])}
    for _, objective, manifest, trusted in selected:
        by_id[objective["id"]] = objective
        manifest["generated_by"] = provenance
        directory = case_dir / "03-fanout/sources" / objective["source_id"]
        write_json(directory / "candidate.json", manifest)
        if not trusted:
            require_approval(
                directory,
                phase=3,
                checkpoint="source",
                artifact_paths=[f"03-fanout/sources/{objective['source_id']}/candidate.json"],
                generated_by=provenance,
                source_fingerprint=objective["source_fingerprint"],
            )
    document = {
        "ontology_version": ontology["version"],
        "prd_path": "01-scope/prd.json",
        "generated_by": provenance,
        "objectives": list(by_id.values()),
    }
    validate_document("objectives", document)
    write_json(path, document)
    (path.parent / "objectives.yaml").write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    rounds = ledger.get("rounds", [])
    rounds.append(
        {
            "request_fingerprint": request_key,
            "queries": list(queries),
            "gaps": list(gaps),
            "candidate_count": len(candidates),
            "selected_source_ids": [item[1]["source_id"] for item in selected],
            "attempts": getattr(search_client, "attempts", []),
            "generated_by": provenance,
        }
    )
    write_json(
        surface, {"request_fingerprint": request_key, "rounds": rounds, "generated_by": provenance}
    )
    return document


def discover_objective(
    case_dir: Path, ontology: dict, decision: DecisionClient, search_client: SearchClient
) -> dict:
    """Compatibility surface for the C3 one-objective caller."""
    return discover_objectives(case_dir, ontology, decision, search_client, max_sources=1)
