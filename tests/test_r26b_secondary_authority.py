"""R26b: human secondary revisions map to generic, domainful PRD publishers."""

from __future__ import annotations

import pytest

from ontofill.phases.p1_scope.phase import (
    _apply_human_authority_revisions,
    _authority_policy_check,
    _secondary_clauses,
)

ENGLISH_REVISION = (
    "DoD must be the case's: >=50 suppliers linked to real public contracts; "
    ">=80% of them with a complete core profile (identity, address, founding date, "
    "tax-list status, sanction status); 0 values without evidence; >=4 source classes. "
    "Keep US sanctions/registry lists as secondary cross-checks."
)

SPANISH_REVISION = (
    "Las fuentes oficiales mexicanas (CompraNet/compras públicas, SAT incl. lista 69-B, "
    "registros públicos) son PRIMARIAS y deben listar sus dominios; las listas de "
    "sanciones y registros de EE. UU. son SECUNDARIAS (verificación cruzada); quitar "
    "la entrada duplicada de verificación cruzada. Mantener las 4 metas del DoD tal "
    "como están."
)


def _publisher(
    kind: str,
    domain: str,
    *,
    tier: str = "secondary",
    jurisdiction: str = "United States",
) -> dict:
    return {
        "kind": kind,
        "tier": tier,
        "jurisdiction": jurisdiction,
        "domains": [domain],
        "rationale": "A public publisher suitable for secondary comparison.",
    }


def _policy(publishers: list[dict]) -> dict:
    return {
        "jurisdiction": "Example Republic",
        "trusted_publishers": [
            _publisher(
                "Example Republic public archive",
                "archive.example.test",
                tier="primary",
                jurisdiction="Example Republic",
            ),
            *publishers,
        ],
        "unknown_source_action": "review",
    }


def _recorded_prd(publishers: list[dict]) -> dict:
    return {"authority_policy": _policy(publishers)}


def _us_secondary_publishers() -> list[dict]:
    return [
        _publisher("United States sanctions list", "sanctions.example.test"),
        _publisher("United States registry list", "registry.example.test"),
    ]


@pytest.mark.parametrize(
    "reason",
    [ENGLISH_REVISION, SPANISH_REVISION],
    ids=["english-control-revision", "spanish-control-revision"],
)
def test_recorded_secondary_revisions_accept_matching_domain_publishers(reason: str) -> None:
    result = _authority_policy_check(
        _recorded_prd(_us_secondary_publishers()), [{"reason": reason}]
    )

    assert result.passed, "; ".join(result.objections)


@pytest.mark.parametrize(
    ("reason", "missing_subject"),
    [
        (ENGLISH_REVISION, "registry"),
        (SPANISH_REVISION, "registros"),
    ],
    ids=["english-registry", "spanish-registros"],
)
def test_missing_secondary_kind_reports_subject_and_domain_requirement(
    reason: str, missing_subject: str
) -> None:
    result = _authority_policy_check(
        _recorded_prd([_publisher("United States sanctions list", "sanctions.example.test")]),
        [{"reason": reason}],
    )

    feedback = "; ".join(result.objections).casefold()
    assert not result.passed
    assert missing_subject in feedback
    assert "secondary" in feedback
    assert "specific domain" in feedback


@pytest.mark.parametrize(
    "reason",
    [
        "U.S. sanctions lists are SECONDARY.",
        "Secondary sources, e.g. public registries, remain in scope.",
        "Las listas de sanciones y registros de EE. UU. son SECUNDARIAS.",
    ],
    ids=["us", "eg", "ee-uu"],
)
def test_secondary_clause_splitter_keeps_common_abbreviations(reason: str) -> None:
    assert _secondary_clauses([{"reason": reason}]) == [reason]


def test_subjectless_secondary_fragments_do_not_create_objections() -> None:
    result = _authority_policy_check(
        _recorded_prd(_us_secondary_publishers()),
        [{"reason": "Secondary sources, e.g. these are secondary."}],
    )

    assert result.passed, "; ".join(result.objections)


def test_foreign_secondary_clause_does_not_demote_local_primary_registry() -> None:
    document = _recorded_prd(_us_secondary_publishers())
    local = document["authority_policy"]["trusted_publishers"][0]
    local["kind"] = "Example Republic registros públicos"

    _apply_human_authority_revisions(document, [{"reason": SPANISH_REVISION}])

    assert local["tier"] == "primary"
    assert _authority_policy_check(document, [{"reason": SPANISH_REVISION}]).passed
