"""A quarantine says what each screener decided (PA 784ce13: step.screen carries Jev and content-safety verdicts), how
many allowlisted links the withheld page offered, and which later step followed one. Old traces still read."""

import json
from datetime import UTC, datetime, timedelta

from test_viz_live import RUN, run_dir

from ontofill_console import live

T0 = datetime(2031, 2, 1, tzinfo=UTC)
NEW_REASON = "gateway flagged: Jev injection (0.93); content safety safe (safety-model-x)"


def steps():
    base = {"run_id": RUN, "phase": 5, "source_id": "portal-example", "mode": "S1", "value_ids": []}
    return [
        {
            **base,
            "step_id": f"step:{RUN}:q-new",
            "event": "quarantine",
            "observed": "page",
            "requested": {"url": "https://portal.example/list"},
            "evaluated": {"status": "quarantined_continue", "reason": NEW_REASON},
            "executed": {
                "urls_offered_from_quarantined_page": ["https://portal.example/a", "https://portal.example/b"]
            },
            "screen": {
                "flagged": True,
                "jev_choice": "injection",
                "jev_confidence": 0.93,
                "safety_verdict": "safe",
                "reason": NEW_REASON,
                "by": "gateway",
            },
            "ts": T0.isoformat(),
        },
        {
            **base,
            "step_id": f"step:{RUN}:q-old",
            "event": "quarantine",
            "observed": "page",
            "requested": "read",
            "evaluated": {"status": "quarantined_continue"},
            "executed": "screen",
            "screen": {
                "flagged": True,
                "jev_choice": None,
                "jev_confidence": None,
                "safety_verdict": "unavailable",
                "reason": "gateway X-BA-Gate: flagged (quarantined in the prompt)",
                "by": "gateway",
            },
            "ts": (T0 + timedelta(seconds=1)).isoformat(),
        },
        {
            **base,
            "step_id": f"step:{RUN}:nav",
            "observed": "page",
            "requested": {"action": "navigate", "url": "https://portal.example/a", "from_quarantined_page": True},
            "executed": "navigate",
            "evaluated": "ok",
            "ts": (T0 + timedelta(seconds=2)).isoformat(),
        },
    ]


def test_screen_text_new_and_old():
    new, old, _ = live.annotate(steps())
    assert live.screen_text(new["detail"]).startswith(
        "Jev: injection 0.93 · safety: safe (safety-model-x) · screened by the gateway · "
        "2 allowlisted links offered from the withheld page"
    )
    assert "flagged by the gateway (no per-screener detail recorded)" in live.screen_text(old["detail"])
    assert "safety: unavailable" in live.screen_text(old["detail"])


def test_views_show_the_screens(client, cases_dir):
    with (run_dir(cases_dir) / "trace.live.jsonl").open("a") as f:
        for s in steps():
            f.write(json.dumps(s) + "\n")
    fm = client.get(f"/cases/libraries/api/viz/failures?run={RUN}").json()
    q = [s for s in fm["strips"] if s["kind"] == "quarantine" and "portal.example" in s["title"]]
    assert any("Jev: injection 0.93 · safety: safe (safety-model-x)" in s["detail"] for s in q)
    inbox = client.get("/api/viz/inbox").json()
    assert any("Jev: injection 0.93" in (s.get("detail") or "") for s in inbox["strips"])
    run = client.get(f"/cases/libraries/runs/{RUN}").text
    assert "2 allowlisted links offered from the withheld page" in run and "https://portal.example/b" in run
    assert "via a quarantined page&#39;s link" in run or "via a quarantined page's link" in run
    assert "(safety-model-x)" in run
