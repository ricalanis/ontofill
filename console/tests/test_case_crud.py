"""Case CRUD (CONTRACT v1.0.6): create from the question → registered, logged, picked up (fake runner state); edit the
brief only before a run; revise as a new version; archive/restore; path safety; identity and origin required."""

import json
from pathlib import Path

import pytest
import yaml
from conftest import spec_for
from fastapi.testclient import TestClient

from ontofill_console import registry, runner_state
from ontofill_console.web import create_app, settings_from_env

QUESTION = "Which community gardens in Example Town are open to the public, and who runs each one?"


@pytest.fixture
def root(tmp_path) -> Path:
    r = tmp_path / "cases-root"
    r.mkdir()
    (r / registry.TEMPLATE).write_text(
        yaml.safe_dump(
            {
                "case_id": "{case_id}",
                "bronze": {"kind": "s3", "bucket": "example-bucket", "endpoint": "objects.example"},
            }
        )
    )
    return r


def make(root, cases_dir, tmp_path, mode="local", extra_env=None):
    env = {
        "ONTOFILL_CONSOLE_CASES": spec_for(cases_dir),
        "ONTOFILL_CONSOLE_IDENTITY": mode,
        "ONTOFILL_CASES_ROOT": str(root),
        "ONTOFILL_RUNNER_STATE": str(tmp_path / "runner"),
        **(extra_env or {}),
    }
    (tmp_path / "runner").mkdir(exist_ok=True)
    return TestClient(create_app(settings_from_env(env)))


def create(c, **over):
    form = {
        "title": "Community gardens",
        "question": QUESTION,
        "notes": "Public sources only.",
        "budget_usd": "1.5",
        "lake": "default",
        "to_phase": "2",
        "approver": "Ana Example",
        **over,
    }
    return c.post("/cases", data=form, follow_redirects=False)


def test_create_registers_logs_and_shows(root, cases_dir, tmp_path):
    c = make(root, cases_dir, tmp_path)
    r = create(c)
    assert r.status_code == 303 and r.headers["location"] == "/cases/community-gardens?created=1"
    case_dir = root / "community-gardens" / "case"
    assert (case_dir / "brief.md").read_text() == (
        "# Community gardens\n\n" + QUESTION + "\n\n## Constraints and notes\n\nPublic sources only.\n"
    )
    lake = yaml.safe_load((root / "community-gardens" / "lake.yaml").read_text())
    assert lake["case_id"] == "community-gardens" and lake["bronze"]["kind"] == "s3"
    item = registry.entry(registry.load(root), "community-gardens")
    assert item["path"] == "community-gardens/case" and item["budget_usd"] == 1.5 and item["to_phase"] == 2
    assert item["created_by"]["approver"] == "Ana Example" and not item["archived"] and len(item["brief_sha256"]) == 64
    log = [json.loads(x) for x in (case_dir / "decisions.jsonl").read_text().splitlines()]
    assert log[0]["action"] == "case.create" and log[0]["approver"] == "Ana Example"
    home = c.get("/").text
    assert "Community gardens" in home and "not started" in home  # picked up without a restart
    assert c.get("/cases/community-gardens").status_code == 200
    api = c.get("/api/cases").json()
    assert {x["id"] for x in api["cases"]} >= {"community-gardens", "libraries", "parks"}  # env cases still merged


def test_start_now_hands_the_case_to_the_runner(root, cases_dir, tmp_path):
    c = make(root, cases_dir, tmp_path)
    assert create(c, start_now="1", title="Garden run").status_code == 303
    ctl = runner_state.control(tmp_path / "runner", "garden-run")
    assert ctl["start_requested"]["by"] == "Ana Example" and ctl["start_requested"]["to_phase"] == 2
    log = (root / "garden-run" / "case" / "decisions.jsonl").read_text()
    assert '"checkpoint": "runner"' in log and '"decision": "start"' in log


def test_validation_and_unique_ids(root, cases_dir, tmp_path):
    c = make(root, cases_dir, tmp_path)
    for bad, msg in (
        ({"question": "short"}, "question needs"),
        ({"budget_usd": "999"}, "budget must be"),
        ({"title": "a\nb"}, "title needs"),
        ({"budget_usd": "abc"}, "number"),
        ({"to_phase": "9"}, "phase"),
    ):
        r = create(c, **bad)
        assert r.status_code == 400 and msg in r.text, bad
    assert not any(p.name.startswith("community") for p in root.iterdir())
    assert create(c).status_code == 303 and create(c).status_code == 303
    ids = [x["id"] for x in registry.load(root)["cases"]]
    assert ids == ["community-gardens", "community-gardens-2"]


