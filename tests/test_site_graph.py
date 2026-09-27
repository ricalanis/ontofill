from __future__ import annotations

import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from ontofill.case.checkpoints import write_json
from ontofill.contracts import validate_document
from ontofill.lake import FileLake
from ontofill.runfeed import RunFeed
from ontofill.phases.p3_fanout.site_graph import (
    build_site_graph,
    rank_objectives_by_site_graph,
    run_confirmed_source_spiders,
    site_graph_context,
    sources_needing_spider,
)
from ontofill.phases.p4_local_scoping.phase import draft_local_scope

SOURCE_ID = "source-synthetic"
OBJECTIVE_ID = "objective-synthetic"
SOURCE_URL = "https://catalog.example.invalid/"
FINGERPRINT = "a" * 64
JOB_ID = "job:" + "b" * 32
PROVENANCE = {
    "backend": "recorded",
    "model": "synthetic-model",
    "at": "2026-09-26T12:00:00+00:00",
}
ONTOLOGY = {
    "version": "ontology-synthetic-1",
    "primary_class": "record",
    "classes": [{"id": "record", "label": "Record", "description": "A public record"}],
    "properties": [
        {
            "id": "record_id",
            "label": "Record ID",
            "description": "Stable record identifier",
            "domain": "record",
            "datatype": "string",
            "dod": True,
        },
        {
            "id": "name",
            "label": "Name",
            "description": "Record name",
            "domain": "record",
            "datatype": "string",
            "dod": True,
        },
    ],
}
OBJECTIVE = {
    "id": OBJECTIVE_ID,
    "source_id": SOURCE_ID,
    "source_url": SOURCE_URL,
    "source_fingerprint": FINGERPRINT,
    "target_fields": ["record_id", "name"],
    "priority": 1,
    "expected_contribution": 0.0,
    "authority_tier": "primary",
    "confirmed_bronze_key": "sha256:" + "c" * 64,
}
OBJECTIVES = {
    "ontology_version": ONTOLOGY["version"],
    "prd_path": "01-scope/prd.json",
    "generated_by": PROVENANCE,
    "objectives": [OBJECTIVE],
}
LOCAL_SCOPE_RESPONSE = {
    "global_requirement_ids": ["req-1"],
    "local_definition_of_done": [{"metric": "records captured", "operator": ">=", "target": 1}],
    "extraction_method": "dom",
    "validation_rules": ["A record identifier is present"],
    "rate_limit_per_minute": 12,
    "budget_usd": 1.0,
    "target_volume": 2,
    "steps": [
        {
            "id": "read-record",
            "description": "Follow the graph's record link and read the record fields",
            "starting_mode": "S1",
            "allowed_modes": ["S1"],
            "observation_channel": "text_structure",
            "risk_tier": "SAFE",
            "termination_predicate": "A record identifier is visible",
        }
    ],
}


def _page(
    lake: FileLake,
    *,
    url: str,
    body: bytes,
    content_type: str,
    depth: int,
    parent_url: str | None,
    link_kind: str,
    index: int,
) -> dict:
    key = lake.put_bytes(body, {"content_type": content_type, "url": url})
    return {
        "url": url,
        "requested_url": url,
        "final_url": url,
        "redirect_chain": [url],
        "depth": depth,
        "status": 200,
        "content_type": content_type,
        "method": "GET",
        "link_kind": link_kind,
        "parent_url": parent_url,
        "bronze_key": key,
        "trace_step_id": "step:" + f"{index:032x}",
        "job_id": JOB_ID,
    }


