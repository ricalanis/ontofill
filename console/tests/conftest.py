import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ontofill_console import fixtures
from ontofill_console.web import create_app, settings_from_env

IDENTITY = {"X-NetBird-User": "ana@example.org"}


@pytest.fixture(scope="session")
def fixture_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("console-fixtures")
    fixtures.generate(root)
    return root


@pytest.fixture
def cases_dir(fixture_root, tmp_path) -> Path:
    """A private, writable copy of both fixture cases (approval tests write APPROVED and decisions.jsonl)."""
    shutil.copytree(fixture_root, tmp_path / "cases")
    return tmp_path / "cases"


def spec_for(root: Path) -> str:
    return f"libraries={root / 'libraries' / 'case'}:{root / 'libraries' / 'lake'},parks={root / 'parks' / 'case'}"


@pytest.fixture
def make_client(cases_dir):
    def _make(identity: str = "sso", header: str | None = None) -> TestClient:
        env = {"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir), "ONTOFILL_CONSOLE_IDENTITY": identity}
        if header:
            env["ONTOFILL_CONSOLE_IDENTITY_HEADER"] = header
        return TestClient(create_app(settings_from_env(env)))

    return _make


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()