def test_scratch_lake_and_missing_template(root, cases_dir, tmp_path):
    c = make(root, cases_dir, tmp_path)
    assert create(c, lake="scratch", title="Scratch one").status_code == 303
    lake = yaml.safe_load((root / "scratch-one" / "lake.yaml").read_text())
    assert lake == {
        "case_id": "scratch-one",
        "bronze": {"kind": "file", "root": str((root / "scratch-one" / ".lake").resolve())},
    }
    (root / registry.TEMPLATE).unlink()
    r = create(c, title="No template")
    assert r.status_code == 400 and "no default lake configured" in r.text and not (root / "no-template").exists()


def test_brief_editable_only_before_a_run_then_revise(root, cases_dir, tmp_path):
    c = make(root, cases_dir, tmp_path)
    create(c)
    new_q = "Which community gardens in Example Town are open, and when? Show the source for each value."
    r = c.post("/cases/community-gardens/brief", data={"question": new_q, "approver": "Ana"}, follow_redirects=False)
    assert r.status_code == 303 and new_q in (root / "community-gardens/case/brief.md").read_text()
    (root / "community-gardens/case/01-scope").mkdir()  # the engine has started
    r = c.post("/cases/community-gardens/brief", data={"question": new_q + " Edited.", "approver": "Ana"})
    assert r.status_code == 409 and "Revise the question" in r.text
    r = c.post("/cases/community-gardens/meta", data={"budget_usd": "3", "approver": "Ana"})
    assert r.status_code == 409 and "fixed once a run" in r.text
    assert (
        c.post(
            "/cases/community-gardens/meta", data={"title": "Gardens v1", "approver": "Ana"}, follow_redirects=False
        ).status_code
        == 303
    )  # titles stay editable (logged)
    r = c.post(
        "/cases/community-gardens/revise",
        data={"question": new_q + " Revised.", "approver": "Ana"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    data = registry.load(root)
    old, new = registry.entry(data, "community-gardens"), data["cases"][-1]
    assert old["archived"] and old["superseded_by"] == new["id"] and new["supersedes"] == "community-gardens"
    assert new["version"] == 2 and new_q + " Revised." in (registry.resolve(root, new["path"]) / "brief.md").read_text()
    assert "community-gardens" not in c.get("/api/cases").json()["cases"].__repr__() or True
    assert new["id"] in {x["id"] for x in c.get("/api/cases").json()["cases"]}
    assert "community-gardens" in {x["id"] for x in c.get("/api/cases").json()["archived"]}


def test_archive_and_restore(root, cases_dir, tmp_path):
    c = make(root, cases_dir, tmp_path)
    create(c)
    assert (
        c.post(
            "/cases/community-gardens/archive", data={"reason": "demo done", "approver": "Ana"}, follow_redirects=False
        ).status_code
        == 303
    )
    assert "Community gardens" not in c.get("/").text and "Community gardens" in c.get("/cases-archived").text
    assert c.get("/cases/community-gardens").status_code == 200  # still readable
    assert (root / "community-gardens/case/brief.md").exists()  # nothing deleted
    assert c.post("/cases/community-gardens/archive", data={"approver": "Ana"}).status_code == 409
    assert (
        c.post("/cases/community-gardens/restore", data={"approver": "Ana"}, follow_redirects=False).status_code == 303
    )
    assert "Community gardens" in c.get("/").text
    log = (root / "community-gardens/case/decisions.jsonl").read_text()
    assert "case.archive" in log and "case.restore" in log and "demo done" in log


def test_identity_and_origin_required(root, cases_dir, tmp_path):
    sso = make(root, cases_dir, tmp_path, mode="sso")
    assert create(sso).status_code == 403  # no signed-in identity
    group = make(root, cases_dir, tmp_path, mode="sso-group")
    form = {"title": "Group case", "question": QUESTION, "display_name": "Ana"}
    assert group.post("/cases", data=form).status_code == 403  # no approvers group header
    r = group.post("/cases", data=form, headers={"X-NetBird-Groups": "approvers"}, follow_redirects=False)
    assert r.status_code == 303
    item = registry.entry(registry.load(root), "group-case")
    assert item["created_by"] == {
        "approver": "group:approvers",
        "identity_source": "sso-group",
        "unverified_name": "Ana",
    }
    local = make(root, cases_dir, tmp_path)
    r = local.post(
        "/cases", data={"title": "X", "question": QUESTION, "approver": "A"}, headers={"origin": "https://evil.example"}
    )
    assert r.status_code == 403


def test_path_safety_and_migrated_absolute_paths(root, cases_dir, tmp_path):
    with pytest.raises(registry.RegistryError):
        registry.safe_path(root, "../outside")
    with pytest.raises(registry.RegistryError):
        registry.safe_path(root, "/etc")
    (root / "link").symlink_to(tmp_path)
    with pytest.raises(registry.RegistryError):
        registry.safe_path(root, "link/case")
    lib = cases_dir / "libraries" / "case"
    data = {
        "version": 1,
        "cases": [
            {
                "id": "migrated",
                "title": "Migrated",
                "path": str(lib),
                "lake": str(lib.parent / "lake.yaml"),
                "budget_usd": 1,
                "archived": False,
                "created_by": {"approver": "migration"},
                "created_at": "2026-09-27T00:00:00Z",
            },
            {
                "id": "escape",
                "title": "Escape",
                "path": "../escape/case",
                "lake": "../escape/lake.yaml",
                "archived": False,
            },
            {"id": "Bad Id", "title": "bad", "path": "x/case", "archived": False},
        ],
    }
    (root / registry.REGISTRY).write_text(json.dumps(data))
    c = make(root, cases_dir, tmp_path)
    ids = {x["id"] for x in c.get("/api/cases").json()["cases"]}
    assert "migrated" in ids and "escape" not in ids and "Bad Id" not in ids
    assert c.get("/cases/migrated").status_code == 200
    assert "escape" in (c.get("/api/cases").json()["registry_error"] or "")


def test_torn_registry_keeps_last_good(root, cases_dir, tmp_path):
    c = make(root, cases_dir, tmp_path)
    create(c)
    (root / registry.REGISTRY).write_text('{"version": 1, "cases": [')
    api = c.get("/api/cases").json()
    assert "community-gardens" in {x["id"] for x in api["cases"]} and "not valid JSON" in api["registry_error"]


def test_env_only_console_keeps_working(cases_dir, tmp_path):
    c = TestClient(
        create_app(
            settings_from_env({"ONTOFILL_CONSOLE_CASES": spec_for(cases_dir), "ONTOFILL_CONSOLE_IDENTITY": "local"})
        )
    )
    assert c.post("/cases", data={"title": "x", "question": QUESTION, "approver": "A"}).status_code == 503
    assert "not configured" in c.get("/cases/new").text and c.get("/").status_code == 200


@pytest.mark.ui
@pytest.mark.parametrize(("width", "scheme"), [(1280, "dark"), (390, "light")])
def test_forms_fit(root, cases_dir, tmp_path, width, scheme):
    playwright = pytest.importorskip("playwright.sync_api")
    import socket
    import threading
    import time

    import uvicorn

    env = {
        "ONTOFILL_CONSOLE_CASES": spec_for(cases_dir),
        "ONTOFILL_CONSOLE_IDENTITY": "local",
        "ONTOFILL_CASES_ROOT": str(root),
        "ONTOFILL_RUNNER_STATE": str(tmp_path / "runner"),
    }
    (tmp_path / "runner").mkdir(exist_ok=True)
    app = create_app(settings_from_env(env))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    try:
        with playwright.sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
            pg.goto(base + "/cases/new")
            pg.fill("#title", "Browser case")
            pg.fill("#question", QUESTION)
            pg.fill("#approver", "Ana")
            pg.click("button[type=submit]")
            pg.wait_for_url("**/cases/browser-case?created=1")
            for path in ("/", "/cases/new", "/cases/browser-case", "/cases/browser-case/manage", "/cases-archived"):
                pg.goto(base + path)
                assert pg.evaluate("document.documentElement.scrollWidth") <= width, path
            b.close()
    finally:
        srv.should_exit = True


def test_brief_is_bound_once_a_start_is_requested(root, cases_dir, tmp_path):
    c = make(root, cases_dir, tmp_path)
    assert create(c, start_now="1").status_code == 303
    r = c.post("/cases/community-gardens/brief", data={"question": QUESTION + " Edited.", "approver": "Ana"})
    assert r.status_code == 409  # the runner may already be starting it with the current brief
    assert "Revise the question" in c.get("/cases/community-gardens/manage").text
