"""R35: P3 confirms retrieval capability through captured access paths."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ontofill.lake import FileLake
from ontofill.phase_loop import LoopBudget
from ontofill.phases.p3_fanout.discovery_loop import DiscoveryLoop, NoConfirmedSources
from ontofill.phases.p3_fanout.leads import Lead, LeadContext, LeadProvider
from ontofill.sandbox import CaptureBlocked
from tests.r17_helpers import SyntheticParseExecutor

PROVENANCE = {
    "backend": "vultr",
    "model": "synthetic-capability-test",
    "at": "2026-09-27T12:00:00Z",
}
POLICY = {
    "jurisdiction": "Synthetic Region",
    "trusted_publishers": [
        {
            "kind": "synthetic public record authority",
            "tier": "primary",
            "domains": ["registry.synthetic.test"],
            "rationale": "Synthetic policy fixture",
        }
    ],
    "unknown_source_action": "review",
}
ONTOLOGY = {
    "version": "synthetic-1",
    "primary_class": "record",
    "classes": [
        {
            "id": "record",
            "label": "record",
            "label_plural": "records",
            "identifier_property": "record_identifier",
            "title_property": "record_title",
        }
    ],
    "properties": [
        {
            "id": "record_identifier",
            "label": "Record identifier",
            "description": "Stable identifier assigned to each record",
            "domain": "record",
            "datatype": "string",
            "dod": True,
        },
        {
            "id": "establishment_date",
            "label": "Establishment date",
            "description": "Date on which the record subject was established",
            "domain": "record",
            "datatype": "date",
            "dod": True,
        },
        {
            "id": "record_title",
            "label": "Record title",
            "description": "Short title shown for the record",
            "domain": "record",
            "datatype": "string",
            "dod": False,
        },
    ],
    "source_classes": [{"id": "public_source", "label": "public record source"}],
}


def _case_dir(path: Path) -> None:
    (path / "01-scope").mkdir(parents=True)
    (path / "02-ontology").mkdir()
    (path / "brief.md").write_text(
        "# Synthetic objective\n\nFind public records and their available fields.\n",
        encoding="utf-8",
    )
    (path / "01-scope/prd.json").write_text(
        json.dumps({"authority_policy": POLICY}), encoding="utf-8"
    )


class OneLead(LeadProvider):
    name = "synthetic_search"

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    def leads(self, context: LeadContext) -> list[Lead]:
        properties = tuple(query.property_id for query in context.queries)
        lead = Lead(
            self.url,
            "Synthetic record portal",
            "Public record lookup source",
            self.name,
            "synthetic record lookup",
            properties,
        )
        self._attempt("synthetic record lookup", "ok", 1)
        return [lead]


class FakeCapture:
    def __init__(self, lake: FileLake, artifacts: dict[str, str | bytes]) -> None:
        self.lake = lake
        self.artifacts = artifacts

    def __call__(self, url: str, **kwargs) -> dict:
        trace = {
            "step_id": f"step:{uuid.uuid4().hex}",
            "run_id": kwargs["run_id"],
            "phase": 3,
            "source_id": kwargs["source_id"],
            "objective_id": None,
            "tdd_path": kwargs["tdd_path"],
            "mode": "S1",
            "observed": {"url": url},
            "requested": {"url": url},
            "executed": {},
            "evaluated": {"status": "captured"},
            "parent_step_id": None,
            "value_ids": [],
            "ts": datetime.now(UTC).isoformat(),
            "generated_by": kwargs["generated_by"],
        }
        artifact = self.artifacts.get(url)
        if artifact is None:
            raise CaptureBlocked("synthetic URL has no capture", [trace])
        if isinstance(artifact, str):
            key = self.lake.put_bytes(artifact.encode("utf-8"), {"url": url})
            trace["executed"] = {"bronze_key": key}
            return {
                "url": url,
                "redirect_chain": [url],
                "status": 200,
                "html_key": key,
                "trace": [trace],
            }
        key = self.lake.put_bytes(
            artifact,
            {"url": url, "content_type": "application/octet-stream"},
        )
        trace["executed"] = {"document_key": key, "artifact_kind": "download"}
        return {
            "url": url,
            "redirect_chain": [url],
            "status": 200,
            "document_key": key,
            "document_content_type": "application/octet-stream",
            "document_size_bytes": len(artifact),
            "trace": [trace],
        }


def _unwrap(value: str) -> str:
    return value.replace("<page_content>", "").replace("</page_content>", "")


class CapabilityCritic:
    """Separate-family-shaped critic double: it classifies access routes, not values."""

    backend = "vultr"
    model = "synthetic-critic-family"
    last_model = "synthetic-critic-family"

    def __init__(self, *, accept: bool = True) -> None:
        self.accept = accept
        self.call_log: list[dict] = []
        self.purposes: list[str] = []

    def complete_json(self, purpose: str, prompt: str, schema: dict) -> dict:
        self.purposes.append(purpose)
        self.call_log.append(
            {
                "purpose": purpose,
                "usage": {
                    "model": self.model,
                    "backend": self.backend,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "est_usd": 0,
                },
            }
        )
        if purpose == "phase3.plan_queries":
            property_ids = schema["properties"]["queries"]["items"]["properties"]["property_id"][
                "enum"
            ]
            return {
                "queries": [
                    {"property_id": property_id, "query": f"synthetic query {property_id}"}
                    for property_id in property_ids
                ]
            }
        assert purpose == "critic.phase3.capability"
        context = json.loads(prompt.split("Review context: ", 1)[1])
        verdicts = []
        for page in context["pages"]:
            evidence = page["captured_access_evidence"]
            forms = evidence.get("forms", [])
            document = evidence.get("document")
            headers = (
                [
                    _unwrap(value)
                    for value in document.get("headers", [])
                    if isinstance(document, dict) and isinstance(value, str)
                ]
                if isinstance(document, dict)
                else []
            )
            for property_id, prop in page["target_properties"].items():
                words = set(
                    re.findall(
                        r"[a-z0-9]{4,}",
                        f"{prop.get('label', '')} {prop.get('description', '')}".casefold(),
                    )
                )
                header = next(
                    (
                        item
                        for item in headers
                        if words & set(re.findall(r"[a-z0-9]{4,}", item.casefold()))
                    ),
                    None,
                )
                form = forms[0] if forms and isinstance(forms[0], dict) else None
                if not self.accept:
                    path = None
                    provides = False
                    reason = "No record retrieval route is present on this generic page."
                elif header:
                    path = {
                        "kind": "dataset",
                        "access_path_quote": header,
                        "property_quote": header,
                    }
                    provides = True
                    reason = "Parsed dataset fields include the ontology property."
                elif form:
                    label = _unwrap(form.get("submit_labels", [""])[0])
                    path = {
                        "kind": "search_form",
                        "access_path_quote": label,
                        "form_index": 0,
                    }
                    provides = True
                    reason = "The public registry form reaches individual records."
                else:
                    path = None
                    provides = False
                    reason = "No record retrieval route is present on this generic page."
                verdicts.append(
                    {
                        "index": page["index"],
                        "property_id": property_id,
                        "provides": provides,
                        "authority_verdict": (
                            "authoritative" if page["publisher_matches"] else "unknown"
                        ),
                        "access_path": path,
                        "reason": reason,
                    }
                )
        return {"verdicts": verdicts}


class BlogMetadataCritic(CapabilityCritic):
    """Try to relabel an ordinary captured paragraph as metadata evidence."""

    def complete_json(self, purpose: str, prompt: str, schema: dict) -> dict:
        result = super().complete_json(purpose, prompt, schema)
        if purpose == "critic.phase3.capability":
            for verdict in result["verdicts"]:
                verdict.update(
                    provides=True,
                    access_path={
                        "kind": "metadata",
                        "access_path_quote": "General commentary about public information.",
                    },
                    reason="The page paragraph describes metadata.",
                )
        return result


def _loop(tmp_path: Path, page: str | bytes, url: str):
    _case_dir(tmp_path)
    lake = FileLake(tmp_path / "lake")
    capture = FakeCapture(lake, {url: page})
    loop = DiscoveryLoop(
        [OneLead(url)],
        capture=capture,
        lake=lake,
        run_id="run-r35-capability",
        provenance=PROVENANCE,
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
        parse_executor=SyntheticParseExecutor(),
    )
    return loop


def test_synthetic_registry_search_form_covers_record_fields_without_values(tmp_path: Path) -> None:
    url = "https://registry.synthetic.test/"
    page = (
        "<html><body><h1>Public records</h1><form>"
        '<label>Número de registro<input type="text" name="record_query"></label>'
        '<button type="submit">Buscar</button></form></body></html>'
    )
    loop = _loop(tmp_path, page, url)
    decision = CapabilityCritic()

    result = loop.discover_sources(
        tmp_path,
        ONTOLOGY,
        decision,
        gaps=("record_identifier", "establishment_date"),
    )

    objective = result["objectives"][0]
    assert set(objective["access_path"]) == {"record_identifier", "establishment_date"}
    assert all(
        path["kind"] == "search_form"
        and path["access_path_quote"] == "Buscar"
        and path["form_index"] == 0
        and "property_quote" not in path
        for path in objective["access_path"].values()
    )
    assert loop._page_access_contexts[url]["forms"][0]["search_like"] is False
    assert decision.purposes.count("critic.phase3.capability") == 1
    trace = next(
        step
        for step in loop.trace
        if step.get("requested", {}).get("tool") == "critic.phase3.capability"
    )
    assert trace["generated_by"]["model"] == "synthetic-critic-family"
    assert all(decision["accepted"] for decision in trace["evaluated"]["decisions"])
    manifest = json.loads(
        (tmp_path / "03-fanout/sources" / objective["source_id"] / "candidate.json").read_text()
    )
    assert manifest["access_path"] == objective["access_path"]


def test_synthetic_blog_without_retrieval_affordance_is_rejected(tmp_path: Path) -> None:
    url = "https://registry.synthetic.test/editorial"
    page = (
        "<html><body><article><h1>Notes from the editor</h1>"
        "<p>General commentary about public information.</p></article></body></html>"
    )
    loop = _loop(tmp_path, page, url)

    with pytest.raises(NoConfirmedSources):
        loop.discover_sources(
            tmp_path,
            ONTOLOGY,
            CapabilityCritic(accept=False),
            gaps=("record_identifier",),
        )

    trace = next(
        step
        for step in loop.trace
        if step.get("requested", {}).get("tool") == "critic.phase3.capability"
    )
    assert trace["evaluated"]["decisions"][0]["accepted"] is False
    assert trace["evaluated"]["decisions"][0]["access_path_quote"] is None


def test_blog_paragraph_cannot_be_relabelled_as_metadata(tmp_path: Path) -> None:
    url = "https://registry.synthetic.test/editorial"
    page = (
        "<html><body><article><h1>Notes from the editor</h1>"
        "<p>General commentary about public information.</p></article></body></html>"
    )
    loop = _loop(tmp_path, page, url)

    with pytest.raises(NoConfirmedSources):
        loop.discover_sources(
            tmp_path,
            ONTOLOGY,
            BlogMetadataCritic(),
            gaps=("record_identifier",),
        )

    trace = next(
        step
        for step in loop.trace
        if step.get("requested", {}).get("tool") == "critic.phase3.capability"
    )
    decision = trace["evaluated"]["decisions"][0]
    assert decision["accepted"] is False
    assert decision["access_path_quote"] == "General commentary about public information."
    assert "parsed field or schema structure" in decision["reason"]


def test_direct_extensionless_dataset_keeps_document_bronze_metadata(tmp_path: Path) -> None:
    url = "https://registry.synthetic.test/export"
    payload = b"record_identifier,establishment_date\nrec-01,2001-04-03\nrec-02,2010-08-12\n"
    loop = _loop(tmp_path, payload, url)
    decision = CapabilityCritic()

    result = loop.discover_sources(
        tmp_path,
        ONTOLOGY,
        decision,
        gaps=("record_identifier", "establishment_date"),
    )

    objective = result["objectives"][0]
    manifest = json.loads(
        (tmp_path / "03-fanout/sources" / objective["source_id"] / "candidate.json").read_text()
    )
    assert objective["confirmed_bronze_key"] == manifest["document_key"]
    assert objective["document_key"] == manifest["document_key"]
    assert manifest["document_content_type"] == "application/octet-stream"
    assert objective["access_path"] == manifest["access_path"]
    assert all(
        path["kind"] == "dataset"
        and path["capture_key"] == objective["confirmed_bronze_key"]
        and path["content_type"] == "application/octet-stream"
        and path["size_bytes"] == len(payload)
        for path in objective["access_path"].values()
    )
