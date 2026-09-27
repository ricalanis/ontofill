import logging
from types import SimpleNamespace

import pytest

from ontofill.workflow import _remaining_budget_usd


def test_unpriced_cached_calls_preserve_remaining_budget_and_log_only_call_numbers(caplog):
    secret = "synthetic-token-that-must-not-be-logged"
    decision = SimpleNamespace(
        call_log=[
            {
                "status": "cached",
                "purpose": "phase1.prd",
                "request": secret,
                "usage": {"est_usd": None},
            },
            {"purpose": "phase2.ontology", "usage": {"est_usd": 0.094}},
            {"purpose": "phase2.ontology", "usage": {"est_usd": float("nan")}},
            {"purpose": "phase2.ontology", "usage": {"est_usd": float("inf")}},
            {"purpose": "phase2.ontology", "usage": {"est_usd": -0.5}},
        ]
    )

    with caplog.at_level(logging.WARNING, logger="ontofill.workflow"):
        remaining = _remaining_budget_usd(decision, 15.0)

    assert remaining == pytest.approx(14.906)
    assert "unpriced" in caplog.text
    assert "call=1" in caplog.text
    assert "call=3" in caplog.text
    assert "call=4" in caplog.text
    assert "call=5" in caplog.text
    assert secret not in caplog.text
