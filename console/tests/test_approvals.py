"""Approvals v0.9.7: SSO identity, artifact digests (409 on stale), append-only decisions.jsonl, no cross-origin."""

import json
import re
import threading

from conftest import IDENTITY

from ontofill_console import approvals as ap

PRD_PAGE = "/cases/libraries/approvals/01-scope"
POST = "/cases/libraries/approvals"


def digests(html: str) -> dict:
    return dict(re.findall(r'name="(artifact_sha256\.[^"]+)" value="([0-9a-f]*)"', html))


def form(client, page=PRD_PAGE, headers=IDENTITY, **extra):
    html = client.get(page, headers=headers).text
    phase_dir = page.split("/approvals/", 1)[1]
    return {"phase_dir": phase_dir, **digests(html), **extra}


def post(client, data, headers=IDENTITY):
    h = {**headers, "host": "testserver", "origin": "http://testserver"}
    return client.post(POST, data=data, headers=h, follow_redirects=False)


def case_path(cases_dir, *parts):
    return cases_dir.joinpath("libraries", "case", *parts)


def log_lines(cases_dir):
    p = case_path(cases_dir, "decisions.jsonl")
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_page_shows_signer_and_digests(client):
    html = client.get(PRD_PAGE, headers=IDENTITY).text
    assert 'Signing as <strong class="mono">ana@example.org</strong>' in html
    assert 'name="approver"' not in html  # no typed name in SSO mode
    d = digests(html)
    assert set(d) == {"artifact_sha256.01-scope/prd.json", "artifact_sha256.01-scope/prd.md"}
    assert all(len(v) == 64 for v in d.values())
    anon = client.get(PRD_PAGE).text
    assert "No signed-in identity: decisions are disabled" in anon and "disabled" in anon


def test_sso_without_header_is_403_and_writes_nothing(client, cases_dir):
    data = form(client, decision="approve")
    r = post(client, data, headers={})
    assert r.status_code == 403
    assert not case_path(cases_dir, "01-scope", "APPROVED").exists() and not log_lines(cases_dir)


def test_approve_records_identity_digests_and_log(client, cases_dir):
    data = form(client, decision="approve", approver="Someone Typed This")
    r = post(client, data)
    assert r.status_code == 303 and "done=01-scope" in r.headers["location"]
    marker = json.loads(case_path(cases_dir, "01-scope", "APPROVED").read_text())
    assert marker["approver"] == "ana@example.org" and marker["identity_source"] == "sso"
    assert marker["checkpoint"] == "prd" and "decision" not in marker
    shown = {k.removeprefix("artifact_sha256."): v for k, v in data.items() if k.startswith("artifact_sha256.")}
    assert marker["artifact_sha256"] == shown
    [line] = log_lines(cases_dir)
    assert line["approver"] == "ana@example.org" and line["decision"] == "approve" and line["case_id"] == "libraries"
    assert line["artifact_sha256"] == marker["artifact_sha256"] and line["phase_dir"] == "01-scope"
    assert "Decision history" in client.get("/cases/libraries/approvals", headers=IDENTITY).text
    assert "ana@example.org" in client.get("/cases/libraries", headers=IDENTITY).text


def test_second_decision_is_409(client, cases_dir):
    data = form(client, decision="approve")
    assert post(client, data).status_code == 303
    again = post(client, data)
    assert again.status_code == 409 and len(log_lines(cases_dir)) == 1


def test_stale_artifact_is_409_and_writes_nothing(client, cases_dir):
    data = form(client, decision="approve")
    prd = case_path(cases_dir, "01-scope", "prd.json")
    prd.write_text(prd.read_text().replace('"version": "v2"', '"version": "v3"'))
    r = post(client, data)
    assert r.status_code == 409 and "changed since you opened it" in r.text
    assert not case_path(cases_dir, "01-scope", "APPROVED").exists() and not log_lines(cases_dir)


def test_missing_digest_is_409(client, cases_dir):
    data = form(client, decision="approve")
    data.pop("artifact_sha256.01-scope/prd.md")
    assert post(client, data).status_code == 409
    assert not case_path(cases_dir, "01-scope", "APPROVED").exists()


def test_deny_needs_a_reason(client, cases_dir):
    assert post(client, form(client, decision="deny")).status_code == 400
    r = post(client, form(client, decision="deny", reason="target has no basis"))
    assert r.status_code == 303
    marker = json.loads(case_path(cases_dir, "01-scope", "APPROVED").read_text())
    assert marker["decision"] == "deny" and marker["reason"] == "target has no basis"
    assert log_lines(cases_dir)[0]["reason"] == "target has no basis"


