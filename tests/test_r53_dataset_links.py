"""R53 follows relevant data files from captured official open-data pages."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from openpyxl import Workbook

from ontofill.contracts import validate_document
from ontofill.inference import RecordedDecisionClient
from ontofill.lake import FileLake
from ontofill.phase_loop import LoopBudget
from ontofill.phases.p3_fanout.discovery_loop import DiscoveryLoop, _dataset_index_links
from ontofill.phases.p5_execute.source_review import reviewable_download_host
from ontofill.sandbox import ParseExecution
from tests.r17_helpers import (
    _HOST,
    _ISOLATION,
    _POD,
    _SECRETS,
    _TEARDOWN,
    SyntheticParseExecutor,
)
from tests.test_discovery_loop import StaticProvider, _library_case

_INDEX = "https://libraries.example.test/open-data/index"
_CSV = "https://libraries.example.test/datasets/branches.csv"
_XLS = "https://libraries.example.test/datasets/branches.xls"
_XLSX = "https://libraries.example.test/datasets/branches.xlsx"
_ZIP = "https://libraries.example.test/datasets/branches.zip"
_OFF_HOST = "https://files.other.example.test/datasets/branches.zip"
_PROVENANCE = {"backend": "recorded", "model": "synthetic", "at": "2026-09-27T00:00:00Z"}


def _index_candidate() -> dict:
    return {
        "source_id": "source-library",
        "url": _INDEX,
        "capture_key": "sha256:" + "a" * 64,
        "property_ids": ["opening_hours"],
    }


def _index_ontology() -> dict:
    return {
        "primary_class": "library_branch",
        "classes": [
            {
                "id": "library_branch",
                "label": "Library branch",
                "label_plural": "Library branches",
                "identifier_property": "branch_id",
                "title_property": "branch_name",
            }
        ],
        "properties": [
            {"id": "opening_hours", "label": "Opening hours"},
            {"id": "branch_name", "label": "Branch name"},
            {"id": "branch_id", "label": "Branch ID"},
        ],
    }


def test_index_admits_relevant_spanish_csv_without_english_cue() -> None:
    links = _dataset_index_links(
        _index_candidate(),
        {
            "links": [
                {
                    "url": "https://libraries.example.test/datos/Incumplidos_2024.csv",
                    "text": "Relación de sucursales incumplidas",
                },
                {
                    "url": "https://libraries.example.test/prensa/comunicado.csv",
                    "text": "Comunicado de prensa",
                },
            ]
        },
        _index_ontology(),
    )
    assert [item["url"] for item in links] == [
        "https://libraries.example.test/datos/Incumplidos_2024.csv"
    ]


def test_network_observed_documents_use_same_bounded_gate() -> None:
    links = _dataset_index_links(
        _index_candidate(),
        {
            "network_requests": [
                {
                    "url": "https://libraries.example.test/api/library-branches.json",
                    "method": "GET",
                    "resource_type": "xhr",
                },
                {
                    "url": "https://libraries.example.test/api/library-branches.json?sig=secret-value",
                    "method": "GET",
                    "content_type": "application/json",
                    "resource_type": "xhr",
                },
                {
                    "url": "https://libraries.example.test/api/library-branches.json",
                    "method": "POST",
                    "status": 200,
                    "content_type": "application/json",
                    "resource_type": "xhr",
                },
                {
                    "url": "https://libraries.example.test/api/help.json",
                    "method": "GET",
                    "status": 200,
                    "content_type": "application/json",
                    "resource_type": "xhr",
                },
                {
                    "url": "https://libraries.example.test/api/library-branches.json?signature=secret-value",
                    "method": "GET",
                    "status": 200,
                    "content_type": "application/json",
                    "resource_type": "xhr",
                },
            ]
        },
        _index_ontology(),
    )
    assert [item["url"] for item in links] == [
        "https://libraries.example.test/api/library-branches.json"
    ]
    assert links[0]["parent_capture_key"] == _index_candidate()["capture_key"]


def test_auth_query_keys_never_enter_link_review() -> None:
    for key in ("sig", "signature", "api_key", "access_token"):
        url = f"https://files.other.example.test/branches.csv?{key}=synthetic-value"
        assert reviewable_download_host(url, []) is None
        assert (
            _dataset_index_links(
                _index_candidate(),
                {"links": [{"url": url, "text": "Library branches dataset"}]},
                _index_ontology(),
            )
            == []
        )


def test_captured_403_challenge_is_inconclusive_before_generic_retry(tmp_path: Path) -> None:
    ontology = _library_case(tmp_path)
    lake = FileLake(tmp_path / "lake")
    capture = _Capture(lake)

    def challenge(url: str, **kwargs) -> dict:
        result = capture(url, **kwargs)
        result.update(status=403, capture_reason="bot_challenge")
        return result

    loop = DiscoveryLoop(
        [StaticProvider("synthetic", {_INDEX: ("opening_hours",)})],
        capture=challenge,
        lake=lake,
        run_id="mock-r53-challenge",
        provenance=_PROVENANCE,
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
        parse_executor=SyntheticParseExecutor(),
    )
    with pytest.raises(ValueError, match="confirmed no source candidates"):
        loop.discover_sources(
            tmp_path, ontology, RecordedDecisionClient({}), gaps=("opening_hours",)
        )
    ledger = json.loads((tmp_path / "03-fanout/surface-map/leads.json").read_text())
    candidate = next(item for item in ledger["candidates"] if item["url"] == _INDEX)
    assert candidate["capture_reason"] == "bot_challenge"
    assert candidate["status"] == "inconclusive"
    assert candidate["alternate_channel_hint"]
    assert candidate["capture_attempts"] == 1


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Branches"
    sheet.append(["Branch name", "Free internet", "Opening hours"])
    sheet.append(["Example Branch", "Yes", "Weekdays"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class _Capture:
    def __init__(self, lake: FileLake) -> None:
        self.lake = lake
        self.calls: list[tuple[str, dict]] = []
        self.documents = {
            _CSV: (
                b"Branch name,Free internet,Opening hours\nExample Branch,Yes,Weekdays\n",
                "text/csv",
            ),
            _XLS: (
                (Path(__file__).parent / "fixtures/r40_synthetic.xls").read_bytes(),
                "application/vnd.ms-excel",
            ),
            _XLSX: (
                _xlsx_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            _ZIP: (b"PK\x03\x04synthetic unsupported archive", "application/zip"),
        }

    def __call__(self, url: str, **kwargs) -> dict:
        self.calls.append((url, kwargs))
        host = urlsplit(url).hostname or ""
        assert host in kwargs["allowed_domains"]
        if url in {_CSV, _XLS, _XLSX, _ZIP}:
            assert kwargs.get("exact_hosts") == [host]
        step_id = f"step:{uuid.uuid4().hex}"
        timestamp = datetime.now(UTC).isoformat()
        if url == _INDEX:
            body = (
                "<html><body><h1>Official Open Data Datasets</h1>"
                f'<a href="{_CSV}">Library branch entity records CSV: Opening hours</a>'
                f'<a href="{_XLS}">Library branch entity records XLS: Opening hours</a>'
                f'<a href="{_XLSX}">Library branch entity records workbook XLSX: Opening hours</a>'
                f'<a href="{_ZIP}">Library branch entity archive ZIP: Opening hours</a>'
                f'<a href="{_OFF_HOST}">Library branch entity archive ZIP: Opening hours</a>'
                '<a href="/help.html">Help</a>'
                '<a href="https://news.example.test/releases.csv">Press releases CSV</a>'
                '<a href="/annual-report.pdf">Annual report PDF</a>'
                "</body></html>"
            )
            key = self.lake.put_bytes(body.encode())
            capture_fields = {"html_key": key}
            executed = {"html_key": key}
        elif url in self.documents:
            payload, content_type = self.documents[url]
            key = self.lake.put_bytes(payload)
            capture_fields = {
                "document_key": key,
                "document_content_type": content_type,
                "document_size_bytes": len(payload),
            }
            executed = {"document_key": key}
        else:
            assert url == "https://libraries.example.test/"
            body = "<html><body><h1>City office home</h1></body></html>"
            key = self.lake.put_bytes(body.encode())
            capture_fields = {"html_key": key}
            executed = {"html_key": key}
        trace = {
            "step_id": step_id,
            "run_id": kwargs["run_id"],
            "phase": 3,
            "source_id": kwargs["source_id"],
            "objective_id": None,
            "tdd_path": kwargs["tdd_path"],
            "mode": "D0",
            "observed": {"url": url, "status": 200},
            "requested": {"url": url},
            "executed": {"network_request": True, **executed},
            "evaluated": {"status": "captured"},
            "parent_step_id": None,
            "value_ids": [],
            "ts": timestamp,
            "generated_by": kwargs["generated_by"],
        }
        return {
            "url": url,
            "redirect_chain": [url],
            "status": 200,
            "screenshot_key": self.lake.put_bytes(b"synthetic screenshot")
            if url == _INDEX
            else None,
            "trace": [trace],
            **capture_fields,
        }


class _ParseRecorder(SyntheticParseExecutor):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes]] = []

    def run(self, payload, *, kind, format, max_rows, base_url, limits):
        self.calls.append((kind, format, bytes(payload)))
        if format == "auto" and payload.startswith(b"PK\x03\x04"):
            return ParseExecution(
                output={"ok": False, "kind": "auto", "error": {"code": "unknown_document_format"}},
                host=_HOST,
                pod=_POD,
                isolation=_ISOLATION,
                secrets=_SECRETS,
                teardown=_TEARDOWN,
            )
        return super().run(
            payload,
            kind=kind,
            format=format,
            max_rows=max_rows,
            base_url=base_url,
            limits=limits,
        )


def test_open_data_index_follows_bounded_entity_files_and_reviews_off_host(tmp_path: Path) -> None:
    ontology = _library_case(tmp_path)
    lake = FileLake(tmp_path / "lake")
    capture = _Capture(lake)
    parser = _ParseRecorder()
    loop = DiscoveryLoop(
        [StaticProvider("synthetic", {_INDEX: ("opening_hours",)})],
        capture=capture,
        lake=lake,
        run_id="mock-r53-links",
        provenance=_PROVENANCE,
        budget=LoopBudget(max_iterations=1, wall_seconds=60),
        parse_executor=parser,
    )

    result = loop.discover_sources(
        tmp_path, ontology, RecordedDecisionClient({}), gaps=("opening_hours",)
    )

    captured_urls = [url for url, _ in capture.calls]
    assert (
        _CSV in captured_urls
        and _XLS in captured_urls
        and _XLSX in captured_urls
        and _ZIP in captured_urls
    )
    assert _OFF_HOST not in captured_urls
    assert "https://news.example.test/releases.csv" not in captured_urls
    document_calls = [call for call in parser.calls if call[0] != "html"]
    assert len(document_calls) >= 4
    assert any(call[2].startswith(b"Branch name,Free internet") for call in document_calls)
    assert any(call[2].startswith(b"\xd0\xcf\x11\xe0") for call in document_calls)
    assert any(call[2].startswith(b"PK\x03\x04") for call in document_calls)
    assert any(call[2].startswith(b"PK\x03\x04") for call in parser.calls)
    parsed_formats = {
        step.get("executed", {}).get("format")
        for step in loop.trace
        if step.get("requested", {}).get("tool") == "file.parse"
        and step.get("evaluated", {}).get("status") == "parsed"
    }
    assert {"csv", "xls", "xlsx"}.issubset(parsed_formats)
    assert all(
        step.get("executed", {}).get("row_count", 0) > 0
        for step in loop.trace
        if step.get("requested", {}).get("tool") == "file.parse"
        and step.get("evaluated", {}).get("status") == "parsed"
        and step.get("executed", {}).get("format") in {"csv", "xls", "xlsx"}
    )
    assert any(
        step.get("requested", {}).get("tool") == "file.parse"
        and step.get("executed", {}).get("reason") == "parse_pod_returned_invalid_detected_format"
        for step in loop.trace
    ), repr([step for step in loop.trace if step.get("requested", {}).get("tool") == "file.parse"])
    assert result["objectives"]

    off_host_packets = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "03-fanout/sources").glob("*/candidate.json")
        if json.loads(path.read_text(encoding="utf-8")).get("url") == _OFF_HOST
    ]
    assert len(off_host_packets) == 1
    packet = off_host_packets[0]
    validate_document("source-candidate", packet)
    assert packet["provider"] == "sandbox_page_link"
    assert packet["link_provenance"]["link_url"] == _OFF_HOST
    assert packet["url"].endswith(".zip")
    assert (
        packet["source_id"]
        == "source-link-"
        + hashlib.sha256(
            json.dumps(
                [packet["link_provenance"]["parent_source_id"], packet["url"]],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:20]
    )
    review_dir = tmp_path / "03-fanout/sources" / packet["source_id"]
    assert (review_dir / "APPROVAL_PENDING.md").is_file()
    assert packet["link_provenance"]["parent_capture_key"].startswith("sha256:")
    assert any(
        step.get("requested", {}).get("tool") == "p3.dataset_link.review"
        and step.get("requested", {}).get("url") == _OFF_HOST
        and step.get("executed", {}).get("network_request") is False
        and step.get("evaluated", {}).get("status") == "pending"
        for step in loop.trace
    )
    assert (
        packet["fingerprint"]
        == hashlib.sha256(
            json.dumps(
                {
                    "url": packet["url"],
                    "title": packet["title"],
                    "snippet": packet["snippet"],
                    "provider": packet["provider"],
                    "capture_key": packet["capture_key"],
                    "authority_policy": json.loads((tmp_path / "01-scope/prd.json").read_text())[
                        "authority_policy"
                    ],
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
    )
