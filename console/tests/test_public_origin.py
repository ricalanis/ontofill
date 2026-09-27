"""R23: behind the NetBird reverse proxy the console sees the upstream Host, while the browser's Origin is the public
hostname. The CSRF check accepts the configured public origins (ONTOFILL_CONSOLE_PUBLIC_ORIGINS) and nothing else;
forwarded-host headers are never trusted."""

import pytest
from conftest import spec_for
from fastapi.testclient import TestClient
from test_approvals import POST, PRD_PAGE, case_path, digests, log_lines
from test_identity_group import GROUPS, PROXY

from ontofill_console.web import create_app, settings_from_env

PUBLIC = "https://ontofill-console.eu1.netbird.services"
UPSTREAM = "100.82.76.174:8410"  # the Host the proxy sends (pass_host_header off)


def client(cases_dir, origins=PUBLIC, identity="sso-group"):
    env = {
        "ONTOFILL_CONSOLE_CASES": spec_for(cases_dir),
        "ONTOFILL_CONSOLE_IDENTITY": identity,
        "ONTOFILL_CONSOLE_PUBLIC_ORIGINS": origins,
    }
    return TestClient(create_app(settings_from_env(env)), client=(PROXY, 50000))


def decide(c, origin=None, referer=None, extra=None):
    form = {
        "phase_dir": "01-scope",
        "decision": "approve",
        "display_name": "Ana",
        **digests(c.get(PRD_PAGE, headers=GROUPS).text),
    }
    headers = {
        **GROUPS,
        "host": UPSTREAM,
        **({"origin": origin} if origin else {}),
        **({"referer": referer} if referer else {}),
        **(extra or {}),
    }
    return c.post(POST, data=form, headers=headers, follow_redirects=False)


def approved(cases_dir):
    return case_path(cases_dir, "01-scope", "APPROVED").exists()


def test_the_public_origin_behind_the_proxy_decides(cases_dir):
    r = decide(client(cases_dir), origin=PUBLIC, referer=f"{PUBLIC}/cases/libraries/approvals/01-scope")
    assert r.status_code == 303 and approved(cases_dir) and len(log_lines(cases_dir)) == 1


def test_without_the_setting_the_proxy_origin_is_refused_as_before(cases_dir):
    r = decide(client(cases_dir, origins=""), origin=PUBLIC)
    assert r.status_code == 403 and "cross-origin" in r.text and not approved(cases_dir)


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://ontofill-console.eu1.netbird.services",
        "https://ontofill-console.eu1.netbird.services.evil.example",
    ],
)
def test_foreign_origins_are_refused(cases_dir, origin):
    r = decide(client(cases_dir), origin=origin)
    assert r.status_code == 403 and not approved(cases_dir) and not log_lines(cases_dir)


def test_a_foreign_referer_is_refused_even_with_the_public_origin(cases_dir):
    r = decide(client(cases_dir), origin=PUBLIC, referer="https://evil.example/page")
    assert r.status_code == 403 and not approved(cases_dir)


def test_forwarded_host_headers_are_not_trusted(cases_dir):
    forged = {"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"}
    r = decide(client(cases_dir, origins=""), origin="https://evil.example", extra=forged)
    assert r.status_code == 403 and not approved(cases_dir)


def test_readonly_still_refuses_with_the_public_origin(cases_dir):
    r = decide(client(cases_dir, identity="readonly"), origin=PUBLIC)
    assert r.status_code == 403 and "read-only" in r.text and not approved(cases_dir)


@pytest.mark.parametrize("bad", ["ontofill-console.eu1.netbird.services", "https://host/path", "ftp://host"])
def test_bad_origin_settings_are_refused_at_start(cases_dir, bad):
    with pytest.raises(ValueError, match="PUBLIC_ORIGINS"):
        settings_from_env({"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir), "ONTOFILL_CONSOLE_PUBLIC_ORIGINS": bad})
