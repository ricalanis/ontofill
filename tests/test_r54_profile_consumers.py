"""A complete pod profile reaches P3 and P5 with literal row receipts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

from profiler import profile_bytes

from ontofill.inference import RecordedDecisionClient, generated_by
from ontofill.lake import FileLake
from ontofill.phases.p3_fanout.discovery_loop import _document_sheet_preview
from ontofill.phases.p5_execute import execute_objective
from ontofill.refiner import MemorySilverStore
from ontofill.sandbox.profile_tables import profile_to_parsed_file
from tests.profiler_helpers import make_pdf
from tests.r17_helpers import SyntheticParseExecutor


def _pdf_profile() -> dict:
    return {
        "format": "pdf",
        "complete": True,
        "table_count": 1,
        "row_receipts_truncated": False,
        "tables": [
            {
                "sheet": "pages_1",
                "page": 1,
                "row_count": 2,
                "headers": ["Entity ID", "Public name"],
                "granularity": "entity",
                "rows": [
                    {
                        "row_number": 2,
                        "page": 1,
                        "values": {"Entity ID": "A-1", "Public name": "A"},
                    },
                    {
                        "row_number": 3,
                        "page": 2,
                        "values": {"Entity ID": "B-2", "Public name": "B"},
                    },
                ],
            }
        ],
    }


def test_pdf_profile_headers_and_samples_reach_p3() -> None:
    parsed = SimpleNamespace(format="pdf", profile=_pdf_profile(), rows=())

    preview = _document_sheet_preview(parsed)

    assert preview[0]["headers"] == ["Entity ID", "Public name"]
    assert preview[0]["row_count"] == 2
    assert [row["values"] for row in preview[0]["sample_rows"]] == [["A-1", "A"], ["B-2", "B"]]


def test_pdf_profile_rows_keep_absolute_page_and_row_for_p5() -> None:
    parsed = SimpleNamespace(format="pdf", profile=_pdf_profile())

    adapted = profile_to_parsed_file(parsed)

    assert adapted is not None
    table, pages = adapted
    assert table.format == "pdf"
    assert len(table.rows) == 3  # a header plus two literal rows
    assert table.rows[2].values == ("B-2", "B")
    assert pages[("pages_1", 3)] == 2


def test_incomplete_or_aggregate_pdf_profile_cannot_be_extracted() -> None:
    profile = _pdf_profile()
    profile["row_receipts_truncated"] = True
    parsed = SimpleNamespace(format="pdf", profile=profile, rows=())
    assert _document_sheet_preview(parsed) == []
    assert profile_to_parsed_file(parsed) is None

    profile["row_receipts_truncated"] = False
    profile["tables"][0]["granularity"] = "aggregate"
    assert profile_to_parsed_file(parsed) is None


def test_p5_pdf_mapping_emits_literal_values_with_page_receipts(tmp_path) -> None:
    pdf = make_pdf(
        [
            ["Entity ID       Public name", "A-1             Alpha"],
            ["Entity ID       Public name", "B-2             Beta"],
        ]
    )
    lake = FileLake(tmp_path / "lake")
    pdf_key = lake.put_bytes(pdf)
    html = '<html><a href="/records.pdf">Download entity records</a></html>'
    html_key = lake.put_bytes(html.encode())
    screenshot = lake.put_bytes(b"synthetic screenshot")
    decision = RecordedDecisionClient(
        {
            "phase5.select_download": [{"index": 0}],
            "phase5.map_columns": [
                {
                    "class_id": "library",
                    "columns": [
                        {"header": "Entity ID", "property_id": "id"},
                        {"header": "Public name", "property_id": "title"},
                    ],
                }
            ],
        }
    )
    provenance = generated_by(decision)

    class ProfiledExecutor(SyntheticParseExecutor):
        def run(self, payload, *, kind, format, max_rows, base_url, limits):
            execution = super().run(
                payload,
                kind=kind,
                format=format,
                max_rows=max_rows,
                base_url=base_url,
                limits=limits,
            )
            if kind == "pdf":
                output = {**execution.output, "profile": profile_bytes(payload)}
                return replace(execution, output=output)
            return execution

    def trace(step_id, url, key):
        return [
            {
                "step_id": step_id,
                "run_id": "mock-profile",
                "phase": 5,
                "source_id": "source-test",
                "objective_id": "objective-test",
                "tdd_path": "04-local/source-test__objective-test/tdd.json",
                "mode": "S1",
                "observed": {"url": url, "status": 200},
                "requested": {"url": url},
                "executed": {"bronze_key": key},
                "evaluated": {"status": "captured"},
                "parent_step_id": None,
                "value_ids": [],
                "ts": datetime.now(UTC).isoformat(),
                "generated_by": provenance,
            }
        ]

    page_url = "https://directory.example.test/records"
    pdf_url = "https://directory.example.test/records.pdf"

    def capture(url, **_kwargs):
        assert url == page_url
        return {
            "url": url,
            "html": html,
            "html_key": html_key,
            "screenshot_key": screenshot,
            "trace": trace("step:page", url, html_key),
        }

    def fetch(url, **_kwargs):
        assert url == pdf_url
        return {
            "url": url,
            "bronze_key": pdf_key,
            "status": 200,
            "content_type": "application/pdf",
            "trace": trace("step:file", url, pdf_key),
        }

    result = execute_objective(
        case_dir=tmp_path,
        objective={
            "source_id": "source-test",
            "id": "objective-test",
            "source_url": page_url,
            "source_type": "directory",
        },
        ontology={
            "classes": [{"id": "library", "identifier_property": "id", "title_property": "title"}],
            "properties": [
                {"id": "id", "domain": "library", "datatype": "string"},
                {"id": "title", "domain": "library", "datatype": "string"},
            ],
        },
        tdd={
            "allowed_domains": ["directory.example.test"],
            "target_fields": ["id", "title"],
            "target_volume": 10,
        },
        lake=lake,
        run_id="mock-profile",
        decision=decision,
        store=MemorySilverStore(),
        provenance=provenance,
        capture=capture,
        fetch=fetch,
        parse_executor=ProfiledExecutor(),
    )

    assert not result.failed
    assert {item.value for item in result.observations if item.property_id == "id"} == {
        "A-1",
        "B-2",
    }
    assert {item.evidence["selector"].split(":")[1] for item in result.observations} == {
        "page=1",
        "page=2",
    }
    assert all(item.evidence["bronze_key"] == pdf_key for item in result.observations)
