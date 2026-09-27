"""R34: the engine's source-review checkpoint (R33: an unknown publisher or redirect host pauses P3 with
03-fanout/sources/<id>/candidate.json + APPROVAL_PENDING.md). The console lists it, shows it, and records a decision
bound to the candidate's digest and the engine's source_fingerprint, in the shape the engine's schema accepts."""

import json
from pathlib import Path

import jsonschema
import pytest
import yaml
from conftest import spec_for
from fastapi.testclient import TestClient
from test_approvals import POST, case_path, digests, log_lines

from ontofill_console.web import create_app, settings_from_env

GROUPS = {"X-NetBird-Groups": "approvers"}
SRC = "03-fanout/sources/source-gob-mx"
PAGE = f"/cases/libraries/approvals/{SRC}"
FP = "a" * 64
SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "approved.schema.json"


def write_source_request(cases_dir, fp=FP, candidate_fp=None):
    d = case_path(cases_dir, *SRC.split("/"))
    d.mkdir(parents=True)
    candidate = {
        "source_id": "source-gob-mx",
        "url": "https://www.economia.gob.mx/padron",
        "landing_url": "https://www.gob.mx/se/padron",
        "redirect_chain": ["https://www.economia.gob.mx/padron", "https://www.gob.mx/se/padron"],
        "title": "Padrón",
        "source_type": "government_registry",
        "authority": "review",
        "authority_tier": "primary",
        "authority_reason": "redirect host www.gob.mx is not a named publisher",
        "fingerprint": candidate_fp or fp,
        "covers": ["supplier_name"],
        "capture_key": "bronze/sha256/" + "b" * 64,
        "generated_by": {"backend": "vultr", "model": "m", "at": "2026-09-27T08:30:00+00:00", "run_id": "run-src"},
    }
    (d / "candidate.json").write_text(json.dumps(candidate))
    meta = {
        "phase": 3,
        "checkpoint": "source",
        "requested_at": "2026-09-27T08:30:00+00:00",
        "reason": "Human review required for source before continuing",
        "artifact_paths": [f"{SRC}/candidate.json"],
        "generated_by": candidate["generated_by"],
        "source_fingerprint": fp,
    }
    (d / "APPROVAL_PENDING.md").write_text(
        "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n# Approval pending: source\n"
    )
    return d


def client(cases_dir, identity="sso-group"):
    env = {"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir), "ONTOFILL_CONSOLE_IDENTITY": identity}
    return TestClient(create_app(settings_from_env(env)), client=("100.82.93.149", 50000))


def decide(c, **extra):
    html = c.get(PAGE, headers=GROUPS).text
    data = {"phase_dir": SRC, **digests(html), "display_name": "Ana", **extra}
    h = {**GROUPS, "host": "testserver", "origin": "http://testserver"}
    return c.post(POST, data=data, headers=h, follow_redirects=False)


def test_listed_and_reviewable(cases_dir):
    write_source_request(cases_dir)
    c = client(cases_dir)
    assert c.get(PAGE, headers=GROUPS).status_code == 200
    assert f"artifact_sha256.{SRC}/candidate.json" in c.get(PAGE, headers=GROUPS).text
    listing = c.get("/cases/libraries/approvals", headers=GROUPS).text
    assert SRC in listing
    inbox = c.get("/api/viz/inbox", headers=GROUPS).json()
    assert any(SRC in (s.get("href") or "") for s in inbox["strips"])


def test_approve_binds_digest_and_fingerprint_in_the_engines_shape(cases_dir):
    d = write_source_request(cases_dir)
    r = decide(client(cases_dir))
    assert r.status_code == 303, r.text
    marker = json.loads((d / "APPROVED").read_text())
    assert marker["checkpoint"] == "source" and marker["source_fingerprint"] == FP
    assert set(marker["artifact_sha256"]) == {f"{SRC}/candidate.json"} and marker["run_id"] == "run-src"
    jsonschema.validate(marker, json.loads(SCHEMA.read_text()))
    # the engine's own acceptance rule (discovery_loop._source_approved)
    assert marker.get("decision", "approve") != "deny" and marker["source_fingerprint"] == FP
    [line] = log_lines(cases_dir)
    assert line["checkpoint"] == "source" and line["source_fingerprint"] == FP and line["phase_dir"] == SRC


def test_deny_needs_a_reason_and_is_recorded(cases_dir):
    d = write_source_request(cases_dir)
    c = client(cases_dir)
    assert decide(c, decision="deny").status_code == 400 and not (d / "APPROVED").exists()
    assert decide(c, decision="deny", reason="a news aggregator, not a publisher").status_code == 303
    marker = json.loads((d / "APPROVED").read_text())
    assert marker["decision"] == "deny" and marker["source_fingerprint"] == FP
    jsonschema.validate(marker, json.loads(SCHEMA.read_text()))


@pytest.mark.parametrize("fp,candidate_fp", [("not-a-fingerprint", None), (FP, "c" * 64)])
def test_a_missing_or_mismatched_fingerprint_is_refused(cases_dir, fp, candidate_fp):
    d = write_source_request(cases_dir, fp=fp, candidate_fp=candidate_fp)
    r = decide(client(cases_dir))
    assert r.status_code == 409 and not (d / "APPROVED").exists() and not log_lines(cases_dir)


def test_readonly_console_cannot_decide_it(cases_dir):
    d = write_source_request(cases_dir)
    c = client(cases_dir, identity="readonly")
    assert c.get(PAGE, headers=GROUPS).status_code == 200
    assert decide(c).status_code == 403 and not (d / "APPROVED").exists()


def test_source_review_shows_what_an_approver_decides(cases_dir):
    write_source_request(cases_dir)
    html = client(cases_dir).get(PAGE, headers=GROUPS).text
    assert '<p class="src-host"><span class="mono">www.gob.mx</span>' in html  # where it really lands
    assert 'requested at <span class="mono">www.economia.gob.mx</span>' in html
    chain = html.split('class="src-chain"', 1)[1].split("</ol>", 1)[0]
    assert chain.index("www.economia.gob.mx") < chain.index("www.gob.mx")  # the redirect chain in order
    assert "government_registry" in html and "primary: can back values on its own" in html
    assert "redirect host www.gob.mx is not a named publisher" in html
    assert "supplier_name" in html
    assert f"{FP[:12]}…" in html  # the fingerprint the decision binds
    assert "bronze%2Fsha256%2F" in html  # the capture, through the bronze route
    assert "Deny, with a reason, to have the engine skip it" in html and "<h1>Review: Source</h1>" in html
    assert 'value="deny"' in html and 'name="reason"' in html


def test_readonly_source_review_has_no_form(cases_dir):
    write_source_request(cases_dir)
    html = client(cases_dir, identity="readonly").get(PAGE).text
    assert "Source to review" in html and "www.gob.mx" in html
    assert 'method="post"' not in html and 'value="deny"' not in html


def test_unknown_tier_and_kind_read_as_not_suggested(cases_dir):
    d = write_source_request(cases_dir)
    c = json.loads((d / "candidate.json").read_text())
    c.update(authority_tier="unknown", source_type=None)
    (d / "candidate.json").write_text(json.dumps(c))
    html = client(cases_dir).get(PAGE, headers=GROUPS).text
    assert "none suggested: the engine could not classify this publisher" in html and "not classified" in html
    assert "<b>unknown</b>" not in html
