"""A reviewed workbook is a source only after its parsed fields reach the P3 critic."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from ontofill.case.checkpoints import ApprovalArtifactMismatch
from ontofill.lake import FileLake
from ontofill.phases.p3_fanout.discovery_loop import (
    DiscoveryLoop,
    _approved_link_publisher,
    _captured_property_evidence,
    _document_sheet_preview,
    _screen_access_content,
)
from tests.test_discovery_loop import StaticProvider


def test_workbook_preview_uses_each_sheet_header_and_four_bounded_rows() -> None:
    rows = [
        {"sheet": "Certified records", "row_number": 1, "values": ["Certified entities"]},
        {"sheet": "Certified records", "row_number": 2, "values": ["Company name", "RFC", "City"]},
        *(
            {
                "sheet": "Certified records",
                "row_number": index + 3,
                "values": [f"Example Company {index}", f"SYN{index:06d}AAA", "North"],
            }
            for index in range(6)
        ),
        {"sheet": "Other records", "row_number": 1, "values": ["Company name", "RFC"]},
        {"sheet": "Other records", "row_number": 2, "values": ["Example Other", "OTH000001AAA"]},
    ]
    parsed = SimpleNamespace(format="xls", rows=rows)

    preview = _document_sheet_preview(parsed)

    assert len(preview) == 2
    assert preview[0]["headers"] == ["Company name", "RFC", "City"]
    assert len(preview[0]["sample_rows"]) == 4
    assert preview[1]["headers"] == ["Company name", "RFC"]
    screened = _screen_access_content({"document_sheets": preview})
    assert "<page_content>Company name</page_content>" in str(screened)
    assert "<page_content>SYN000000AAA</page_content>" in str(screened)


def test_approved_document_inherits_only_verified_linking_publisher(tmp_path) -> None:
    policy = {
        "jurisdiction": "Example Republic",
        "trusted_publishers": [
            {
                "kind": "Example records office",
                "tier": "primary",
                "domains": ["agency.example.test"],
                "rationale": "Official records",
            }
        ],
        "unknown_source_action": "review",
    }
    root = tmp_path / "03-fanout/sources"
    parent = root / "source-parent"
    parent.mkdir(parents=True)
    parent_key = "sha256:" + "1" * 64
    parent_url = "https://agency.example.test/certified"
    (parent / "candidate.json").write_text(
        json.dumps({"source_id": "source-parent", "url": parent_url, "capture_key": parent_key})
    )
    linked = root / "source-link-test"
    linked.mkdir()
    url = "https://blob.example.test/certified.xls"
    fingerprint = hashlib.sha256(url.encode()).hexdigest()
    packet = {
        "source_id": "source-link-test",
        "url": url,
        "fingerprint": fingerprint,
        "capture_key": parent_key,
        "link_provenance": {
            "parent_source_id": "source-parent",
            "parent_page_url": parent_url,
            "parent_capture_key": parent_key,
            "link_url": url,
            "link_text": "Certified companies",
        },
    }
    candidate_path = linked / "candidate.json"
    candidate_path.write_text(json.dumps(packet))
    relative = candidate_path.relative_to(tmp_path).as_posix()
    (linked / "APPROVED").write_text(
        json.dumps(
            {
                "approver": "Reviewer",
                "date": "2026-09-27",
                "checkpoint": "source",
                "decision": "approve",
                "source_fingerprint": fingerprint,
                "identity_source": "local",
                "artifact_sha256": {
                    relative: hashlib.sha256(candidate_path.read_bytes()).hexdigest()
                },
            }
        )
    )

    inherited = _approved_link_publisher(
        tmp_path, "source-link-test", fingerprint, url, policy, "vultr"
    )
    assert inherited is not None
    assert inherited["publisher_of_record"]["tier"] == "primary"
    assert inherited["publisher_of_record"]["kind"] == "Example records office"
    assert inherited["matched_publishers"][0]["domain"] == "agency.example.test"
    packet["link_provenance"]["parent_page_url"] = "https://other.example.test/false"
    candidate_path.write_text(json.dumps(packet))
    with pytest.raises(ApprovalArtifactMismatch, match="different artifact version"):
        _approved_link_publisher(tmp_path, "source-link-test", fingerprint, url, policy, "vultr")


def test_critic_sees_screened_workbook_rows_and_confirms_name_and_identifier(tmp_path) -> None:
    url = "https://blob.example.test/certified.xls"
    key = "sha256:" + "a" * 64
    preview = _document_sheet_preview(
        SimpleNamespace(
            format="xls",
            rows=[
                {"sheet": "Certified", "row_number": 1, "values": ["Certified entities"]},
                {"sheet": "Certified", "row_number": 2, "values": ["Company name", "RFC"]},
                *(
                    {
                        "sheet": "Certified",
                        "row_number": index + 3,
                        "values": [f"Example Company {index}", f"SYN{index:06d}AAA"],
                    }
                    for index in range(5)
                ),
            ],
        )
    )
    context = {
        "page_text": "",
        "forms": [],
        "links": [],
        "table_headers": [],
        "listing_row_count": 0,
        "document": {
            "capture_key": key,
            "content_type": "application/vnd.ms-excel",
            "size_bytes": 1200,
            "format": "xls",
            "headers": preview[0]["headers"],
            "sheets": preview,
            "row_count": 7,
            "text": "",
        },
    }
    candidate = {
        "url": url,
        "landing_url": url,
        "title": "Certified companies",
        "snippet": "Public company list",
        "source_id": "source-link-test",
        "status": "captured",
        "capture_key": key,
        "authority": "auto",
        "property_ids": ["company_name", "rfc"],
        "matched_publishers": [
            {"kind": "Example records office", "tier": "primary", "domain": "agency.example.test"}
        ],
        "publisher_of_record": {
            "kind": "Example records office",
            "tier": "primary",
            "domain": "agency.example.test",
            "basis": "approved_policy",
        },
        "publisher_inherited_from_approved_link": True,
    }
    ontology = {
        "primary_class": "Company",
        "classes": [
            {
                "id": "Company",
                "label": "Company",
                "label_plural": "Companies",
                "title_property": "company_name",
                "identifier_property": "rfc",
            }
        ],
        "properties": [
            {"id": "company_name", "label": "Company name", "domain": "Company", "dod": True},
            {"id": "rfc", "label": "RFC", "domain": "Company", "dod": True},
        ],
    }
    loop = DiscoveryLoop(
        [StaticProvider("synthetic", {})],
        capture=lambda *_args, **_kwargs: {},
        lake=FileLake(tmp_path / "lake"),
        run_id="run-r50-test",
        provenance={"backend": "vultr", "model": "synthetic", "at": "2026-01-01T00:00:00Z"},
    )
    loop._page_access_contexts[url] = context

    class Critic:
        backend = "vultr"
        model = "synthetic"

        def complete_json(self, purpose: str, prompt: str, _schema: dict) -> dict:
            assert purpose == "critic.phase3.capability"
            listing = json.loads(prompt.split("Review context: ", 1)[1])["pages"][0]
            evidence = listing["captured_access_evidence"]["document"]
            assert listing["publisher_inherited_from_approved_link"] is True
            assert listing["publisher_matches"][0]["domain"] == "agency.example.test"
            assert len(evidence["sheets"][0]["sample_rows"]) == 4
            assert "<page_content>RFC</page_content>" in str(evidence["sheets"])
            return {
                "verdicts": [
                    {
                        "index": 0,
                        "property_id": property_id,
                        "provides": True,
                        "authority_verdict": "authoritative",
                        "access_path": {
                            "kind": "dataset",
                            "access_path_quote": label,
                            "property_quote": label,
                            "record_granularity": "entity_records",
                            "granularity_quote": "Company name",
                        },
                        "reason": "Captured workbook has per-company rows and matching columns.",
                    }
                    for property_id, label in (("company_name", "Company name"), ("rfc", "RFC"))
                ]
            }

    verdicts = loop._model_verdicts(
        Critic(), {"candidates": {url: candidate}}, ontology, {"trusted_publishers": []}
    )
    assert verdicts[url] == {"company_name": None, "rfc": None}
    paths = loop._page_access_paths[url]
    assert all(path["record_granularity"] == "entity_records" for path in paths.values())
    property_evidence = _captured_property_evidence(candidate, context, paths)
    assert property_evidence["rfc"]["quote"] == "RFC"
    assert property_evidence["company_name"]["document_sheets"] == preview
