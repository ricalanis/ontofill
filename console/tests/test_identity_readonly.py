"""readonly identity: a public viewing instance (e.g. behind a shared password) shows every page and decides nothing,
whatever headers arrive — a forged approvers group header included."""

import pytest
from conftest import spec_for
from fastapi.testclient import TestClient
from test_approvals import POST, PRD_PAGE, case_path, digests, log_lines
from test_case_crud import root  # noqa: F401  (fixture)

from ontofill_console.web import create_app, settings_from_env

FORGED = {
    "X-NetBird-Groups": "approvers",
    "X-NetBird-User": "ana@example.org",
    "host": "testserver",
    "origin": "http://testserver",
}
PAGES = ("/", "/inbox", "/whoami", "/cases/libraries", PRD_PAGE, "/cases/libraries/approvals", "/cases/new")


@pytest.fixture
def viewer(root, cases_dir, tmp_path):  # noqa: F811
    (tmp_path / "runner").mkdir()
    env = {
        "ONTOFILL_CONSOLE_CASES": spec_for(cases_dir),
        "ONTOFILL_CONSOLE_IDENTITY": "readonly",
        "ONTOFILL_CASES_ROOT": str(root),
        "ONTOFILL_RUNNER_STATE": str(tmp_path / "runner"),
    }
    return TestClient(create_app(settings_from_env(env)), client=("100.82.93.149", 50000))


def test_pages_render_as_a_read_only_viewer_with_no_write_form(viewer):
    for page in (*PAGES, "/cases/libraries/manage"):
        r = viewer.get(page, headers=FORGED)
        assert r.status_code == 200, page
        html = r.text
        assert "read-only viewer" in html, page  # the masthead says what this console is
        assert 'method="post"' not in html, page  # no decision, runner, kill or case-edit form
        assert not any(f'name="{n}"' in html for n in ("decision", "action", "state", "question")), page
        assert "No signed-in identity" not in html and "sign-in URL" not in html, (
            page
        )  # not the misleading no-identity copy
        assert 'name="display_name"' not in html and 'name="approver"' not in html, page
    html = viewer.get(PRD_PAGE, headers=FORGED).text
    assert "Read-only console: approvals and runner actions are made on the approvers' console." in html
    assert "Waiting for an approver" in html
    assert 'href="/cases/new"' not in viewer.get("/", headers=FORGED).text
    assert "Cases are created on the approvers" in viewer.get("/cases/new", headers=FORGED).text
    assert "Read-only viewer" in viewer.get("/whoami", headers=FORGED).text


@pytest.mark.parametrize(
    "url,form",
    [
        (POST, {"phase_dir": "01-scope", "decision": "approve", "display_name": "Ana"}),
        ("/cases/libraries/runner", {"action": "start", "display_name": "Ana"}),
        ("/runner/kill", {"state": "on", "display_name": "Ana"}),
        ("/cases", {"title": "x", "question": "Which gardens are open?", "display_name": "Ana"}),
        ("/cases/libraries/brief", {"question": "Which gardens are open?", "display_name": "Ana"}),
        ("/cases/libraries/archive", {"display_name": "Ana"}),
    ],
)
def test_every_write_is_403_with_a_forged_group_header(viewer, cases_dir, tmp_path, url, form):
    if url == POST:
        form = {**form, **digests(viewer.get(PRD_PAGE, headers=FORGED).text)}
    for method in (viewer.post, viewer.put, viewer.delete):
        kwargs = {"data": form} if method != viewer.delete else {}
        r = method(url, headers=FORGED, follow_redirects=False, **kwargs)
        assert r.status_code == 403 and "read-only" in r.text, (url, method)
    assert not case_path(cases_dir, "01-scope", "APPROVED").exists() and not log_lines(cases_dir)
    assert not (tmp_path / "runner" / "KILL").exists()
    assert not list((tmp_path / "runner").rglob("control.json"))


def test_unknown_mode_is_refused(cases_dir):
    with pytest.raises(ValueError, match="readonly"):
        settings_from_env({"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir), "ONTOFILL_CONSOLE_IDENTITY": "judges"})
