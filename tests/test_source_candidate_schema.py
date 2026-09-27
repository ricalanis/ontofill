"""The source-review packet is a public contract shared with the console."""

from __future__ import annotations

from copy import deepcopy

import pytest
from jsonschema import ValidationError

from ontofill.contracts import validate_document

PROVENANCE = {"backend": "vultr", "model": "synthetic", "at": "2026-09-27T00:00:00Z"}
KEY = "sha256:" + "a" * 64
FINGERPRINT = "b" * 64


def _redirect_packet() -> dict:
    return {
        "source_id": "source-redirect-test",
        "url": "https://registry.other.test/records",
        "title": "Redirect destination at registry.other.test",
        "snippet": "Public URL observed in a browser redirect chain.",
        "provider": "sandbox_redirect",
        "providers": ["sandbox_redirect"],
        "capture_key": None,
        "authority": "review",
        "authority_tier": "unknown",
        "authority_reason": "publisher authority needs human review",
        "fingerprint": FINGERPRINT,
        "redirect_chain": [
            "https://origin.example.test/start",
            "https://registry.other.test/records",
        ],
        "generated_by": PROVENANCE,
    }


def test_redirect_review_packet_has_complete_chain_and_provenance() -> None:
    validate_document("source-candidate", _redirect_packet())


def test_captured_source_candidate_keeps_evidence_fields() -> None:
    packet = {
        **_redirect_packet(),
        "provider": "synthetic_search",
        "providers": ["synthetic_search"],
        "capture_key": KEY,
        "screenshot_key": KEY,
        "source_type": "public registry",
        "authority": "auto",
        "authority_tier": "primary",
        "authority_reason": "approved publisher kind: public registry",
        "covers": ["property-record-name"],
        "landing_url": "https://registry.other.test/records",
        "property_evidence": {
            "property-record-name": {
                "quote": "A synthetic record name appears here.",
                "capture_key": KEY,
                "url": "https://registry.other.test/records",
                "publisher_kinds": ["public registry"],
                "authority_verdict": "code_evidence_only",
            }
        },
    }
    validate_document("source-candidate", packet)


@pytest.mark.parametrize("missing", ["redirect_chain", "generated_by"])
def test_redirect_packet_rejects_missing_review_evidence(missing: str) -> None:
    packet = deepcopy(_redirect_packet())
    packet.pop(missing)
    with pytest.raises(ValidationError):
        validate_document("source-candidate", packet)


def test_redirect_packet_rejects_chain_without_blocked_target() -> None:
    packet = _redirect_packet()
    packet["redirect_chain"] = [packet["redirect_chain"][0]]
    with pytest.raises(ValidationError):
        validate_document("source-candidate", packet)