def test_factors_and_action_shapes(client, cases_dir):
    f = form(
        client,
        page="/cases/libraries/approvals/02-ontology/factors",
        decision="approve",
        **{"decision.operator_kind": "accept", "decision.service_level": "reject"},
    )
    assert post(client, f).status_code == 303
    marker = json.loads(case_path(cases_dir, "02-ontology", "factors", "APPROVED").read_text())
    assert marker["decisions"] == {"operator_kind": "accept", "service_level": "reject"}
    a = form(client, page="/cases/libraries/approvals/05-actions/req-0001", decision="deny", reason="not read-only")
    assert post(client, a).status_code == 303
    action = json.loads(case_path(cases_dir, "05-actions", "req-0001", "APPROVED").read_text())
    assert action["checkpoint"] == "action" and action["decision"] == "deny"
    assert action["run_id"] == "run-libraries-0001"  # the live run is paused at this checkpoint


def test_cross_origin_is_403(client, cases_dir):
    data = form(client, decision="approve")
    for bad in ({"origin": "https://evil.example"}, {"referer": "https://evil.example/page"}):
        r = client.post(POST, data=data, headers={**IDENTITY, "host": "testserver", **bad}, follow_redirects=False)
        assert r.status_code == 403
    assert not log_lines(cases_dir)


def test_local_mode_only_when_set(make_client, cases_dir):
    local = make_client(identity="local")
    html = local.get(PRD_PAGE).text
    assert 'name="approver"' in html and "Signing as" not in html
    data = {"phase_dir": "01-scope", **digests(html), "decision": "approve", "approver": "Dev Person"}
    r = local.post(POST, data=data, headers={"host": "testserver"}, follow_redirects=False)
    assert r.status_code == 303
    marker = json.loads(case_path(cases_dir, "01-scope", "APPROVED").read_text())
    assert marker["approver"] == "Dev Person" and marker["identity_source"] == "local"


def test_identity_header_is_configurable_and_sanitized(make_client, cases_dir):
    c = make_client(header="X-Forwarded-User, X-NetBird-User")
    data = form(c, headers={"X-Forwarded-User": "bo@example.org"}, decision="approve")
    assert post(c, data, headers={"X-Forwarded-User": "bo@example.org"}).status_code == 303
    assert json.loads(case_path(cases_dir, "01-scope", "APPROVED").read_text())["approver"] == "bo@example.org"
    assert ap.clean_identity("  a@b.c ") == "a@b.c"
    assert ap.clean_identity("bad\nname") is None and ap.clean_identity("x" * 201) is None


def test_log_failure_rolls_back_the_marker(client, cases_dir, monkeypatch):
    data = form(client, decision="approve")
    real_open = ap.os.open

    def failing_open(path, *a, **k):
        if str(path).endswith("decisions.jsonl"):
            raise OSError("disk full")
        return real_open(path, *a, **k)

    monkeypatch.setattr(ap.os, "open", failing_open)
    r = post(client, data)
    assert r.status_code == 500
    assert not case_path(cases_dir, "01-scope", "APPROVED").exists()


def test_concurrent_posts_record_one_decision(client, cases_dir):
    data = form(client, decision="approve")
    codes = []
    threads = [threading.Thread(target=lambda: codes.append(post(client, data).status_code)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes).count(303) == 1 and len(log_lines(cases_dir)) == 1


def test_run_id_is_the_paused_run_when_a_finished_proof_run_is_latest(client, cases_dir):
    """A separate proof run written after the case paused becomes `latest`; the decision still records the run that
    is paused at this checkpoint (the runner resumes that one: its active_run_id)."""
    runs = cases_dir / "libraries" / "lake" / "runs" / "fixture-libraries"
    (runs / "proof-0002").mkdir()
    (runs / "proof-0002" / "status.json").write_text(
        json.dumps({"run_id": "proof-0002", "state": "done", "phase": 5, "checkpoint_pending": None})
    )
    (runs / "latest.json").write_text(json.dumps({"run_id": "proof-0002"}))
    a = form(client, page="/cases/libraries/approvals/05-actions/req-0001", decision="deny", reason="not read-only")
    assert post(client, a).status_code == 303
    action = json.loads(case_path(cases_dir, "05-actions", "req-0001", "APPROVED").read_text())
    assert action["run_id"] == "run-libraries-0001"
    assert log_lines(cases_dir)[-1]["run_id"] == "run-libraries-0001"
