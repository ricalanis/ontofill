"""Synthetic contract checks; no network or real source content."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from ontofill_scrape import (
    Evidence,
    FailureKind,
    Observation,
    ReplayResult,
    SandboxWorkspace,
    SearchResult,
    ToolFailure,
    code_promote,
    code_test,
    code_write,
    emit_observation,
    entity_lookup,
    extract_llm,
    extract_selector,
    file_fetch,
    file_parse,
    ontology_gaps,
    page_forms,
    page_links,
    page_pagination,
    page_query,
    page_snapshot,
    source_discover,
)
from openpyxl import Workbook
from pypdf import PdfWriter

HTML = """<html><head><title>Example listings</title></head><body>
<form method="GET" action="/search"><input name="q"><button>Search</button></form>
<table><tr><th>Name</th></tr><tr><td class="name">Proveedor Ejemplo 01</td></tr></table>
<a href="/results?page=2" rel="next">Siguiente →</a>
</body></html>"""


def test_captured_page_tools_are_read_only() -> None:
    snapshot = page_snapshot(HTML, "https://example.test/results/42?page=1")
    assert snapshot.title == "Example listings"
    assert snapshot.url_template.endswith("/results/{id}")
    assert snapshot.skeleton_hash
    assert any(element.role == "link" for element in snapshot.elements)
    assert page_query(HTML, "td.name")[0].text == "Proveedor Ejemplo 01"
    assert page_query(HTML, "//td[@class='name']")[0].text == "Proveedor Ejemplo 01"
    assert page_forms(HTML, snapshot.url)[0].search_or_filter
    assert page_links(HTML, snapshot.url)[0].url == "https://example.test/results?page=2"
    assert page_pagination(HTML, snapshot.url)[0].rel == "next"
    assert extract_selector(HTML, "td.name") == ("Proveedor Ejemplo 01",)


def test_login_and_captcha_stop() -> None:
    for html in (
        "<form><input type='password'></form>",
        "<p>Please sign in to continue</p>",
        "<div id='captcha-box'>Verify</div>",
    ):
        with pytest.raises(ToolFailure) as exc:
            page_snapshot(html, "https://example.test/")
        assert exc.value.kind == FailureKind.BLOCKED


class FakeResponse:
    def __init__(
        self, status_code: int, content: bytes = b"", headers: dict[str, str] | None = None
    ):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


class FakeFetchClient:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str) -> FakeResponse:
        self.urls.append(url)
        if url.endswith("/redirect"):
            return FakeResponse(302, headers={"location": "/data.csv"})
        return FakeResponse(
            200, b"name,tax_id\nProveedor Ejemplo 01,EXM010101AAA\n", {"content-type": "text/csv"}
        )


def test_file_fetch_allowlist_and_csv_rows() -> None:
    client = FakeFetchClient()
    fetched = file_fetch("https://example.test/redirect", {"example.test"}, client)
    assert client.urls == ["https://example.test/redirect", "https://example.test/data.csv"]
    rows = file_parse(fetched).rows
    assert rows[0].row_number == 1 and rows[0].values == ("name", "tax_id")
    assert rows[1].values[0] == "Proveedor Ejemplo 01"
    with pytest.raises(ToolFailure) as exc:
        file_fetch("https://other.test/data.csv", {"example.test"}, client)
    assert exc.value.kind == FailureKind.BLOCKED
    assert len(client.urls) == 2


def test_xlsx_preserves_sheet_and_original_row_positions() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Synthetic records"
    sheet.append(["Report title"])
    sheet.append([])
    sheet.append(["Supplier", "Tax ID"])
    sheet.append(["Proveedor Ejemplo 01", "EXM010101AAA"])
    data = io.BytesIO()
    workbook.save(data)
    rows = file_parse(data.getvalue(), format="xlsx").rows
    assert [(row.sheet, row.row_number) for row in rows] == [
        ("Synthetic records", 1),
        ("Synthetic records", 3),
        ("Synthetic records", 4),
    ]
    assert rows[-1].values == ("Proveedor Ejemplo 01", "EXM010101AAA")


def test_pdf_parse_offline() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    data = io.BytesIO()
    writer.write(data)
    assert file_parse(data.getvalue(), format="pdf").text == ""


class FakeSearchClient:
    def __init__(self) -> None:
        self.query = ""

    def search(self, query: str) -> list[SearchResult]:
        self.query = query
        return [
            SearchResult("https://source.example.test/open-data", "Open data", "Public records")
        ]


def test_source_discovery_uses_brief_and_injected_search() -> None:
    client = FakeSearchClient()
    found = source_discover("Find public supplier contracts in the target region", client)
    assert "supplier contracts" in client.query
    assert found[0].url == "https://source.example.test/open-data"
    assert found[0].query == client.query


class FakeDecision:
    def decide(self, prompt: str, response_schema: dict) -> dict:
        assert "untrusted data" in prompt
        assert "legal_name" in response_schema["required"]
        return {"legal_name": "Proveedor Ejemplo 01"}


def test_extraction_lookup_gaps_and_observation_channel() -> None:
    assert extract_llm(HTML, ["legal_name"], FakeDecision()) == {
        "legal_name": "Proveedor Ejemplo 01"
    }
    entities = [{"id": "sup:1", "tax_id": "EXM010101AAA", "legal_name": "Proveedor Ejemplo 01"}]
    assert (
        entity_lookup(entities, identifier_property="tax_id", identifier="EXM-010101-AAA")["id"]
        == "sup:1"
    )
    assert ontology_gaps(["legal_name", "tax_id"], {"sup:1": {"legal_name": "x"}}) == {
        "sup:1": ("tax_id",)
    }
    assert ontology_gaps(["enabled", "visits"], {"item:1": {"enabled": False, "visits": 0}}) == {
        "item:1": ()
    }
    evidence = Evidence(
        "https://example.test/record",
        "sha256:" + "a" * 64,
        "td.name",
        "2026-09-26T12:00:00Z",
        "source:1",
    )
    writes: list[dict] = []
    observation = Observation("sup:1", "legal_name", "Proveedor Ejemplo 01", evidence, 0.9)
    result = emit_observation(
        observation, {"legal_name"}, validate=lambda row: True, write=writes.append
    )
    assert writes == [result]
    with pytest.raises(ToolFailure):
        emit_observation(observation, {"tax_id"}, validate=lambda row: True, write=writes.append)
    assert len(writes) == 1


class FakeSandboxRunner:
    def run(self, script: Path, replay_pages: tuple[Path, ...]) -> ReplayResult:
        assert script.read_text() == "print('example')\n"
        return ReplayResult(True, len(replay_pages), "synthetic replay passed")


def test_code_tools_require_sandbox_workspace_and_replay(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    replay = root / "replay"
    replay.mkdir(parents=True)
    page = replay / "synthetic.html"
    page.write_text(HTML)
    workspace = SandboxWorkspace(root, replay)
    script = code_write(workspace, "extract.py", "print('example')\n")
    result = code_test(workspace, script, [page], FakeSandboxRunner())
    macro = code_promote(workspace, script, result)
    assert macro.pages_tested == 1 and len(macro.script_sha256) == 64
    with pytest.raises(ToolFailure) as exc:
        code_write(workspace, "../escape.py", "print(1)")
    assert exc.value.kind == FailureKind.BLOCKED
    outside = tmp_path / "outside.html"
    outside.write_text(HTML)
    with pytest.raises(ToolFailure):
        code_test(workspace, script, [outside], FakeSandboxRunner())
