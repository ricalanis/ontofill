"""Recorded S1 controller paths keep only screened, trace-backed observations."""

from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from ontofill.lake import FileLake
from ontofill.phases.p5_execute.controller import _safe_url
from ontofill.refiner import MemorySilverStore
from ontofill.runfeed import RunFeed
from ontofill.sandbox import build_job_record
from ontofill.workflow import _scratch_case, run_case
from tests.genericity.fixtures.libraries import library_decisions

PAGE_URL = "https://libraries.example.test/branches"
RECORDED = {"backend": "recorded", "model": "synthetic-replay", "at": "2026-09-26T00:00:00Z"}


def test_signed_page_url_is_not_exported_as_evidence() -> None:
    assert (
        _safe_url(
            "https://libraries.example.test/list?X-Amz-Signature=not-a-real-key",
            ["libraries.example.test"],
        )
        is None
    )


def _teardown_job_record(run_id: str, source_id: str, job_id: str) -> dict:
    checkpoints = [
        "dispatch_result",
        "host_check",
        "pod_identity",
        "isolation_probe",
        "secrets",
        "teardown",
    ]
    trace = [
        {
            "step_id": "step:synthetic-cell",
            "run_id": run_id,
            "source_id": source_id,
            "requested": {"url": PAGE_URL, "allowed_domains": ["libraries.example.test"]},
            "evaluated": {"status": "captured", "proof_checkpoint": checkpoint},
            "generated_by": RECORDED,
        }
        for checkpoint in checkpoints
    ]
    return build_job_record(
        {
            "trace": trace,
            "status": 200,
            "started_at": "2026-09-26T00:00:00Z",
            "ended_at": "2026-09-26T00:00:01Z",
            "limits": {
                "memory_mb": 1024,
                "cpus": 1.0,
                "pids": 256,
                "timeout_s": 900,
                "max_steps": 30,
            },
            "usage": {"peak_memory_mb": 8, "wall_s": 1, "steps": 1},
            "proof": {
                "dispatch_result": {"url": PAGE_URL, "status": 200},
                "host_check": {
                    "docker_host": "synthetic-host",
                    "runtime": "runc",
                    "runtime_available": True,
                    "cpu_virtualization_flags": [],
                    "dev_kvm_present": False,
                },
                "pod_identity": {
                    "hostname": "synthetic-pod",
                    "uname": {"system": "Linux", "release": "synthetic", "machine": "arm64"},
                },
                "isolation_probe": {
                    "network": {
                        "host": "blocked.invalid",
                        "blocked": True,
                        "proxy_logged_block": True,
                    },
                    "write_outside_pod": {"path": "/host/proof", "blocked": True},
                    "write_outside_writable_mount": {"path": "/etc/proof", "blocked": True},
                },
                "secrets": {
                    "ok": True,
                    "env_keys_found": 0,
                    "files_with_keys": 0,
                    "metadata_ip": "BLOCKED",
                    "mesh": "BLOCKED",
                },
                "teardown": {
                    "pod_gone": True,
                    "proxy_gone": True,
                    "network_removed": True,
                    "verified": True,
                },
            },
        },
        job_id=job_id,
    )


