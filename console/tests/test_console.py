"""Registry, routing, run view, files and bronze (CONTRACT §14)."""

import pytest
from conftest import IDENTITY

from ontofill_console.web import parse_cases

RUN = "run-libraries-0001"


def test_registry_parses_and_validates(cases_dir):
    cases = parse_cases(f"a={cases_dir / 'parks' / 'case'}")
    assert list(cases) == ["a"] and cases["a"].lake_error  # no lake.yaml next to it: the case still registers
    for bad in ("Bad=/x", "x", "a=/x,a=/y", "a" * 41 + "=/x"):
        with pytest.raises(ValueError):
            parse_cases(bad)


def test_cases_list_and_overview(client):
    home = client.get("/").text
    assert "Which public libraries are open" not in home  # the list shows titles, not briefs
    assert "/cases/libraries" in home and "/cases/parks" in home and "Public parks" in home
    over = client.get("/cases/libraries").text
    assert "Waiting for a decision" in over and "01-scope" in over and RUN in over
    assert client.get("/cases/nope").status_code == 404
    assert client.get("/cases/NOPE").status_code == 404


def test_run_view_renders_the_trail(client):
    page = client.get(f"/cases/libraries/runs/{RUN}", params={"limit": 2000}).text
    for needle in ("Phase 1 loop", "Reopened phase 3", "step--quarantine", "Sandbox proof", "verdict--achieved",
                   "Code attempt 1", "Approve-before-submit gate", "Watch the agent's browser",
                   'data-api="/cases/libraries/api/runs/', 'href="/cases/libraries/bronze/sha256'):
        assert needle in page, needle
    api = client.get(f"/cases/libraries/api/runs/{RUN}", params={"after": 0}).json()
    assert api["count"] > 20 and api["state"] == "paused" and "/cases/libraries/bronze/" in api["steps_html"]
    assert client.get("/cases/libraries/runs/run-nope").status_code == 404
    assert RUN in client.get("/cases/libraries/runs").text


def test_files_are_path_safe(client):
    assert client.get("/cases/libraries/files/01-scope/prd.json").status_code == 200
    for bad in ("../../etc/passwd", "..%2F..%2Fetc%2Fpasswd", "01-scope/../../lake/runs"):
        assert client.get(f"/cases/libraries/files/{bad}").status_code == 404


def test_bronze_serves_captures_safely(client):
    page = client.get(f"/cases/libraries/runs/{RUN}", params={"limit": 2000}).text
    key = page.split('href="/cases/libraries/bronze/', 1)[1].split('"', 1)[0]
    r = client.get(f"/cases/libraries/bronze/{key}")
    assert r.status_code == 200 and "sandbox" in r.headers["content-security-policy"]
    assert client.get("/cases/libraries/bronze/sha256:00").status_code == 404
    assert client.get("/cases/parks/bronze/" + key).status_code == 404  # another case's lake


def test_case_without_lake_still_serves_approvals(client):
    assert client.get("/cases/parks").status_code == 200
    assert "Sign off" in client.get("/cases/parks/approvals/01-scope", headers=IDENTITY).text


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True, "cases": ["libraries", "parks"], "identity": "sso"}


def test_whoami_never_shows_header_values(make_client):
    c = make_client(header="X-Forwarded-User, X-NetBird-User")
    sentinel = "SENTINEL-VALUE-7f3a"
    headers = {"X-NetBird-User": sentinel, "X-Extra": sentinel + "-2", "Cookie": "session=" + sentinel}
    for url in ("/whoami", "/api/whoami", "/", "/cases/libraries"):
        r = c.get(url, headers=headers)
        assert r.status_code == 200 and sentinel not in r.text, url
    api = c.get("/api/whoami", headers=headers).json()
    assert "x-netbird-user" in api["header_names"] and "x-extra" in api["header_names"]
    assert api["used_header"] == "X-NetBird-User" and api["identity_detected"] is True
    first = api["candidates"][0]
    assert first == {"name": "X-Forwarded-User", "present": False, "non_empty": False, "usable": False}
    page = c.get("/whoami", headers=headers).text
    assert "Signing as: (identity detected, value hidden)" in page and 'href="/whoami"' in page
    none = c.get("/api/whoami").json()
    assert none["identity_detected"] is False and none["used_header"] is None


def test_case_without_gold_uses_its_run_feed_and_renders(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from ontofill_console import web

    case = tmp_path / "c" / "case"
    (case / "01-scope").mkdir(parents=True)
    (case / "brief.md").write_text("# A paused case\n")
    lake = tmp_path / "c" / "lake"
    (lake / "runs" / "paused-case" / "run-1").mkdir(parents=True)
    (lake / "runs" / "paused-case" / "latest.json").write_text('{"run_id": "run-1"}')
    (lake / "runs" / "paused-case" / "run-1" / "status.json").write_text('{"run_id": "run-1", "state": "paused"}')
    monkeypatch.setenv("ONTOFILL_CONSOLE_CASES", f"paused={case}:{lake}")
    monkeypatch.setenv("ONTOFILL_CONSOLE_IDENTITY", "local")
    client = TestClient(web.create_app())
    assert client.get("/cases/paused").status_code == 200
    assert client.get("/cases/paused/approvals").status_code == 200
