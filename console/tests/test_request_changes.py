"""Reopen an approved checkpoint ("Request changes"): the approval becomes a deny in the normal deny shape, bound to
the current artifact bytes, so the engine's deny path redrafts on its next run. Approvers only; readonly consoles
and foreign origins are refused, and a refusal changes nothing."""

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from conftest import spec_for
from fastapi.testclient import TestClient
from test_approvals import POST, PRD_PAGE, case_path, digests, log_lines

from ontofill_console.web import create_app, settings_from_env

GROUPS = {"X-NetBird-Groups": "approvers"}
REOPEN = "/cases/libraries/approvals/request-changes"
SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "approved.schema.json"
REASON = "Add ComprasMX / Buen Gobierno as the PRIMARY federal procurement publisher."


def client(cases_dir, identity="sso-group"):
    env = {"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir), "ONTOFILL_CONSOLE_IDENTITY": identity}
    return TestClient(create_app(settings_from_env(env)), client=("100.82.93.149", 50000))


def post(c, url, data, origin="http://testserver"):
    h = {**GROUPS, "host": "testserver", **({"origin": origin} if origin else {})}
    return c.post(url, data=data, headers=h, follow_redirects=False)


def approve_prd(c):
    form = {
        "phase_dir": "01-scope",
        "decision": "approve",
        "display_name": "Ana",
        **digests(c.get(PRD_PAGE, headers=GROUPS).text),
    }
    assert post(c, POST, form).status_code == 303


def reopen_form(c, **extra):
    return {
        "phase_dir": "01-scope",
        "display_name": "Ana",
        "reason": REASON,
        **digests(c.get(PRD_PAGE, headers=GROUPS).text),
        **extra,
    }


def test_request_changes_turns_the_approval_into_a_bound_deny(cases_dir):
    c = client(cases_dir)
    approve_prd(c)
    page = c.get(PRD_PAGE, headers=GROUPS).text
    assert "Request changes to this approved PRD" in page and "/approvals/request-changes" in page
    old = case_path(cases_dir, "01-scope", "APPROVED").read_bytes()
    r = post(c, REOPEN, reopen_form(c))
    assert r.status_code == 303, r.text
    marker = json.loads(case_path(cases_dir, "01-scope", "APPROVED").read_text())
    assert marker["decision"] == "deny" and marker["reason"] == REASON and marker["checkpoint"] == "prd"
    assert marker["identity_source"] == "sso-group" and marker["unverified_name"] == "Ana"
    root = cases_dir / "libraries" / "case"
    # bound to every artifact the checkpoint names, exactly as a normal decision (current bytes)
    assert marker["artifact_sha256"] and all(
        hashlib.sha256((root / rel).read_bytes()).hexdigest() == d for rel, d in marker["artifact_sha256"].items()
    )
    assert "01-scope/prd.json" in marker["artifact_sha256"]
    jsonschema.validate(marker, json.loads(SCHEMA.read_text()))  # the engine's own APPROVED schema
    superseded = list(case_path(cases_dir, "01-scope").glob("APPROVED.superseded.*"))
    assert len(superseded) == 1 and superseded[0].read_bytes() == old
    line = log_lines(cases_dir)[-1]
    assert line["reopened"] is True and line["decision"] == "deny" and line["reason"] == REASON
    assert line["superseded_approval_sha256"] == hashlib.sha256(old).hexdigest()
    assert "Request changes to this approved" not in c.get(PRD_PAGE, headers=GROUPS).text  # now a deny


@pytest.mark.parametrize("identity,origin", [("readonly", "http://testserver"), ("sso-group", "https://evil.example")])
def test_readonly_or_foreign_origin_is_403_and_changes_nothing(cases_dir, identity, origin):
    c = client(cases_dir)
    approve_prd(c)
    before = case_path(cases_dir, "01-scope", "APPROVED").read_bytes()
    target = client(cases_dir, identity) if identity == "readonly" else c
    r = post(target, REOPEN, reopen_form(c), origin=origin)
    assert r.status_code == 403
    assert case_path(cases_dir, "01-scope", "APPROVED").read_bytes() == before
    assert not list(case_path(cases_dir, "01-scope").glob("APPROVED.superseded.*"))
    if identity == "readonly":
        assert "Request changes" not in client(cases_dir, "readonly").get(PRD_PAGE).text


def test_no_reason_stale_digest_or_unapproved_is_refused(cases_dir):
    c = client(cases_dir)
    assert post(c, REOPEN, reopen_form(c)).status_code == 409  # not approved yet
    approve_prd(c)
    before = case_path(cases_dir, "01-scope", "APPROVED").read_bytes()
    assert post(c, REOPEN, reopen_form(c, reason="  ")).status_code == 400
    stale = {**reopen_form(c), "artifact_sha256.01-scope/prd.json": "0" * 64}
    assert post(c, REOPEN, stale).status_code == 409
    assert case_path(cases_dir, "01-scope", "APPROVED").read_bytes() == before