class FakeBrowserController:
    """A recorded controller matching the service's screen-free observe summary."""

    def __init__(self, steps_root: Path, captures_root: Path, *, unsafe: bool = False) -> None:
        self.steps_root = steps_root
        self.captures_root = captures_root
        self.unsafe = unsafe
        self.session_id = f"browser-{uuid.uuid4().hex[:12]}"
        self.calls: list[str] = []
        self.steps_path: Path | None = None
        self.screenshot_key = ""
        self.extracted: dict = {}
        self.run_id = ""
        self.source_id = ""
        self.objective_id = ""
        self.job_id = ""

    def session_open(self, tdd: dict, allowed_domains: list[str], limits: dict) -> dict:
        self.calls.append("open")
        assert allowed_domains == ["libraries.example.test"]
        assert limits["max_steps"] > 0
        assert limits["memory_mb"] == 1024
        assert limits["cpus"] == 1.0
        assert limits["pids"] == 256
        self.run_id = tdd["run_id"]
        self.source_id = tdd["source_id"]
        self.objective_id = tdd["objective_id"]
        self.job_id = tdd["job_id"]
        self.steps_root.mkdir(parents=True, exist_ok=True)
        self.steps_path = self.steps_root / f"{self.session_id}.jsonl"
        blob = b"synthetic controller screenshot"
        digest = hashlib.sha256(blob).hexdigest()
        self.screenshot_key = f"sha256:{digest}"
        capture_path = self.captures_root / "bronze" / "sha256" / digest
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        capture_path.write_bytes(blob)
        (capture_path.parent / f"{digest}.meta.json").write_text(
            json.dumps(
                {
                    "content_type": "image/png",
                    "url": PAGE_URL,
                    "captured_at": "2026-09-26T00:00:00Z",
                    "source_id": tdd["source_id"],
                    "step_id": "step:synthetic-screen",
                }
            ),
            encoding="utf-8",
        )
        return {
            "session_id": self.session_id,
            "cell_id": "cell:synthetic",
            "isolation": {
                "provider": "recorded",
                "runtime": "runc",
                "tier": "synthetic",
                "egress": [],
            },
            "live_view_url": None,
            "url": PAGE_URL,
            "steps_path": str(self.steps_path),
        }

    def _append(self, **extra: object) -> dict:
        assert self.steps_path is not None
        base = {
            "step_id": f"step:{uuid.uuid4().hex}",
            "session_id": self.session_id,
            "run_id": self.run_id,
            "phase": 5,
            "source_id": self.source_id,
            "objective_id": self.objective_id,
            "tdd_path": f"04-local/{self.source_id}__{self.objective_id}/tdd.json",
            "mode": "S1",
            "observed": {"url": PAGE_URL},
            "requested": {"tool": "synthetic.controller"},
            "executed": {"status": "recorded"},
            "evaluated": {"status": "ok"},
            "parent_step_id": None,
            "value_ids": [],
            "ts": datetime.now(UTC).isoformat(),
            "generated_by": RECORDED,
        }
        step = base | extra
        with self.steps_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(step) + "\n")
        return step

    def session_act(
        self, session_id: str, *, goal: str | None = None, action: dict | None = None
    ) -> dict:
        self.calls.append("act")
        assert session_id == self.session_id
        assert goal and action is None
        clear_step = self._append(
            screenshot_key=self.screenshot_key,
            screen={
                "flagged": False,
                "jev_choice": "benign",
                "jev_confidence": 0.96,
                "safety_verdict": "unavailable",
                "reason": "controller screen marked the page benign",
                "by": "controller",
            },
        )
        quarantine_step = None
        if self.unsafe:
            quarantine_step = self._append(
                event="quarantine",
                screenshot_key=self.screenshot_key,
                screen={
                    "flagged": True,
                    "jev_choice": "injection",
                    "jev_confidence": 0.98,
                    "safety_verdict": "unavailable",
                    "reason": "synthetic unsafe page",
                    "by": "controller",
                },
                evaluated={"status": "quarantined_continue", "reason": "synthetic unsafe page"},
            )
        self._append(
            event="action_gate",
            gate={
                "action": "page.read",
                "risk_tier": "SAFE",
                "decided_by": "code",
                "outcome": "allowed",
                "approval_path": None,
            },
        )
        self._append(
            event="verify",
            verify={
                "goal": "Read public library branch fields",
                "verdict": "achieved",
                "confidence": 0.94,
                "backend": "vultr",
                "model": "synthetic-verifier",
                "screenshot_key": self.screenshot_key,
            },
        )
        selectors = {
            "name": "main table tr:nth-child(2) td:nth-child(1)",
            "free_internet": "main table tr:nth-child(2) td:nth-child(2)",
            "opening_hours": "main table tr:nth-child(2) td:nth-child(3)",
            "unrequested": "main table .internal",
        }
        values = {
            "name": {"value": "North Branch", "selector": selectors["name"]},
            "free_internet": {"value": "true", "selector": selectors["free_internet"]},
            "opening_hours": {
                "value": "Mon-Fri 09:00-17:00",
                "selector": selectors["opening_hours"],
            },
        }
        extract_step = self._append(
            requested={"tool": "extract", "args": {"fields": selectors}},
            executed={"ok": True, "values": values},
            evaluated={"ok": True, "fields_found": sorted(values), "fields_missing": []},
            parent_step_id=(quarantine_step or clear_step)["step_id"],
            screenshot_key=self.screenshot_key,
        )
        self.extracted = {
            property_id: {
                **value,
                "url": PAGE_URL,
                "screenshot_key": self.screenshot_key,
                "step_id": extract_step["step_id"],
                "captured_at": extract_step["ts"],
            }
            for property_id, value in values.items()
        }
        self.extracted["unrequested"] = {
            "value": "ignore",
            "selector": selectors["unrequested"],
            "url": PAGE_URL,
            "screenshot_key": self.screenshot_key,
            "step_id": extract_step["step_id"],
            "captured_at": extract_step["ts"],
        }
        return {"status": "achieved", "extracted": self.extracted}

    def session_observe(self, session_id: str) -> dict:
        self.calls.append("observe")
        assert session_id == self.session_id
        # Current PA service returns observation/extracted/metrics without screen.
        return {
            "observation": {"text_excerpt": "synthetic text never logged by the engine"},
            "extracted": self.extracted,
            "metrics": {},
        }

    def session_close(self, session_id: str) -> dict:
        self.calls.append("close")
        assert session_id == self.session_id
        record = _teardown_job_record(self.run_id, self.source_id, self.job_id)
        return {
            "closed": True,
            "metrics": {},
            "token_revoked": True,
            "cell": {
                "cell_id": "cell:synthetic",
                "released": True,
                "destroy_ms": 1,
                "teardown": {"state": "destroyed", "job_record": record},
            },
        }