def _capture_result(lake: FileLake) -> dict:
    root = SOURCE_URL
    detail = "https://catalog.example.invalid/records/1"
    download = "https://catalog.example.invalid/files/export.csv"
    pages = [
        _page(
            lake,
            url=root,
            body=(
                b"<html><body><main><h1>Public records</h1>"
                b'<a href="/records/1">Record</a></main></body></html>'
            ),
            content_type="text/html",
            depth=0,
            parent_url=None,
            link_kind="navigate",
            index=1,
        ),
        _page(
            lake,
            url=detail,
            body=(
                b"<html><body><main><h1>Record</h1><p>Record ID R-1</p>"
                b'<a href="/files/export.csv" download>Export</a></main></body></html>'
            ),
            content_type="text/html",
            depth=1,
            parent_url=root,
            link_kind="navigate",
            index=2,
        ),
        _page(
            lake,
            url=download,
            body=b"record_id,name\nR-1,Record A\n",
            content_type="text/csv",
            depth=2,
            parent_url=detail,
            link_kind="download",
            index=3,
        ),
        {
            "url": "https://catalog.example.invalid/private",
            "requested_url": "https://catalog.example.invalid/private",
            "final_url": "https://catalog.example.invalid/private",
            "redirect_chain": ["https://catalog.example.invalid/private"],
            "depth": 1,
            "status": None,
            "content_type": "application/octet-stream",
            "method": "GET",
            "link_kind": "navigate",
            "parent_url": root,
        },
    ]
    return {
        "job_id": JOB_ID,
        "pages": pages,
        "robots": [
            {
                "origin": "https://catalog.example.invalid",
                "url": "https://catalog.example.invalid/robots.txt",
                "http_status": 200,
                "decision": "allow",
                "crawl_delay_seconds": 0.0,
                "bronze_key": lake.put_bytes(b"User-agent: *\nAllow: /\n"),
            }
        ],
        "edges": [
            {
                "from_url": root,
                "to_url": detail,
                "kind": "navigate",
                "method": "GET",
                "risk_tier": "SAFE",
                "followed": True,
                "reason": None,
            },
            {
                "from_url": detail,
                "to_url": download,
                "kind": "download",
                "method": "GET",
                "risk_tier": "SAFE",
                "followed": True,
                "reason": None,
            },
            {
                "from_url": root,
                "to_url": "https://catalog.example.invalid/private",
                "kind": "navigate",
                "method": "GET",
                "risk_tier": "SAFE",
                "followed": False,
                "reason": "robots_disallow",
            },
            {
                "from_url": root,
                "to_url": "https://elsewhere.example.net/records",
                "kind": "navigate",
                "method": "GET",
                "risk_tier": "SAFE",
                "followed": False,
                "reason": "cross_registrable_domain",
            },
        ],
        "crawl": {
            "max_depth": 2,
            "page_cap": 30,
            "delay_seconds": 1.0,
            "attempted_pages": 4,
            "fetched_pages": 3,
            "stop_reason": "queue_exhausted",
        },
    }


class FakeDecision:
    backend = "recorded"
    model = "synthetic-model"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, purpose: str, prompt: str, schema: dict) -> dict:
        self.calls.append((purpose, prompt))
        if purpose == "phase3.site_graph_page_type":
            if "text/csv" in prompt:
                response = {"label": "download", "property_hints": []}
            elif "/records/1" in prompt:
                response = {
                    "label": "detail:record",
                    "property_hints": [
                        {
                            "sample_index": 0,
                            "property_id": "record_id",
                            "evidence_quote": "Record ID R-1",
                        }
                    ],
                }
            else:
                response = {
                    "label": "listing",
                    "property_hints": [
                        {
                            "sample_index": 0,
                            "property_id": "name",
                            "evidence_quote": "Public records",
                        }
                    ],
                }
        elif purpose == "phase4.local_scope":
            response = copy.deepcopy(LOCAL_SCOPE_RESPONSE)
        else:
            raise AssertionError(f"unexpected inference purpose: {purpose}")
        Draft202012Validator(schema).validate(response)
        return response


