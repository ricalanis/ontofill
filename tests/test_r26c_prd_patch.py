"""R26c: a narrow human deny changes the named domain without regressing a reviewed PRD."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from ontofill.phases.p1_scope.phase import PrdDraftUnavailable, _authority_policy_check, draft_prd
from tests.approval_support import bind_approval
from tests.test_r22_p1 import _prd
from tests.test_r26b_secondary_authority import ENGLISH_REVISION, SPANISH_REVISION

FOURTH_DENIAL = (
    "Keep dod1-dod4, the tiers and the secondary US publishers exactly as they are. "
    "One defect: the primary procurement publisher domain comprar.gob.mx does not exist "
    "(no DNS record), so no CompraNet/compras publicas source could ever match it. "
    "Replace it with the actual, resolvable domain(s) of Mexico's federal public "
    "procurement publisher."
)
THIRD_DENIAL = (
    "The >=80% complete-profile target comes from my reason (basis human, quote "
    "'>=80% of them with a complete core profile'). Mexican official publishers "
    "(CompraNet/compras públicas, SAT incl. 69-B, public registries) are tier PRIMARY "
    "with their domains; US sanctions/registry lists are SECONDARY."
)


def _v4() -> dict:
    """The v4 authority policy as drafted live; other sections stay synthetic."""
    prior = _prd()
    prior["authority_policy"] = {
        "jurisdiction": "Mexico",
        "trusted_publishers": [
            {
                "kind": kind,
                "tier": tier,
                "jurisdiction": jurisdiction,
                "domains": [domain],
                "rationale": "Public publisher selected for this source class.",
            }
            for kind, tier, jurisdiction, domain in [
                (
                    "Mexican public procurement records (CompraNet/compras publicas)",
                    "primary",
                    "Mexico",
                    "comprar.gob.mx",
                ),
                ("Mexican tax authority incl. lista 69-B (SAT)", "primary", "Mexico", "sat.gob.mx"),
                (
                    "Mexican public commercial registries (registros publicos)",
                    "primary",
                    "Mexico",
                    "economia.gob.mx",
                ),
                (
                    "US sanctions lists (OFAC) cross-check",
                    "secondary",
                    "United States",
                    "treasury.gov",
                ),
                (
                    "US company registry lists cross-check",
                    "secondary",
                    "United States",
                    "sec.gov",
                ),
            ]
        ],
        "unknown_source_action": "review",
    }
    prior["revisions"] = [
        {
            "n": index,
            "reason": reason,
            "decision": "deny",
            "approver": "reviewer",
            "date": "2026-09-27",
        }
        for index, reason in enumerate([ENGLISH_REVISION, SPANISH_REVISION, THIRD_DENIAL], 1)
    ]
    prior["generated_by"] = {
        "backend": "vultr",
        "model": "synthetic-vultr",
        "at": "2026-09-27T00:00:00Z",
    }
    return prior


def test_v4_with_fourth_denial_has_no_new_secondary_subject() -> None:
    prior = _v4()
    revisions = [*prior["revisions"], {"reason": FOURTH_DENIAL}]

    result = _authority_policy_check(prior, revisions)

    assert result.passed, "; ".join(result.objections)


class PatchDecision:
    backend = "vultr"
    model = "synthetic-vultr"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.call_log: list[dict] = []

    def complete_json(self, purpose: str, prompt: str, schema: dict) -> dict:
        self.calls.append((purpose, prompt, schema))
        assert purpose == "phase1.prd.domain_patch", "a narrow deny must patch, not redraft"
        assert "comprar.gob.mx" in prompt
        return {"domains": ["procurement.example.test"]}

    def review_json(self, _purpose: str, _artifact: dict, _prompt: str) -> dict:
        return {"accepted": True, "reason": "No unrelated field changed"}


def _case_with_v4_denial(tmp_path) -> dict:
    (tmp_path / "brief.md").write_text(
        "Find public contract records with evidence.", encoding="utf-8"
    )
    scope = tmp_path / "01-scope"
    scope.mkdir()
    prior = _v4()
    (scope / "prd.json").write_text(json.dumps(prior) + "\n", encoding="utf-8")
    (scope / "prd.md").write_text("Reviewed v4 draft\n", encoding="utf-8")
    (scope / "prd.input.sha256").write_text("old-input-key\n", encoding="utf-8")
    for revision in prior["revisions"]:
        archive = scope / "revisions" / str(revision["n"])
        archive.mkdir(parents=True)
        (archive / "APPROVED").write_text(
            json.dumps({**revision, "checkpoint": "prd"}) + "\n", encoding="utf-8"
        )
    marker = bind_approval(
        tmp_path,
        ["01-scope/prd.json"],
        {
            "approver": "reviewer",
            "date": "2026-09-27",
            "checkpoint": "prd",
            "decision": "deny",
            "reason": FOURTH_DENIAL,
        },
    )
    (scope / "APPROVED").write_text(json.dumps(marker) + "\n", encoding="utf-8")
    return prior


def test_digest_verified_v4_domain_denial_patches_only_the_named_publisher(tmp_path) -> None:
    prior = _case_with_v4_denial(tmp_path)
    scope = tmp_path / "01-scope"
    decision = PatchDecision()

    revised = draft_prd(tmp_path, decision, run_id="run-r26c-v5")

    expected = deepcopy(prior)
    expected["authority_policy"]["trusted_publishers"][0]["domains"] = ["procurement.example.test"]
    expected["revisions"].append(
        {
            "n": 4,
            "reason": FOURTH_DENIAL,
            "decision": "deny",
            "approver": "reviewer",
            "date": "2026-09-27",
        }
    )
    expected["generated_by"] = revised["generated_by"]
    assert revised == expected
    assert decision.calls and all(call[0] == "phase1.prd.domain_patch" for call in decision.calls)
    assert not (scope / "APPROVED").exists()
    assert (scope / "revisions/4/APPROVED").exists()


def test_invalid_domain_patch_keeps_reviewed_bytes_and_denial(tmp_path) -> None:
    _case_with_v4_denial(tmp_path)
    scope = tmp_path / "01-scope"
    names = ("prd.json", "prd.md", "prd.input.sha256", "APPROVED")
    before = {name: (scope / name).read_bytes() for name in names}

    class InvalidPatch(PatchDecision):
        def complete_json(self, purpose: str, prompt: str, schema: dict) -> dict:
            super().complete_json(purpose, prompt, schema)
            return {"domains": ["comprar.gob.mx"]}

    with pytest.raises(PrdDraftUnavailable, match="replacement domains still include"):
        draft_prd(tmp_path, InvalidPatch(), run_id="run-r26c-invalid")

    assert {name: (scope / name).read_bytes() for name in names} == before
    assert not (scope / "revisions/4").exists()


class SchemaProbeDecision:
    backend = "vultr"
    model = "synthetic-vultr"

    def __init__(self) -> None:
        self.required: list[str] = []
        self.call_log: list[dict] = []

    def complete_json(self, _purpose: str, _prompt: str, schema: dict) -> dict:
        if "authority_policy" in schema["properties"]:
            self.required = schema["$defs"]["trusted_publisher"]["required"]
        draft = _prd()
        return {name: deepcopy(draft[name]) for name in schema["required"]}

    def review_json(self, _purpose: str, _artifact: dict, _prompt: str) -> dict:
        return {"accepted": True, "reason": "valid"}


def test_new_full_draft_tool_schema_requires_publisher_tier(tmp_path) -> None:
    (tmp_path / "brief.md").write_text("Find public records in Example City.", encoding="utf-8")
    decision = SchemaProbeDecision()

    draft_prd(tmp_path, decision, run_id="run-r26c-fresh")

    assert "tier" in decision.required