def test_s1_controller_exports_only_trace_backed_target_values(tmp_path: Path) -> None:
    case = tmp_path / "case"
    case.mkdir()
    brief = Path(__file__).parent / "genericity/cases/libraries/brief.md"
    (case / "brief.md").write_text(brief.read_text(encoding="utf-8"), encoding="utf-8")
    run_id = f"mock-{uuid.uuid4().hex[:12]}"
    decisions = library_decisions()
    local_scope = deepcopy(decisions.responses["phase4.local_scope"][0])
    local_scope["steps"][0]["starting_mode"] = "S1"
    decisions.responses["phase4.local_scope"][0] = local_scope
    steps_root, captures_root = tmp_path / "controller-steps", tmp_path / "controller-captures"
    controller = FakeBrowserController(steps_root, captures_root)

    def unexpected_capture(*_args, **_kwargs):
        raise AssertionError("an S1 TDD must start at the controller")

    result = run_case(
        case,
        run_id=run_id,
        decision=decisions,
        preview_past_checkpoints=True,
        search_client=_LibrarySearch(),
        capture=unexpected_capture,
        browser_client=controller,
        browser_steps_root=steps_root,
        browser_captures_root=captures_root,
        store=MemorySilverStore(),
    )

    assert result in {0, 3}
    assert controller.calls == ["open", "act", "observe", "close"]
    scratch, lake = _scratch_case(case, run_id)
    assert scratch.exists()
    trace_key = f"gold/{case.name}/{run_id}/trace.jsonl"
    trace = [json.loads(line) for line in lake.read_key(trace_key).splitlines()]
    controller_steps = [step for step in trace if step.get("session_id") == controller.session_id]
    dispatch = next(
        step
        for step in trace
        if step.get("requested", {}).get("tool") == "browser_agent.session.open"
    )
    raw_steps = [
        json.loads(line) for line in controller.steps_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len({step["step_id"] for step in trace}) == len(trace)
    assert trace.index(dispatch) < trace.index(controller_steps[0])
    assert controller_steps[0]["parent_step_id"] == dispatch["step_id"]
    assert controller_steps[0]["step_id"] == raw_steps[0]["step_id"]
    assert raw_steps[0]["parent_step_id"] is None
    assert any(step.get("event") == "action_gate" for step in controller_steps)
    assert any(
        step.get("event") == "verify" and step["verify"]["verdict"] == "achieved"
        for step in controller_steps
    )
    values = [step for step in trace if step.get("executed", {}).get("tool") == "emit.observation"]
    assert len(values) == 3
    assert {step["requested"]["property_id"] for step in values} == {
        "name",
        "free_internet",
        "opening_hours",
    }
    extract_ids = {
        step["step_id"]
        for step in controller_steps
        if step.get("requested", {}).get("tool") == "extract"
    }
    assert {step["parent_step_id"] for step in values} == extract_ids
    entities = [
        json.loads(line)
        for line in lake.read_key(f"gold/{case.name}/{run_id}/entities.jsonl").splitlines()
    ]
    assert len(entities) == 1
    assert entities[0]["properties"]["free_internet"]["value"] is True
    evidence = entities[0]["properties"]["free_internet"]["evidence"][0]
    assert evidence["step_id"] in extract_ids
    extract_step = next(step for step in controller_steps if step["step_id"] == evidence["step_id"])
    assert evidence["captured_at"] == extract_step["ts"]
    jobs = [
        json.loads(line)
        for line in lake.read_key(f"runs/{case.name}/{run_id}/jobs.jsonl").splitlines()
    ]
    assert len(jobs) == 1
    assert set(jobs[0]["checkpoints"]) == {
        "host",
        "task",
        "where",
        "isolation",
        "secrets",
        "teardown",
    }
    serialized_trace = json.dumps(trace)
    assert str(case.resolve()) not in serialized_trace
    assert "live_view_url" not in serialized_trace


class _LibrarySearch:
    name = "synthetic_directory"

    def search(self, _query: str):
        from ontofill_scrape import SearchResult

        return [SearchResult(PAGE_URL, "City library directory", "Public branch information")]


def test_missing_download_falls_back_to_controller_and_quarantine_withholds(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    store = MemorySilverStore()
    steps_root, captures_root = tmp_path / "controller-steps", tmp_path / "controller-captures"
    controller = FakeBrowserController(steps_root, captures_root, unsafe=True)
    feed = RunFeed(lake, "synthetic-case", "mock-r3-fallback", RECORDED, start_heartbeat=False)
    feed.update_status(state="running", phase=5)
    page_key = lake.put_bytes(b"no downloadable file")
    page_screen = lake.put_bytes(b"captured landing page")

    def capture(url: str, **kwargs) -> dict:
        assert url == PAGE_URL
        return {
            "url": url,
            "html": "<html><main>No downloadable table here.</main></html>",
            "html_key": page_key,
            "screenshot_key": page_screen,
            "trace": [],
        }

    from ontofill.phases.p5_execute.phase import execute_objective

    try:
        result = execute_objective(
            case_dir=tmp_path,
            objective={
                "source_id": "library_source",
                "id": "find_access",
                "source_url": PAGE_URL,
                "source_type": "city_directory",
            },
            ontology={
                "classes": [
                    {"id": "library", "identifier_property": "name", "title_property": "name"}
                ],
                "properties": [
                    {
                        "id": "name",
                        "label": "Name",
                        "description": "Displayed branch name",
                        "domain": "library",
                        "datatype": "string",
                    },
                    {
                        "id": "free_internet",
                        "label": "Free internet",
                        "description": "Whether the library offers free internet",
                        "domain": "library",
                        "datatype": "boolean",
                    },
                    {
                        "id": "opening_hours",
                        "label": "Opening hours",
                        "description": "Displayed public hours",
                        "domain": "library",
                        "datatype": "string",
                    },
                ],
            },
            tdd={
                "allowed_domains": ["libraries.example.test"],
                "target_fields": ["name", "free_internet", "opening_hours"],
                "target_volume": 1,
                "budget_usd": 0.5,
                "steps": [{"starting_mode": "D0"}],
            },
            lake=lake,
            run_id="mock-r3-fallback",
            decision=library_decisions(),
            store=store,
            provenance=RECORDED,
            capture=capture,
            feed=feed,
            browser_client=controller,
            browser_steps_root=steps_root,
            browser_captures_root=captures_root,
        )
        assert result.observations == []
        assert controller.calls == ["open", "act", "observe", "close"]
        trace = [
            json.loads(line)
            for line in lake.read_key(
                "runs/synthetic-case/mock-r3-fallback/trace.live.jsonl"
            ).splitlines()
        ]
        assert any(step.get("event") == "quarantine" for step in trace)
        assert any(step.get("event") == "verify" for step in trace)
        assert any(step.get("event") == "action_gate" for step in trace)
        assert not store.list_for_run("mock-r3-fallback")
        job = lake.read_key("runs/synthetic-case/mock-r3-fallback/jobs.jsonl")
        assert len(job.splitlines()) == 1
    finally:
        feed.close()
