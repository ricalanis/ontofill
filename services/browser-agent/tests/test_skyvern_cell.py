"""Skyvern cells with the docker CLI and Skyvern's HTTP API faked. The live test runs a real cell (opt-in)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
import respx

from backends.skyvern import cell as cellmod
from backends.skyvern.backend import SkyvernBackend, SkyvernClient, host_allowed
from backends.skyvern.cell import CellError, CellSpec, SkyvernCell, create_skyvern_cell, ensure_postgres
from shared.steps import StepLog

TOKEN = "gw-session-token-abc123"
API_KEY = "sky-api-key-xyz"
PORT = 49999
API = f"http://127.0.0.1:{PORT}/api/v1"


class FakeDocker:
    """Records docker invocations; captures env-file contents at `create` time (the file is deleted after)."""

    def __init__(self, pg_ready_after: int = 0):
        self.calls: list[list[str]] = []
        self.env_files: list[str] = []
        self.pg_ready_after = pg_ready_after

    def __call__(self, args, *, check=True, input=None):
        args = list(args)
        self.calls.append(args)
        if args[0] == "create" and "--env-file" in args:
            self.env_files.append(Path(args[args.index("--env-file") + 1]).read_text())
        if args[:2] in (["network", "ls"], ["ps", "-a"]):
            return ""
        if args[0] == "port":
            return f"127.0.0.1:{PORT}\n"
        if args[0] == "inspect":
            return "running\n"
        if args[0] == "exec" and args[-1] == "/app/.skyvern/credentials.toml":
            return f'[skyvern]\nconfigs = [{{"env" = "local", "orgs" = [{{name="Skyvern", cred="{API_KEY}"}}]}}]\n'
        if args[0] == "exec" and args[-1] == "select 1":
            self.pg_ready_after -= 1
            return "1\n" if self.pg_ready_after < 0 else ""
        if args[0] == "exec" and "alembic_version" in args[-1]:
            return "2dda9d1255a6\n"
        return "ok\n"

    def named(self, verb):
        return [c for c in self.calls if c[0] == verb]


def spec(**kw):
    return CellSpec(cell_id=kw.pop("cell_id", "c0"), gateway_base="http://host.docker.internal:8700/v1",
                    subnet_index=kw.pop("subnet_index", 101), **kw)


@pytest.fixture
def docker():
    return FakeDocker()


@pytest.fixture
def api():
    with respx.mock(assert_all_called=False) as r:
        r.get(f"{API}/heartbeat").mock(return_value=httpx.Response(200, json={}))
        yield r


def started(docker, **kw):
    return SkyvernCell(spec(**kw), docker=docker, sleep=lambda s: None).start(TOKEN)


def test_start_applies_limits_and_stable_hands_ip(docker, api):
    c = started(docker)
    hands = next(a for a in docker.named("run") if "/hands.sh" in a)
    for flag, val in (("--memory", "1g"), ("--cpus", "1"), ("--pids-limit", "512"), ("--ip", "172.30.101.10")):
        assert hands[hands.index(flag) + 1] == val
    brain = docker.named("create")[0]
    for flag, val in (("--memory", "3g"), ("--cpus", "2"), ("--pids-limit", "1024"), ("-p", "127.0.0.1::8000")):
        assert brain[brain.index(flag) + 1] == val
    assert ["network", "create", "--subnet", "172.30.101.0/24", "ba-sky-c0"] in docker.calls
    assert ["network", "connect", "ba-sky-db", "ba-sky-c0-brain"] in docker.calls
    assert c.info() == {"cell_id": "c0", "backend": "skyvern", "cdp_url": "http://172.30.101.10:9222",
                        "brain_url": API, "live_view_port": None}
    assert c.api_key == API_KEY and API_KEY not in repr(c)


def test_token_only_in_env_file_as_openai_compatible_key(docker, api):
    started(docker)
    assert TOKEN not in json.dumps(docker.calls)  # never on a command line (ps-visible)
    env = dict(line.split("=", 1) for line in docker.env_files[0].strip().splitlines())
    assert env["OPENAI_COMPATIBLE_API_KEY"] == TOKEN
    assert [k for k, v in env.items() if v == TOKEN] == ["OPENAI_COMPATIBLE_API_KEY"]
    assert not [k for k in env if re.search("VULTR|JEV", k, re.IGNORECASE)]
    assert env["BROWSER_TYPE"] == "cdp-connect"
    assert env["BROWSER_REMOTE_DEBUGGING_URL"] == "http://172.30.101.10:9222"
    assert env["OPENAI_COMPATIBLE_API_BASE"] == "http://host.docker.internal:8700/v1"
    assert env["SKYVERN_TELEMETRY"] == "false"


def test_env_file_is_deleted_after_create(docker, api):
    started(docker)
    create = docker.named("create")[0]
    assert not Path(create[create.index("--env-file") + 1]).exists()


def test_refuses_upstream_credential_env_names(docker, api, monkeypatch):
    monkeypatch.setattr(CellSpec, "brain_env", lambda self: {"VULTR_INFERENCE_API_KEY": "x"})
    with pytest.raises(CellError):
        SkyvernCell(spec(), docker=docker, sleep=lambda s: None).start(TOKEN)
    assert not docker.named("create")


def test_allowed_hosts_and_skip_migrations(docker, api):
    started(docker, allowed_hosts=["172.30.99.20"], skip_migration_version="2dda9d1255a6")
    env = docker.env_files[0]
    assert 'ALLOWED_HOSTS=["172.30.99.20"]' in env and "ALLOWED_SKIP_DB_MIGRATION_VERSION=2dda9d1255a6" in env


def test_recycle_hands_keeps_brain_and_rebind_recreates_brain(docker, api):
    c = started(docker)
    n_create = len(docker.named("create"))
    c.recycle_hands()
    assert len(docker.named("create")) == n_create  # brain untouched
    last_run = docker.named("run")[-1]
    assert last_run[last_run.index("--ip") + 1] == "172.30.101.10"
    c.rebind("gw-session-token-NEW")
    assert len(docker.named("create")) == n_create + 1
    assert "OPENAI_COMPATIBLE_API_KEY=gw-session-token-NEW" in docker.env_files[-1]


def test_stop_removes_containers_and_network(docker, api):
    c = started(docker)
    c.stop()
    assert ["rm", "-f", "-v", "ba-sky-c0-brain", "ba-sky-c0-hands"] in docker.calls
    assert ["network", "rm", "ba-sky-c0"] in docker.calls
    with pytest.raises(CellError):
        _ = c.api_key


def test_unhealthy_brain_raises(docker):
    with respx.mock() as r:
        r.get(f"{API}/heartbeat").mock(side_effect=httpx.ConnectError("refused"))
        c = SkyvernCell(spec(), docker=docker, sleep=lambda s: None, boot_timeout_s=0.01)
        with pytest.raises(CellError):
            c.start(TOKEN)


def test_postgres_waits_until_ready_and_create_uses_schema_version(api):
    d = FakeDocker(pg_ready_after=3)
    ensure_postgres(d, sleep=lambda s: None)
    assert sum(1 for c in d.calls if c[-1] == "select 1") == 4
    assert ["network", "create", "--internal", "ba-sky-db"] in d.calls
    c = create_skyvern_cell("c1", TOKEN, "http://host.docker.internal:8700/v1", 102, docker=d)
    assert "ALLOWED_SKIP_DB_MIGRATION_VERSION=2dda9d1255a6" in d.env_files[-1] and c.brain_url == API


def test_postgres_never_ready_raises():
    d = FakeDocker(pg_ready_after=10**6)
    with pytest.raises(CellError):
        ensure_postgres(d, ready_timeout_s=0.01, sleep=lambda s: None)


def mock_task(r, status="completed", actions=None, extracted=None):
    r.post(f"{API}/tasks").mock(return_value=httpx.Response(200, json={"task_id": "tsk_1"}))
    r.get(f"{API}/tasks/tsk_1").mock(side_effect=[
        httpx.Response(200, json={"status": "running"}),
        httpx.Response(200, json={"status": status, "extracted_information": extracted, "step_count": 1,
                                  "action_screenshot_urls": ["file:///data/artifacts/o/t/a.png"],
                                  "screenshot_url": "file:///data/artifacts/o/t/final.png"})])
    r.get(f"{API}/tasks/tsk_1/actions").mock(return_value=httpx.Response(200, json=actions or [
        {"action_type": "complete", "reasoning": "read"}, {"action_type": "extract", "reasoning": "rows"}]))


def test_client_uses_v1_field_names_and_api_key(api):
    mock_task(api, extracted={"heading": "H"})
    client = SkyvernClient(API, API_KEY, sleep=lambda s: None)
    res = client.run_task("http://x.example/", "Read it.", "Extract.", {"type": "object"}, max_steps=3)
    post = next(call.request for call in api.calls if call.request.method == "POST")
    body = json.loads(post.content)
    assert body["data_extraction_goal"] == "Extract." and body["extracted_information_schema"] == {"type": "object"}
    assert "data_extraction_schema" not in body and body["max_steps_per_run"] == 3
    assert post.headers["x-api-key"] == API_KEY and API_KEY not in repr(client)
    assert res["status"] == "completed" and res["extracted"] == {"heading": "H"}
    assert [a["action_type"] for a in res["actions"]] == ["complete", "extract"]
    assert res["screenshots"][-1].endswith("final.png")


def test_client_rejected_task(api):
    api.post(f"{API}/tasks").mock(return_value=httpx.Response(400, text="The host in your url is blocked"))
    res = SkyvernClient(API, API_KEY).run_task("http://10.0.0.1/", "Read.")
    assert res["status"] == "rejected" and "blocked" in res["failure_reason"]


# -- backend ---------------------------------------------------------------------------------------------------

class FakeCell:
    cell_id = "c0"
    brain_url = API
    api_key = API_KEY

    def fetch_artifact(self, url):
        return url.encode()


class FakeClient:
    def __init__(self, result):
        self.result, self.goals = result, []

    def run_task(self, url, goal, extraction_goal, schema, max_steps):
        self.goals.append(goal)
        return self.result


def backend_with(result, tmp_path, allowed=("x.example",)):
    steps = StepLog(tmp_path / "steps.jsonl", run_id="run-1", session_id="s1", source_id="src")
    client = FakeClient(result)
    b = SkyvernBackend(FakeCell(), steps, list(allowed), gateway_session_id="skyvern-c0-1",
                       capture=lambda b: "sha256:" + b.hex()[:8], usage=lambda sid: {"spent_usd": 0.0014, "calls": 8},
                       client=client)
    b.open("s1")
    return b, client, tmp_path / "steps.jsonl"


def lines(p):
    return [json.loads(x) for x in p.read_text().splitlines()]


OK = {"task_id": "tsk_1", "status": "completed", "extracted": {"heading": "H"}, "step_count": 1, "elapsed_s": 14.2,
      "actions": [{"action_type": "complete", "reasoning": "read"}, {"action_type": "extract", "reasoning": "x"}],
      "screenshots": ["file:///data/artifacts/a.png", "file:///data/artifacts/final.png"]}


def test_backend_maps_actions_to_s2_steps(tmp_path):
    b, client, path = backend_with(OK, tmp_path)
    res = b.run_goal("Read the table.", "https://www.x.example/list", {"type": "object"})
    assert res["ok"] and "read-only task" in client.goals[0]
    steps = lines(path)
    assert [s["mode"] for s in steps] == ["S2"] * 3
    assert steps[-1]["executed"]["tool"] == "skyvern.task" and steps[-1]["evaluated"]["extracted"] == {"heading": "H"}
    assert steps[-1]["screenshot_key"].startswith("sha256:")
    assert steps[1]["parent_step_id"] == steps[0]["step_id"] and steps[2]["parent_step_id"] == steps[1]["step_id"]
    assert all(s["event"] is None for s in steps)
    assert steps[-1]["generated_by"]["agent"] == "skyvern" and steps[-1]["session_id"] == "s1"
    closed = b.close()
    assert closed["closed"] and closed["usage"]["calls"] == 8 and closed["gateway_session_id"] == "skyvern-c0-1"


def test_backend_blocks_start_url_outside_allowed_domains(tmp_path):
    b, client, path = backend_with(OK, tmp_path)
    res = b.run_goal("Read.", "https://evil.invalid/")
    assert res["status"] == "blocked" and not client.goals
    assert lines(path)[-1]["event"] == "hard_stop"


def test_backend_flags_non_read_only_actions(tmp_path):
    bad = {**OK, "actions": [{"action_type": "upload_file", "reasoning": "?"}, {"action_type": "complete"}]}
    b, _, path = backend_with(bad, tmp_path)
    res = b.run_goal("Read.", "https://x.example/")
    steps = lines(path)
    assert steps[0]["event"] == "hard_stop" and steps[-1]["event"] == "failure" and not res["ok"]


def test_backend_failed_task_is_failure_step(tmp_path):
    b, _, path = backend_with({**OK, "status": "failed", "failure_reason": "LLM errors", "actions": []}, tmp_path)
    assert not b.run_goal("Read.", "https://x.example/")["ok"]
    assert lines(path)[-1]["event"] == "failure"


def test_backend_emits_quarantine_when_the_gateway_flagged_the_task(tmp_path):
    flag = {"flagged": True, "jev_choice": "injection", "jev_confidence": 0.98, "safety_verdict": "unsafe",
            "reason": "injection", "by": "gateway", "ts": 1.0}
    counts = iter([{"flagged": 0}, {"flagged": 2, "last_flag": flag}])
    b, _client, path = backend_with(OK, tmp_path)
    b.usage = lambda sid: next(counts)
    b.run_goal("read the page", "https://x.example/")
    steps = lines(path)
    q = [s for s in steps if s.get("event") == "quarantine"]
    assert len(q) == 1 and q[0]["screen"]["by"] == "gateway" and q[0]["screen"]["jev_confidence"] == 0.98
    assert "ts" not in q[0]["screen"] and q[0]["evaluated"]["status"] == "quarantined_continue"
    assert steps[1]["parent_step_id"] == q[0]["step_id"]  # the actions chain after the quarantine step


def test_backend_no_quarantine_when_nothing_flagged(tmp_path):
    b, _client, path = backend_with(OK, tmp_path)
    b.run_goal("read the page", "https://x.example/")
    assert not [s for s in lines(path) if s.get("event") == "quarantine"]


def test_host_allowed():
    assert host_allowed("https://a.b.example/x", ["b.example"]) and not host_allowed("https://bexample/", ["b.example"])
    assert not host_allowed("https://b.example.evil.invalid/", ["b.example"])


# -- live (opt-in): a real cell against a local synthetic page ---------------------------------------------------

@pytest.mark.live
@pytest.mark.skipif(os.environ.get("BA_SKYVERN_LIVE") != "1" or not shutil.which("docker"),
                    reason="set BA_SKYVERN_LIVE=1 with docker, the Skyvern image and a running gateway")
def test_live_cell_reads_local_page(tmp_path):
    """Needs: gateway on the host (BA_GATEWAY_URL, BA_GATEWAY_ADMIN_TOKEN in env), images pulled.
    Cell traffic reaches the gateway at BA_SKYVERN_GATEWAY_BASE (default http://host.docker.internal:8700/v1)."""
    from shared.gateway_client import GatewayAdmin

    admin = GatewayAdmin(os.environ.get("BA_GATEWAY_URL", "http://127.0.0.1:8700"), os.environ["BA_GATEWAY_ADMIN_TOKEN"])
    d = cellmod.Docker()
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>Padrón de entidades de ejemplo</h1><table><tr><td>Entidad Ejemplo 01</td>"
                                     "<td>ZZZ010101AA1</td></tr></table>", encoding="utf-8")
    web, web_ip, sid = "ba-sky-live-web", "172.30.120.20", "skyvern-live-test"
    d(["rm", "-f", web], check=False)
    cell = None
    try:
        cell = create_skyvern_cell("live", admin.open_session(sid, ttl_s=900, budget_usd=0.2, run_id="skyvern-live"),
                                   os.environ.get("BA_SKYVERN_GATEWAY_BASE", "http://host.docker.internal:8700/v1"),
                                   subnet_index=120, docker=d, allowed_hosts=[web_ip])
        d(["run", "-d", "--name", web, "--network", cell.spec.network, "--ip", web_ip, "-v", f"{site}:/site:ro",
           "--entrypoint", "python", cell.spec.image, "-m", "http.server", "8080", "-d", "/site"])
        steps = StepLog(tmp_path / "steps.jsonl", run_id="skyvern-live", session_id="s-live")
        b = SkyvernBackend(cell, steps, [web_ip], gateway_session_id=sid, usage=admin.usage,
                           capture=lambda data: f"bytes:{len(data)}")
        b.open("s-live")
        res = b.run_goal("Read the page.", f"http://{web_ip}:8080/index.html",
                         {"type": "object", "properties": {"heading": {"type": "string"},
                                                           "first_row": {"type": "string"}}},
                         extraction_goal="Extract the heading and the first row name.", max_steps=3)
        assert res["status"] == "completed", res.get("failure_reason")
        assert "Ejemplo" in json.dumps(res["extracted"], ensure_ascii=False)
        assert res["screenshot_keys"] and all(k != "bytes:0" for k in res["screenshot_keys"])
        closed = b.close()
        print("LIVE timings", cell.timings, "task_s", res["elapsed_s"], "calls", closed["usage"]["calls"])
        assert closed["usage"]["calls"] >= 2
        env_names = subprocess.run(["docker", "inspect", cell.spec.brain, "--format",
                                    "{{range .Config.Env}}{{println .}}{{end}}"],
                                   capture_output=True, text=True, check=True).stdout
        assert not [ln for ln in env_names.splitlines() if re.search("VULTR|JEV", ln.split("=", 1)[0], re.IGNORECASE)]
    finally:
        admin.revoke(sid)
        d(["rm", "-f", web], check=False)
        if cell:
            cell.stop()
