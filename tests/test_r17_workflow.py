"""Synthetic CKAN wiring stays on the fetch and parse sandbox boundaries."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ontofill import workflow
from ontofill.phases.p3_fanout.leads import CkanLeadProvider, JsonCache
from ontofill.workflow import _SandboxCkanJsonFetcher


def test_ckan_fetcher_uses_exact_domain_and_copies_sandbox_proofs() -> None:
    fetched_calls = []
    parsed_calls = []
    document = {"success": True, "result": {"results": []}}
    fetch_job = {"proof": {"fixture": "fetch"}, "trace": [{"step_id": "step:fetch"}]}
    parse_job = {"job_id": "job:parse", "checkpoints": {"task": {"ok": True}}}

    def fetch(url, **kwargs):
        fetched_calls.append((url, kwargs))
        return {
            "bronze_key": "sha256:" + "a" * 64,
            "trace": fetch_job["trace"],
            **fetch_job,
        }

    def parse(lake, key, **kwargs):
        parsed_calls.append((lake, key, kwargs))
        return SimpleNamespace(
            document=document,
            trace=({"step_id": "step:parse"},),
            job_record=parse_job,
        )

    fetcher = _SandboxCkanJsonFetcher(
        lake=object(),
        run_id="mock-ckan-run",
        provenance={"backend": "recorded", "model": "fixture", "at": "2026-09-26T12:00:00Z"},
        fetch=fetch,
        parse=parse,
    )
    provider = CkanLeadProvider(fetch_json=fetcher, cache=JsonCache(None))

    actual = provider._search("catalog.example.test", "public parks")

    assert actual == document
    url, kwargs = fetched_calls[0]
    assert url.startswith("https://catalog.example.test/api/3/action/package_search?")
    assert kwargs["allowed_domains"] == ["catalog.example.test"]
    assert kwargs["include_bytes"] is False
    assert kwargs["phase"] == 3
    _, key, parse_kwargs = parsed_calls[0]
    assert key.startswith("sha256:")
    assert parse_kwargs["run_id"] == "mock-ckan-run"
    assert [step["step_id"] for step in provider.trace] == ["step:fetch", "step:parse"]
    assert provider.jobs == [fetcher.jobs[0], parse_job]


def test_ckan_fetcher_rejects_host_escape_before_dispatch() -> None:
    calls = []
    fetcher = _SandboxCkanJsonFetcher(
        lake=object(),
        run_id="mock-ckan-run",
        provenance={"backend": "recorded", "model": "fixture", "at": "2026-09-26T12:00:00Z"},
        fetch=lambda *args, **kwargs: calls.append((args, kwargs)),
        parse=lambda *args, **kwargs: None,
    )

    with pytest.raises(ValueError, match="approved catalog endpoint"):
        fetcher("https://other.example.test/api/3/action/package_search", "catalog.example.test")
    assert calls == []


def test_live_ckan_uses_byte_fetch_and_publishes_trace_once(monkeypatch) -> None:
    fetched_calls = []
    published = []
    trace = {"step_id": "step:ckan-fetch", "evaluated": {"status": "captured"}}

    def fetch(url, **kwargs):
        fetched_calls.append((url, kwargs))
        kwargs["on_trace"](trace)
        return {"bronze_key": "sha256:" + "a" * 64, "trace": [trace]}

    monkeypatch.setattr(workflow, "fetch_url", fetch)
    fetcher, published_ids = workflow._ckan_fetcher_with_live_trace(
        lake=object(),
        run_id="mock-ckan-run",
        provenance={"backend": "recorded", "model": "fixture", "at": "2026-09-26T12:00:00Z"},
        publish=published.append,
    )
    fetcher.parse = lambda *args, **kwargs: SimpleNamespace(
        document={"success": True, "result": {"results": []}},
        trace=(),
        job_record={"job_id": "job:ckan-parse"},
    )

    payload = fetcher(
        "https://catalog.example.test/api/3/action/package_search?q=parks", "catalog.example.test"
    )

    assert payload["success"] is True
    assert fetched_calls[0][1]["include_bytes"] is False
    assert fetched_calls[0][1]["allowed_domains"] == ["catalog.example.test"]
    assert published == [trace]
    assert published_ids == {"step:ckan-fetch"}
    assert fetcher.trace == [trace]