def test_site_graph_publishes_ontology_labeled_pages_and_keeps_unfetched_links_in_edges(
    tmp_path: Path,
) -> None:
    decision = FakeDecision()
    lake = FileLake(tmp_path / "lake")
    capture_result = _capture_result(lake)
    graph_trace: list[dict] = []
    envelope = build_site_graph(
        case_dir=tmp_path / "case",
        objective=OBJECTIVE,
        ontology=ONTOLOGY,
        decision=decision,
        lake=lake,
        run_id="mock-synthetic-run",
        capture_result=capture_result,
        trace_out=graph_trace,
    )

    validate_document("site-graph", envelope)
    assert envelope["schema_version"] == "1.0.3"
    assert envelope["generated_by"]["backend"] == decision.backend
    assert envelope["generated_by"]["model"] == decision.model
    assert isinstance(envelope["generated_by"]["at"], str)
    feed = RunFeed(
        lake,
        "synthetic-case",
        "mock-synthetic-run",
        envelope["generated_by"],
        start_heartbeat=False,
    )
    for step in graph_trace:
        validate_document("trace-step", step)
        feed.append_step(step)
    feed.close()
    assert len(
        lake.read_key("runs/synthetic-case/mock-synthetic-run/trace.live.jsonl").splitlines()
    ) == len(graph_trace)
    graph = envelope["graph"]
    assert len(graph["types"]) == 3
    assert {item["label"]["kind"] for item in graph["types"]} == {
        "listing",
        "detail",
        "download",
    }
    detail_type = next(item for item in graph["types"] if item["label"]["kind"] == "detail")
    assert detail_type["label"]["class_id"] == "record"
    assert {hint["property_id"] for item in graph["types"] for hint in item["property_hints"]} == {
        "name",
        "record_id",
    }
    assert len(graph["instances"]) == 3
    assert all(item["job_id"] == JOB_ID for item in graph["instances"])
    assert all(item["bronze_key"].startswith("sha256:") for item in graph["instances"])
    assert "https://catalog.example.invalid/private" not in {
        item["url"] for item in graph["instances"]
    }
    assert graph["coverage"]["uncovered_property_ids"] == []

    edge_by_url = {item["to_url"]: item for item in graph["edges"]}
    assert edge_by_url["https://catalog.example.invalid/private"]["to_instance_id"] is None
    assert edge_by_url["https://catalog.example.invalid/private"]["followed"] is False
    assert edge_by_url["https://elsewhere.example.net/records"]["risk_tier"] == "LOW"
    assert edge_by_url["https://elsewhere.example.net/records"]["method"] == "GET"

    expected_payload = json.dumps(
        graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert lake.read_key(envelope["bronze_key"]) == expected_payload
    assert envelope["bronze_key"] not in expected_payload.decode()
    artifact_path = tmp_path / "case/03-fanout/surface-map/source-synthetic/site-graph.json"
    assert json.loads(artifact_path.read_text(encoding="utf-8")) == envelope


def test_site_graph_failure_traces_are_valid_run_feed_steps(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    source_dir = case_dir / "03-fanout/sources" / SOURCE_ID
    write_json(
        source_dir / "candidate.json",
        {
            "fingerprint": FINGERPRINT,
            "capture_key": OBJECTIVE["confirmed_bronze_key"],
            "covers": ["record_id", "name"],
            "authority": "auto",
        },
    )
    lake = FileLake(tmp_path / "lake")
    decision = FakeDecision()

    def fail_capture(url: str, **kwargs: dict) -> dict:
        raise OSError("synthetic spider failure")

    def invalid_capture(url: str, **kwargs: dict) -> dict:
        return {"job_id": kwargs["job_id"], "pages": []}

    feed = RunFeed(
        lake,
        "synthetic-case",
        "mock-synthetic-run",
        PROVENANCE,
        start_heartbeat=False,
    )
    for capture in (fail_capture, invalid_capture):
        result = run_confirmed_source_spiders(
            case_dir=case_dir,
            objectives=OBJECTIVES,
            ontology=ONTOLOGY,
            decision=decision,
            lake=lake,
            run_id="mock-synthetic-run",
            capture=capture,
            provenance=PROVENANCE,
        )
        assert len(result["trace"]) == 1
        step = result["trace"][0]
        validate_document("trace-step", step)
        feed.append_step(step)
    feed.close()
    assert len(
        lake.read_key("runs/synthetic-case/mock-synthetic-run/trace.live.jsonl").splitlines()
    ) == 2


def test_p4_uses_site_graph_as_bounded_starting_context(tmp_path: Path) -> None:
    decision = FakeDecision()
    lake = FileLake(tmp_path / "lake")
    build_site_graph(
        case_dir=tmp_path,
        objective=OBJECTIVE,
        ontology=ONTOLOGY,
        decision=decision,
        lake=lake,
        run_id="synthetic-run",
        capture_result=_capture_result(lake),
    )
    prd = {"requirements": [{"id": "req-1", "description": "Read source-backed records"}]}

    local, tdd = draft_local_scope(tmp_path, prd, ONTOLOGY, OBJECTIVE, decision)

    purpose, prompt = next(call for call in decision.calls if call[0] == "phase4.local_scope")
    tdd_markdown = (tmp_path / "04-local/source-synthetic__objective-synthetic/tdd.md").read_text(
        encoding="utf-8"
    )
    assert purpose == "phase4.local_scope"
    assert "confirmed site graph is the starting path" in prompt
    assert "/records/1" in prompt
    assert "Site graph starting path bronze key:" in tdd_markdown
    assert "site-graph.json" in tdd_markdown
    assert local["source_id"] == SOURCE_ID
    assert tdd["source_url"] == SOURCE_URL


def test_confirmed_source_spider_runner_uses_bounded_policy_and_ranks_objectives(
    tmp_path: Path,
) -> None:
    case_dir = tmp_path / "case"
    source_dir = case_dir / "03-fanout/sources" / SOURCE_ID
    write_json(
        source_dir / "candidate.json",
        {
            "fingerprint": FINGERPRINT,
            "capture_key": OBJECTIVE["confirmed_bronze_key"],
            "covers": ["record_id", "name"],
            "authority": "auto",
        },
    )
    lake = FileLake(tmp_path / "lake")
    capture_result = _capture_result(lake)
    calls: list[dict] = []

    def capture(url: str, **kwargs: dict) -> dict:
        calls.append({"url": url, **kwargs})
        result = copy.deepcopy(capture_result)
        result["job_id"] = kwargs["job_id"]
        for page in result["pages"]:
            if page.get("status") is not None:
                page["job_id"] = kwargs["job_id"]
        return {
            **result,
            "trace": [],
            "page_trace": [],
            "proof": {"dispatch_result": {"status": 200}},
        }

    decision = FakeDecision()
    decision.backend = "vultr"
    live_provenance = {**PROVENANCE, "backend": "vultr"}
    result = run_confirmed_source_spiders(
        case_dir=case_dir,
        objectives=OBJECTIVES,
        ontology=ONTOLOGY,
        decision=decision,
        lake=lake,
        run_id="run-synthetic-site-graph",
        capture=capture,
        provenance=live_provenance,
    )

    assert len(calls) == 1
    assert calls[0]["url"] == SOURCE_URL
    assert calls[0]["allowed_domains"] == ["example.invalid"]
    assert calls[0]["generated_by"] == live_provenance
    assert calls[0]["spider_options"]["max_depth"] == 2
    assert calls[0]["spider_options"]["page_cap"] == 30
    assert calls[0]["spider_options"]["delay_seconds"] >= 0
    assert calls[0]["job_id"].startswith("job:")
    graph = result["graphs"][SOURCE_ID]["graph"]
    assert graph["job_ids"] == [calls[0]["job_id"]]
    assert {item["job_id"] for item in graph["instances"]} == {calls[0]["job_id"]}
    assert result["objectives"]["objectives"][0]["expected_contribution"] == 1.0
    assert len(result["jobs"]) == 1
    assert sources_needing_spider(case_dir, result["objectives"], ["record_id"]) == []
    assert sources_needing_spider(tmp_path / "fresh-case", result["objectives"], ["record_id"]) == [
        SOURCE_ID
    ]


def test_spider_runner_skips_sources_without_a_confirmed_manifest(tmp_path: Path) -> None:
    calls: list[str] = []

    def capture(url: str, **kwargs: dict) -> dict:
        calls.append(url)
        raise AssertionError("unconfirmed sources must not be crawled")

    result = run_confirmed_source_spiders(
        case_dir=tmp_path / "case",
        objectives=OBJECTIVES,
        ontology=ONTOLOGY,
        decision=FakeDecision(),
        lake=FileLake(tmp_path / "lake"),
        run_id="synthetic-run",
        capture=capture,
        provenance=PROVENANCE,
    )

    assert calls == []
    assert result["graphs"] == {}
    assert result["jobs"] == []


def test_site_graph_context_rejects_stale_fingerprint_or_ontology(tmp_path: Path) -> None:
    decision = FakeDecision()
    lake = FileLake(tmp_path / "lake")
    build_site_graph(
        case_dir=tmp_path,
        objective=OBJECTIVE,
        ontology=ONTOLOGY,
        decision=decision,
        lake=lake,
        run_id="synthetic-run",
        capture_result=_capture_result(lake),
    )

    assert site_graph_context(tmp_path, OBJECTIVE, ONTOLOGY) is not None
    assert (
        site_graph_context(tmp_path, {**OBJECTIVE, "source_fingerprint": "d" * 64}, ONTOLOGY)
        is None
    )
    assert site_graph_context(tmp_path, OBJECTIVE, {**ONTOLOGY, "version": "new-version"}) is None


def test_ranker_ignores_stale_graphs(tmp_path: Path) -> None:
    decision = FakeDecision()
    lake = FileLake(tmp_path / "lake")
    build_site_graph(
        case_dir=tmp_path,
        objective=OBJECTIVE,
        ontology=ONTOLOGY,
        decision=decision,
        lake=lake,
        run_id="synthetic-run",
        capture_result=_capture_result(lake),
    )
    changed = copy.deepcopy(OBJECTIVES)
    changed["objectives"][0]["source_fingerprint"] = "d" * 64

    ranked = rank_objectives_by_site_graph(tmp_path, changed, ONTOLOGY)

    assert ranked["objectives"][0]["expected_contribution"] == 0.0
