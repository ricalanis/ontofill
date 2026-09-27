"""R18 + R19: "who acted" names the control-plane runner and lists its starts and resumes for the run, read-only
from ONTOFILL_CONSOLE's runner state (events.jsonl). Without runner state, it says the run doesn't name its runner."""

import json

RUN = "run-libraries-0001"


def test_runner_events_in_who_acted(client, tmp_path, monkeypatch):
    root = tmp_path / "runner"
    root.mkdir()
    rows = [
        {
            "ts": "2026-09-27T10:00:00Z",
            "case_id": "libraries",
            "kind": "started",
            "detail": "start requested",
            "run_id": RUN,
        },
        {
            "ts": "2026-09-27T10:40:00Z",
            "case_id": "libraries",
            "kind": "paused_at_checkpoint",
            "detail": "prd",
            "run_id": RUN,
        },
        {
            "ts": "2026-09-27T11:10:00Z",
            "case_id": "libraries",
            "kind": "resumed",
            "detail": "resumed after the prd decision",
            "run_id": RUN,
        },
        {
            "ts": "2026-09-27T11:11:00Z",
            "case_id": "parks",
            "kind": "started",
            "detail": "other case",
            "run_id": "run-x",
        },
        {
            "ts": "2026-09-27T11:12:00Z",
            "case_id": "libraries",
            "kind": "resumed",
            "detail": "other run",
            "run_id": "run-other",
        },
    ]
    (root / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + "{torn")
    monkeypatch.setenv("ONTOFILL_RUNNER_STATE", str(root))
    m = client.get(f"/cases/libraries/api/viz/inference?run={RUN}").json()
    a = m["actors"]
    assert a["runner"] == "ontofill-runner (control plane)" and a["runner_known"]
    assert [e["kind"] for e in a["runner_events"]] == ["started", "paused_at_checkpoint", "resumed"]
    page = client.get(f"/cases/libraries/inference?run={RUN}").text
    assert "resumed after the prd decision" in page and "other case" not in page and "other run" not in page


def test_without_runner_state(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOFILL_RUNNER_STATE", str(tmp_path / "absent"))
    a = client.get(f"/cases/libraries/api/viz/inference?run={RUN}").json()["actors"]
    assert a["runner_events"] == [] and "does not name its runner" in a["engine_label"]
