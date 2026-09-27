"""R30 P2 factors must cite published PRD records when labeled grounded."""

from __future__ import annotations

import json

from ontofill.inference import RecordedDecisionClient
from ontofill.phases.p2_ontology.phase import draft_factors

_PRD_QUOTE = "Every award identifies its procuring region."


def _prd() -> dict:
    return {
        "requirements": [
            {
                "id": "req-region",
                "job_id": "find-awards",
                "description": _PRD_QUOTE,
            }
        ],
        "definition_of_done": [
            {
                "id": "dod-region",
                "basis_quote": "Include the published region for each award.",
            }
        ],
    }


def _factor(*, kind: str, evidence: list[dict]) -> dict:
    return {
        "id": "region",
        "label": "Region",
        "description": "How awards vary by procuring region",
        "kind": kind,
        "evidence": evidence,
    }


def _prd_evidence() -> dict:
    return {
        "source": {
            "type": "case_file",
            "path": "01-scope/prd.json",
            "record_id": "requirement:req-region",
            "quote": _PRD_QUOTE,
        },
        "description": f"PRD requirement req-region: {_PRD_QUOTE}",
    }


def _validation_callback(calls: list[tuple[str, int, str]]):
    return lambda purpose, attempt, reason: calls.append((purpose, attempt, reason))


def test_grounded_factor_without_evidence_retries_with_exact_prd_record_guidance(tmp_path) -> None:
    missing = _factor(kind="grounded", evidence=[])
    corrected = _factor(kind="grounded", evidence=[_prd_evidence()])
    decision = RecordedDecisionClient(
        {"phase2.factors": [{"factors": [missing]}, {"factors": [corrected]}]}
    )
    validation_calls: list[tuple[str, int, str]] = []

    factors = draft_factors(
        tmp_path,
        _prd(),
        decision,
        on_validation_error=_validation_callback(validation_calls),
    )

    prompts = [prompt for purpose, prompt in decision.calls if purpose == "phase2.factors"]
    assert len(prompts) == 2
    assert "grounded factor `region`" in validation_calls[0][2]
    assert "requirement:req-region" in validation_calls[0][2]
    assert validation_calls[0][2] in prompts[1]
    assert "exact quote" in prompts[1]
    assert factors["factors"] == [corrected]


def test_exhaustion_keeps_cited_factor_and_relabels_only_unsupported_factor(tmp_path) -> None:
    supported = _factor(kind="grounded", evidence=[_prd_evidence()])
    unsupported = {
        **_factor(
            kind="grounded",
            evidence=[
                {
                    "url": "https://invented.example/regions",
                    "description": "A supposed regional registry",
                }
            ],
        ),
        "id": "award_type",
        "label": "Award type",
    }
    candidate = {"factors": [supported, unsupported]}
    decision = RecordedDecisionClient({"phase2.factors": [candidate, candidate, candidate]})
    validation_calls: list[tuple[str, int, str]] = []

    factors = draft_factors(
        tmp_path,
        _prd(),
        decision,
        on_validation_error=_validation_callback(validation_calls),
    )

    assert [attempt for _, attempt, _ in validation_calls] == [1, 2, 3]
    assert all("award_type" in reason for _, _, reason in validation_calls)
    assert factors["factors"] == [
        supported,
        {
            **unsupported,
            "kind": "conceptual",
            "evidence": [],
        },
    ]
    assert "invented.example" not in (tmp_path / "02-ontology/factors/factors.json").read_text()


def test_cached_grounded_factor_without_evidence_is_redrafted(tmp_path) -> None:
    conceptual = _factor(kind="conceptual", evidence=[])
    corrected = _factor(kind="grounded", evidence=[_prd_evidence()])
    decision = RecordedDecisionClient(
        {"phase2.factors": [{"factors": [conceptual]}, {"factors": [corrected]}]}
    )
    draft_factors(tmp_path, _prd(), decision)
    path = tmp_path / "02-ontology/factors/factors.json"
    cached = json.loads(path.read_text(encoding="utf-8"))
    cached["factors"][0]["kind"] = "grounded"
    path.write_text(json.dumps(cached), encoding="utf-8")

    factors = draft_factors(tmp_path, _prd(), decision)

    assert factors["factors"] == [corrected]
    assert len([purpose for purpose, _ in decision.calls if purpose == "phase2.factors"]) == 2
