"""Off-domain P5 downloads require a digest-bound source decision."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from jsonschema import ValidationError

from ontofill.case.checkpoints import ApprovalArtifactMismatch, load_verified_approval
from ontofill.contracts import validate_document
from ontofill.inference import RecordedDecisionClient
from ontofill.lake import FileLake
from ontofill.phases.p5_execute.phase import _document_format, _format, execute_objective
from ontofill.phases.p5_execute.source_review import SourceReviewPending
from ontofill.refiner import MemorySilverStore
from tests.r17_helpers import SyntheticParseExecutor

_PROVENANCE = {
    "backend": "vultr",
    "model": "synthetic-vultr",
    "at": "2026-09-27T00:00:00Z",
}
_PARENT_URL = "https://registry.example.test/catalog"
_LINK_URL = "https://blob.example.test/exports/registry.csv"
_SOURCE_ID = "source-registry"
_OBJECTIVE_ID = "objective-registry"
_TDD = {
    "allowed_domains": ["registry.example.test"],
    "target_fields": ["record_id"],
    "target_volume": 10,
}
_ONTOLOGY = {
    "classes": [
        {
            "id": "registry_record",
            "identifier_property": "record_id",
            "title_property": "record_id",
        }
    ],
    "properties": [{"id": "record_id", "domain": "registry_record", "datatype": "string"}],
}
_MAPPING = {
    "class_id": "registry_record",
    "columns": [{"header": "record_id", "property_id": "record_id"}],
}


def _make_case(tmp_path, html: str):
    lake = FileLake(tmp_path / "lake")
    html_state = {"value": html}
    fetch_calls: list[tuple[str, dict]] = []
    objective = {
        "id": _OBJECTIVE_ID,
        "source_id": _SOURCE_ID,
        "source_url": _PARENT_URL,
        "source_type": "public_registry",
    }
    tdd = dict(_TDD)
    (tmp_path / "01-scope").mkdir()
    (tmp_path / "01-scope/prd.json").write_text(
        '{"authority_policy":{"trusted_publishers":[{"kind":"public_registry",'
        '"tier":"primary","domains":["registry.example.test"]}]}}',
        encoding="utf-8",
    )

    def capture(url: str, **kwargs) -> dict:
        current_html = html_state["value"]
        page_key = lake.put_bytes(current_html.encode())
        screenshot_key = lake.put_bytes(b"synthetic screenshot")
        return {
            "url": url,
            "html": current_html,
            "html_key": page_key,
            "screenshot_key": screenshot_key,
            "trace": [
                {
                    "step_id": "step:trusted-page",
                    "run_id": kwargs["run_id"],
                    "phase": 5,
                    "source_id": kwargs["source_id"],
                    "objective_id": kwargs["objective_id"],
                    "tdd_path": kwargs["tdd_path"],
                    "mode": "D0",
                    "observed": {"url": url},
                    "requested": {"url": url},
                    "executed": {"html_key": page_key},
                    "evaluated": {"status": "captured"},
                    "parent_step_id": None,
                    "value_ids": [],
                    "ts": datetime.now(UTC).isoformat(),
                    "generated_by": _PROVENANCE,
                }
            ],
        }

    def fetch(url: str, **kwargs) -> dict:
        fetch_calls.append((url, kwargs))
        payload = b"record_id\nR-1\n"
        key = lake.put_bytes(payload, {"content_type": "text/csv", "url": url})
        return {
            "url": url,
            "bronze_key": key,
            "content_type": "text/csv",
            "status": 200,
            "trace": [
                {
                    "step_id": "step:document-fetch",
                    "run_id": kwargs["run_id"],
                    "phase": 5,
                    "source_id": kwargs["source_id"],
                    "objective_id": kwargs["objective_id"],
                    "tdd_path": kwargs["tdd_path"],
                    "mode": "D0",
                    "observed": {"url": url, "status": 200},
                    "requested": {"url": url},
                    "executed": {"network_request": True, "bronze_key": key},
                    "evaluated": {"status": "captured"},
                    "parent_step_id": None,
                    "value_ids": [],
                    "ts": datetime.now(UTC).isoformat(),
                    "generated_by": _PROVENANCE,
                }
            ],
        }

    def run(decision: RecordedDecisionClient, *, feed=None):
        return execute_objective(
            case_dir=tmp_path,
            objective=objective,
            ontology=_ONTOLOGY,
            tdd=tdd,
            lake=lake,
            run_id="run-r40",
            decision=decision,
            store=MemorySilverStore(),
            provenance=_PROVENANCE,
            capture=capture,
            fetch=fetch,
            feed=feed,
            parse_executor=SyntheticParseExecutor(),
        )

    return lake, html_state, fetch_calls, objective, tdd, run


def _source_approval(case_dir, candidate_path, decision: str = "approve") -> None:
    raw = candidate_path.read_bytes()
    candidate = json.loads(raw)
    relative = candidate_path.relative_to(case_dir).as_posix()
    marker = {
        "approver": "synthetic reviewer",
        "date": "2026-09-27",
        "checkpoint": "source",
        "source_fingerprint": candidate["fingerprint"],
        "artifact_sha256": {relative: hashlib.sha256(raw).hexdigest()},
        "identity_source": "local",
        "decision": decision,
    }
    if decision == "deny":
        marker["reason"] = "Synthetic reviewer denied this source."
    validate_document("approved", marker)
    (candidate_path.parent / "APPROVED").write_text(
        json.dumps(marker, indent=2) + "\n", encoding="utf-8"
    )


def test_off_domain_link_pauses_with_bronze_and_screenshot_provenance(tmp_path) -> None:
    _, _, fetch_calls, _, _, run = _make_case(
        tmp_path, f'<main><a href="{_LINK_URL}">Download registry data</a></main>'
    )

    with pytest.raises(SourceReviewPending) as stopped:
        run(RecordedDecisionClient({}))

    candidates = list((tmp_path / "03-fanout/sources").glob("*/candidate.json"))
    assert not fetch_calls
    assert len(candidates) == 1
    candidate = json.loads(candidates[0].read_text(encoding="utf-8"))
    assert candidate["url"] == _LINK_URL
    assert candidate["link_provenance"]["link_url"] == candidate["url"]
    assert candidate["link_provenance"]["parent_source_id"] == _SOURCE_ID
    assert candidate["link_provenance"]["parent_page_url"] == _PARENT_URL
    assert candidate["link_provenance"]["parent_capture_key"].startswith("sha256:")
    assert candidate["link_provenance"]["step_id"] == "step:trusted-page"
    assert candidate["screenshot_key"].startswith("sha256:")
    assert stopped.value.directory == candidates[0].parent
    assert stopped.value.trace[-1]["executed"]["network_request"] is False
    assert (candidates[0].parent / "APPROVAL_PENDING.md").is_file()


def test_approval_reuses_unchanged_parent_capture_and_fetches_exact_host(tmp_path) -> None:
    _, _, fetch_calls, _, _, run = _make_case(
        tmp_path, f'<main><a href="{_LINK_URL}">Download registry data</a></main>'
    )
    with pytest.raises(SourceReviewPending):
        run(RecordedDecisionClient({}))
    candidate_path = next((tmp_path / "03-fanout/sources").glob("*/candidate.json"))
    original_packet = candidate_path.read_bytes()
    _source_approval(tmp_path, candidate_path)

    decision = RecordedDecisionClient(
        {
            "phase5.select_download": [{"index": 0}],
            "phase5.map_columns": [_MAPPING],
        }
    )
    result = run(decision)

    assert fetch_calls and fetch_calls[0][0] == _LINK_URL
    assert fetch_calls[0][1]["allowed_domains"] == ["blob.example.test"]
    assert fetch_calls[0][1]["exact_hosts"] == ["blob.example.test"]
    assert fetch_calls[0][1]["phase"] == 5
    assert candidate_path.read_bytes() == original_packet
    assert len(result.sandbox_jobs) >= 4  # page and document fetches plus both parse pods


def test_changed_parent_capture_never_reuses_approved_link(tmp_path) -> None:
    _, html_state, fetch_calls, _, _, run = _make_case(
        tmp_path, f'<main><a href="{_LINK_URL}">Download registry data</a></main>'
    )
    with pytest.raises(SourceReviewPending):
        run(RecordedDecisionClient({}))
    candidate_path = next((tmp_path / "03-fanout/sources").glob("*/candidate.json"))
    _source_approval(tmp_path, candidate_path)
    before = candidate_path.read_bytes()
    html_state["value"] = f'<main><a href="{_LINK_URL}">Updated registry data</a></main>'

    with pytest.raises(ApprovalArtifactMismatch):
        run(RecordedDecisionClient({"phase5.select_download": [{"index": 0}]}))

    assert candidate_path.read_bytes() == before
    assert fetch_calls == []


def test_denial_skips_link_without_rewriting_pending_file(tmp_path) -> None:
    _, _, fetch_calls, _, tdd, run = _make_case(
        tmp_path, f'<main><a href="{_LINK_URL}">Download registry data</a></main>'
    )
    with pytest.raises(SourceReviewPending):
        run(RecordedDecisionClient({}))
    candidate_path = next((tmp_path / "03-fanout/sources").glob("*/candidate.json"))
    pending_path = candidate_path.parent / "APPROVAL_PENDING.md"
    pending_bytes = pending_path.read_bytes()
    _source_approval(tmp_path, candidate_path, "deny")
    tdd["membership"] = {"property_id": "record_exists"}

    result = run(RecordedDecisionClient({}))

    assert result.observations == []
    assert fetch_calls == []
    assert pending_path.read_bytes() == pending_bytes


def test_stale_approved_candidate_fails_closed_without_writes_or_fetch(tmp_path) -> None:
    _, _, fetch_calls, _, _, run = _make_case(
        tmp_path, f'<main><a href="{_LINK_URL}">Download registry data</a></main>'
    )
    with pytest.raises(SourceReviewPending):
        run(RecordedDecisionClient({}))
    candidate_path = next((tmp_path / "03-fanout/sources").glob("*/candidate.json"))
    _source_approval(tmp_path, candidate_path)
    candidate_path.write_bytes(candidate_path.read_bytes() + b" ")
    candidate_before = candidate_path.read_bytes()
    pending_path = candidate_path.parent / "APPROVAL_PENDING.md"
    pending_before = pending_path.read_bytes()

    with pytest.raises(ApprovalArtifactMismatch):
        run(RecordedDecisionClient({}))

    assert candidate_path.read_bytes() == candidate_before
    assert pending_path.read_bytes() == pending_before
    assert fetch_calls == []


def test_same_domain_download_remains_on_tdd_allowlist(tmp_path) -> None:
    same_domain_url = "https://registry.example.test/downloads/registry.csv"
    _, _, fetch_calls, _, _, run = _make_case(
        tmp_path, f'<main><a href="{same_domain_url}">Download registry data</a></main>'
    )
    decision = RecordedDecisionClient(
        {
            "phase5.select_download": [{"index": 0}],
            "phase5.map_columns": [_MAPPING],
        }
    )

    run(decision)

    assert len(fetch_calls) == 1
    assert fetch_calls[0][0] == same_domain_url
    assert fetch_calls[0][1]["allowed_domains"] == ["registry.example.test"]
    assert "exact_hosts" not in fetch_calls[0][1]
    assert not (tmp_path / "03-fanout/sources").exists()


def test_page_review_batches_12_unique_links_and_deduplicates(tmp_path) -> None:
    links = [f"https://blob.example.test/files/{index}.csv" for index in range(12)]
    html = "<main>" + "".join(
        f'<a href="{url}">Data {index}</a>' for index, url in enumerate(links)
    )
    html += (
        f'<a href="{links[0]}">Repeated first link</a>'
        f'<a href="{links[0].replace("blob", "BLOB")}">Same link with host case changed</a></main>'
    )
    _, _, fetch_calls, _, _, run = _make_case(tmp_path, html)

    with pytest.raises(SourceReviewPending) as stopped:
        run(RecordedDecisionClient({}))

    candidates = sorted((tmp_path / "03-fanout/sources").glob("*/candidate.json"))
    assert len(candidates) == 12
    assert len(stopped.value.review_directories) == 12
    for path in candidates:
        candidate = json.loads(path.read_text(encoding="utf-8"))
        pending_text = (path.parent / "APPROVAL_PENDING.md").read_text(encoding="utf-8")
        assert candidate["fingerprint"] in pending_text
        relative = path.relative_to(tmp_path).as_posix()
        assert relative in pending_text
    assert not fetch_calls
    for path in candidates:
        candidate = json.loads(path.read_text(encoding="utf-8"))
        decision = "deny" if candidate["url"] == links[0] else "approve"
        _source_approval(tmp_path, path, decision)
        relative = path.relative_to(tmp_path).as_posix()
        approval = load_verified_approval(path.parent / "APPROVED", tmp_path, [relative], "source")
        assert (
            approval["artifact_sha256"][relative] == hashlib.sha256(path.read_bytes()).hexdigest()
        )

    result = run(
        RecordedDecisionClient(
            {
                "phase5.select_download": [{"index": 0}],
                "phase5.map_columns": [_MAPPING],
            }
        )
    )
    assert result.observations
    assert len(fetch_calls) == 1
    assert fetch_calls[0][0] == links[1]
    assert fetch_calls[0][1]["allowed_domains"] == ["blob.example.test"]
    assert fetch_calls[0][1]["exact_hosts"] == ["blob.example.test"]


def test_page_review_records_eligible_links_omitted_past_packet_ceiling(tmp_path) -> None:
    links = [f"https://blob.example.test/files/{index}.csv" for index in range(305)]
    html = (
        "<main>"
        + "".join(f'<a href="{url}">Data {index}</a>' for index, url in enumerate(links))
        + "</main>"
    )
    _, _, fetch_calls, _, _, run = _make_case(tmp_path, html)

    with pytest.raises(SourceReviewPending) as stopped:
        run(RecordedDecisionClient({}))

    candidates = list((tmp_path / "03-fanout/sources").glob("*/candidate.json"))
    assert len(candidates) == 300
    omissions = [
        step
        for step in stopped.value.trace
        if step["requested"].get("tool") == "source.review.omissions"
    ]
    assert len(omissions) == 1
    assert omissions[0]["evaluated"]["omitted_count"] == 5
    assert omissions[0]["executed"]["network_request"] is False
    validate_document("trace-step", omissions[0])
    assert not fetch_calls


def test_legacy_xls_extension_and_mime_are_recognized() -> None:
    url = "https://blob.example.test/exports/legacy.xls"
    assert _format(url) == "xls"
    assert _document_format(url, "application/vnd.ms-excel") == "xls"
    assert (
        _document_format("https://blob.example.test/exports/data.xlsx", "application/vnd.ms-excel")
        == "xlsx"
    )


def test_malformed_source_approval_fails_closed_without_writes(tmp_path) -> None:
    candidate_path = tmp_path / "03-fanout/sources/source-link-test/candidate.json"
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_text('{"fingerprint":"' + "a" * 64 + '"}\n', encoding="utf-8")
    relative = candidate_path.relative_to(tmp_path).as_posix()
    marker_path = candidate_path.parent / "APPROVED"
    marker_path.write_text(
        json.dumps(
            {
                "approver": "Synthetic reviewer",
                "date": "2026-09-27",
                "checkpoint": "source",
                "source_fingerprint": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    before = (candidate_path.read_bytes(), marker_path.read_bytes())

    with pytest.raises(ApprovalArtifactMismatch):
        load_verified_approval(marker_path, tmp_path, [relative], "source")

    assert (candidate_path.read_bytes(), marker_path.read_bytes()) == before
    assert not (candidate_path.parent / "APPROVAL_PENDING.md").exists()


def test_source_approval_schema_requires_digest_and_identity() -> None:
    marker = {
        "approver": "Synthetic reviewer",
        "date": "2026-09-27",
        "checkpoint": "source",
        "source_fingerprint": "a" * 64,
    }
    with pytest.raises(ValidationError):
        validate_document("approved", marker)

    marker["artifact_sha256"] = {"03-fanout/sources/example/candidate.json": "b" * 64}
    marker["identity_source"] = "local"
    validate_document("approved", marker)
