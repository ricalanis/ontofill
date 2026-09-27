"""sso-group identity: NetBird Cloud forwards group membership (x-netbird-groups), not the user; the name is
self-declared; direct mesh access (our own peers' addresses) cannot decide."""

import json

import pytest
from conftest import spec_for
from fastapi.testclient import TestClient
from test_approvals import POST, PRD_PAGE, case_path, digests, log_lines

from ontofill_console import approvals as ap
from ontofill_console.web import create_app, settings_from_env

GROUPS = {"X-NetBird-Groups": "admins, approvers"}
MESH = "100.82.17.158"  # a direct peer on our own mesh
PROXY = "100.82.93.149"  # a NetBird proxy address (not ours)


def app_client(cases_dir, client_addr=PROXY, deny=f"{MESH},100.82.76.174/32"):
    env = {"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir), "ONTOFILL_CONSOLE_IDENTITY": "sso-group",
           "ONTOFILL_CONSOLE_DIRECT_DENY": deny}
    return TestClient(create_app(settings_from_env(env)), client=(client_addr, 50000))


def post(c, data, headers):
    h = {**headers, "host": "testserver", "origin": "http://testserver"}
    return c.post(POST, data=data, headers=h, follow_redirects=False)


def good_form(c, **extra):
    html = c.get(PRD_PAGE, headers=GROUPS).text
    return {"phase_dir": "01-scope", **digests(html), "decision": "approve", **extra}


def nothing_written(cases_dir):
    return not case_path(cases_dir, "01-scope", "APPROVED").exists() and not log_lines(cases_dir)


def test_page_states_verified_group_and_asks_for_a_name(cases_dir):
    c = app_client(cases_dir)
    html = c.get(PRD_PAGE, headers=GROUPS).text
    assert "Verified: member of" in html and "name self-declared" in html and 'name="display_name"' in html
    html = c.get(PRD_PAGE).text
    assert "Not verified as a member of" in html and 'name="display_name"' not in html


def test_without_groups_header_is_403_and_writes_nothing(cases_dir):
    c = app_client(cases_dir)
    r = post(c, good_form(c, display_name="Ana"), headers={})
    assert r.status_code == 403 and "not signed in as a member of approvers" in r.text
    assert nothing_written(cases_dir)


def test_header_without_the_group_is_403(cases_dir):
    c = app_client(cases_dir)
    r = post(c, good_form(c, display_name="Ana"), headers={"X-NetBird-Groups": "admins, approvers-old"})
    assert r.status_code == 403 and nothing_written(cases_dir)


def test_member_decides_and_the_record_says_what_was_verified(cases_dir):
    c = app_client(cases_dir)
    r = post(c, good_form(c, display_name="  Ana Pérez ", approver="ignored@example.org"), headers=GROUPS)
    assert r.status_code == 303
    marker = json.loads(case_path(cases_dir, "01-scope", "APPROVED").read_text())
    assert marker["approver"] == "group:approvers" and marker["unverified_name"] == "Ana Pérez"
    assert marker["identity_source"] == "sso-group"
    assert marker["verified"] == {"group": "approvers", "via": "NetBird SSO (x-netbird-groups)"}
    assert marker["artifact_sha256"]
    [line] = log_lines(cases_dir)
    assert line["approver"] == "group:approvers" and line["unverified_name"] == "Ana Pérez"
    assert line["identity_source"] == "sso-group" and line["verified"]["group"] == "approvers"
    page = c.get("/cases/libraries/approvals/01-scope", headers=GROUPS).text
    assert "(self-declared" in page


def test_missing_or_bad_display_name_is_400(cases_dir):
    c = app_client(cases_dir)
    assert post(c, good_form(c), headers=GROUPS).status_code == 400
    assert post(c, good_form(c, display_name="bad\nname"), headers=GROUPS).status_code == 400
    assert post(c, good_form(c, display_name="x" * 101), headers=GROUPS).status_code == 400
    assert nothing_written(cases_dir)


def test_direct_mesh_address_cannot_decide_even_with_a_forged_header(cases_dir):
    c = app_client(cases_dir, client_addr=MESH)
    forged = {**GROUPS, "X-Forwarded-For": PROXY, "X-Real-IP": PROXY}
    r = post(c, good_form(c, display_name="Ana"), headers=forged)
    assert r.status_code == 403 and "must come through the NetBird proxy" in r.text
    assert nothing_written(cases_dir)
    assert "Not verified as a member of" in c.get(PRD_PAGE, headers=GROUPS).text


def test_digest_binding_still_applies(cases_dir):
    c = app_client(cases_dir)
    data = good_form(c, display_name="Ana")
    case_path(cases_dir, "01-scope", "prd.json").write_text('{"changed": true}')
    assert post(c, data, headers=GROUPS).status_code == 409 and nothing_written(cases_dir)


def test_whoami_reports_booleans_never_values_or_addresses(cases_dir):
    c = app_client(cases_dir, client_addr=MESH)
    sentinel = {"X-NetBird-Groups": "approvers, sentinel-group-zz9"}
    html = c.get("/whoami", headers=sentinel).text
    api = c.get("/api/whoami", headers=sentinel).json()
    assert "sentinel-group-zz9" not in html and "sentinel-group-zz9" not in json.dumps(api)
    assert MESH not in html and MESH not in json.dumps(api)
    assert api["groups_header_present"] is True and api["in_approver_group"] is True
    assert api["direct_denied"] is True and api["group_verified"] is False


def test_helpers():
    assert ap.groups_contain("admins, approvers", "approvers")
    assert not ap.groups_contain("approvers-old", "approvers") and not ap.groups_contain(None, "approvers")
    nets = ap.parse_networks("100.82.17.158, 10.0.0.0/8")
    assert ap.address_denied("100.82.17.158", nets) and ap.address_denied("10.1.2.3", nets)
    assert not ap.address_denied("100.82.93.149", nets) and not ap.address_denied("testclient", nets)
    with pytest.raises(ValueError):
        ap.parse_networks("not-an-ip")
    assert ap.clean_display_name(" Ana ") == "Ana" and ap.clean_display_name("") is None
