"""The masthead shows the approver GROUP (CONTRACT v1.0.4a) or a mode, never an email or a header value."""

import re

from conftest import spec_for
from fastapi.testclient import TestClient

from ontofill_console.web import create_app, settings_from_env

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)*")


def _client(cases_dir, mode):
    return TestClient(
        create_app(
            settings_from_env({"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir), "ONTOFILL_CONSOLE_IDENTITY": mode})
        )
    )


def _masthead(html):
    return html.split('<header class="masthead">', 1)[1].split("</header>", 1)[0]


def test_masthead_never_shows_an_email(cases_dir):
    for mode, headers in (
        ("sso", {"X-NetBird-User": "ana@example.org"}),
        ("sso-group", {"X-NetBird-Groups": "approvers"}),
        ("local", {}),
    ):
        head = _masthead(_client(cases_dir, mode).get("/inbox", headers=headers).text)
        assert not EMAIL.search(head), (mode, head)
        assert "ana@" not in head


def test_sso_group_shows_the_group(cases_dir):
    head = _masthead(_client(cases_dir, "sso-group").get("/", headers={"X-NetBird-Groups": "approvers"}).text)
    assert "group:" in head and ("names self-declared" in head or "read-only" in head)
