"""R54: the parse pod's document profiler detects format, tables, headers and columns."""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from profiler import extract as extract_module
from profiler import profile_bytes
from profiler import profiler as profiler_module
from profiler.extract import ProfileFailure, detect_format
from profiler.patterns import match_patterns
from profiler.profiler import detect_header_row, entity_rows, profile_table

from tests.profiler_helpers import make_pdf, make_xlsx, make_zip

_POD = Path(__file__).resolve().parents[1] / "sandbox/parse-pod"


def _load_pod_runner():
    spec = importlib.util.spec_from_file_location("r54_pod_runner", _POD / "runner.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    if str(_POD) not in sys.path:
        sys.path.insert(0, str(_POD))
    spec.loader.exec_module(module)
    return module


def _patch_runner_proof(monkeypatch, runner) -> None:
    monkeypatch.setattr(
        runner,
        "_proof",
        lambda: {
            "pod": {"hostname": "synthetic-parse-pod"},
            "isolation": {"probes": [], "work_write_allowed": True},
            "secrets": {
                "ok": True,
                "env_keys_found": 0,
                "files_with_keys": 0,
                "metadata_ip": "BLOCKED",
                "mesh": "BLOCKED",
            },
        },
    )


def _run_pod(runner, tmp_path: Path, payload: bytes, *, kind: str = "auto") -> dict:
    envelope = {
        "kind": kind,
        "max_rows": 100,
        "base_url": "",
        "payload": base64.b64encode(payload).decode("ascii"),
    }
    source = tmp_path / "input.json"
    output = tmp_path / "output.json"
    source.write_text(json.dumps(envelope), encoding="utf-8")
    runner.run(source, output)
    return json.loads(output.read_text(encoding="utf-8"))


def test_multi_page_pdf_table_merges_and_profiles_rfc() -> None:
    pdf = make_pdf(
        [
            [
                "RFC              NOMBRE                  IMPORTE",
                "XAXX010101000    ACME SA DE CV           1000.50",
                "GODE561231GR8    GOMEZ SC                 200.00",
            ],
            [
                "RFC              NOMBRE                  IMPORTE",
                "AAAA010101AAA    OTRA SA DE CV            300.00",
            ],
        ]
    )

    profile = profile_bytes(pdf, jurisdictions=("MX",))

    assert profile["format"] == "pdf"
    assert profile["table_count"] == 1
    table = profile["tables"][0]
    assert table["headers"] == ["RFC", "NOMBRE", "IMPORTE"]
    assert table["row_count"] == 3
    columns = {column["header"]: column for column in table["columns"]}
    assert columns["RFC"]["dominant_pattern"] == "tax_id_rfc"
    assert columns["IMPORTE"]["dominant_pattern"] in {"decimal", "amount"}
    assert table["granularity"] == "entity"
    assert profile["structure"]["page_span"][table["sheet"]] == [1, 2]


def test_pdf_profile_keeps_pages_after_page_40_and_logical_row_receipts() -> None:
    pages = [
        ["Record ID          Name", f"SYN-{number:02d}             Example {number:02d}"]
        for number in range(1, 42)
    ]

    profile = profile_bytes(make_pdf(pages))
    entity = entity_rows(profile)

    assert profile["structure"]["pages"] == 41
    assert profile["table_count"] == 1
    assert profile["row_receipts"] == 41
    assert entity[-1]["Record ID"] == "SYN-41"
    assert entity[-1]["receipt"]["page"] == 41
    assert entity[-1]["receipt"]["row_number"] == 42


def test_pdf_profile_accepts_input_over_8_mib_in_pod_runner(tmp_path: Path, monkeypatch) -> None:
    runner = _load_pod_runner()
    _patch_runner_proof(monkeypatch, runner)
    pdf = make_pdf([["Record ID          Name", "SYN-01             Example One"]])
    payload = pdf + b"% synthetic padding\n" + b" " * (9 * 1024 * 1024)

    document = _run_pod(runner, tmp_path, payload, kind="pdf")

    assert document["ok"] is True
    assert document["kind"] == "pdf"
    assert document["profile"]["row_receipts"] == 1
    receipt = document["profile"]["tables"][0]["rows"][0]
    assert receipt["page"] == 1
    assert receipt["row_number"] == 2
    assert receipt["values"] == {"Record ID": "SYN-01", "Name": "Example One"}


def test_non_pdf_profile_keeps_the_8_mib_limit() -> None:
    payload = b"Name,Value\nExample,1\n" + b" " * (9 * 1024 * 1024)

    with pytest.raises(ProfileFailure, match="input_too_large"):
        profile_bytes(payload)


def test_pdf_page_limit_fails_instead_of_returning_a_partial_profile(monkeypatch) -> None:
    monkeypatch.setattr(extract_module, "MAX_PDF_PAGES", 2, raising=False)
    pdf = make_pdf([["Record ID", f"SYN-{number}"] for number in range(1, 4)])

    with pytest.raises(ProfileFailure, match="max_pdf_pages_exceeded"):
        profile_bytes(pdf)


def test_table_limit_fails_instead_of_returning_a_partial_profile(monkeypatch) -> None:
    monkeypatch.setattr(extract_module, "MAX_TABLES", 1)
    html = b"<table><tr><th>Name</th></tr><tr><td>First</td></tr></table>" * 2

    with pytest.raises(ProfileFailure, match="max_tables_exceeded"):
        profile_bytes(html)


def test_profile_row_limit_fails_instead_of_slicing_receipts(monkeypatch) -> None:
    monkeypatch.setattr(profiler_module, "MAX_PROFILE_ROWS", 2)
    csv_data = b"Name,Value\nOne,1\nTwo,2\nThree,3\n"

    with pytest.raises(ProfileFailure, match="max_profile_rows_exceeded"):
        profile_bytes(csv_data)


def test_extract_row_limit_fails_instead_of_slicing_csv(monkeypatch) -> None:
    monkeypatch.setattr(extract_module, "MAX_ROWS", 2)

    with pytest.raises(ProfileFailure, match="max_rows_exceeded"):
        profile_bytes(b"Name,Value\nOne,1\nTwo,2\nThree,3\n")


def test_profile_document_row_limit_fails_instead_of_slicing_receipts(monkeypatch) -> None:
    monkeypatch.setattr(profiler_module, "MAX_PROFILE_ROWS", 3)
    monkeypatch.setattr(profiler_module, "MAX_PROFILE_TOTAL_ROWS", 5)
    html = b"""<table>
<tr><th>Name</th><th>Value</th></tr><tr><td>A</td><td>1</td></tr>
<tr><td>B</td><td>2</td></tr><tr><td>C</td><td>3</td></tr></table>
<table><tr><th>Name</th><th>Value</th></tr><tr><td>D</td><td>4</td></tr>
<tr><td>E</td><td>5</td></tr><tr><td>F</td><td>6</td></tr></table>"""

    with pytest.raises(ProfileFailure, match="max_profile_total_rows_exceeded"):
        profile_bytes(html)


def test_pod_runner_surfaces_profile_limit_without_partial_rows_or_profile(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _load_pod_runner()
    _patch_runner_proof(monkeypatch, runner)
    monkeypatch.setattr(profiler_module, "MAX_PROFILE_ROWS", 1)
    payload = b"Name,Value\nOne,1\nTwo,2\n"

    document = _run_pod(runner, tmp_path, payload, kind="csv")

    assert document["ok"] is False
    assert document["error"]["code"] == "max_profile_rows_exceeded"
    assert document["rows"] == []
    assert document["profile"] == {}


def test_pod_output_limit_fails_with_no_partial_rows_or_profile(
    tmp_path: Path, monkeypatch
) -> None:
    runner = _load_pod_runner()
    _patch_runner_proof(monkeypatch, runner)
    monkeypatch.setattr(runner, "MAX_OUTPUT_BYTES", 256)
    payload = b"Name,Value\nOne,1\nTwo,2\n"

    document = _run_pod(runner, tmp_path, payload, kind="csv")

    assert document["ok"] is False
    assert document["error"]["code"] == "output_too_large"
    assert document["rows"] == []
    assert document["profile"] == {}
    assert document["proof"]["secrets"]["ok"] is True


def test_xlsx_title_row_two_sheets_and_aggregate_verdict() -> None:
    xlsx = make_xlsx(
        [
            (
                "Proveedores",
                [
                    ["Directorio de proveedores 2026"],
                    ["RFC", "Razon Social", "Monto"],
                    *[[f"XAXX01010{i}000", f"Proveedor {i} SA DE CV", 1000 + i] for i in range(6)],
                ],
            ),
            ("Resumen", [["Concepto", "Total"], ["Proveedores", 6], ["Monto", 6015]]),
        ]
    )

    profile = profile_bytes(xlsx, jurisdictions=("MX",))

    assert profile["format"] == "xlsx"
    assert profile["structure"]["sheets"] == ["Proveedores", "Resumen"]
    tables = {table["sheet"]: table for table in profile["tables"]}
    suppliers = tables["Proveedores"]
    assert suppliers["header_row"] == 1
    assert suppliers["headers"] == ["RFC", "Razon Social", "Monto"]
    assert suppliers["row_count"] == 6
    assert suppliers["granularity"] == "entity"
    assert any("title rows above header" in note for note in suppliers["notes"])
    assert tables["Resumen"]["granularity"] == "aggregate"
    assert tables["Resumen"]["headers"] == ["Concepto", "Total"]


def test_zip_of_csvs_walks_members() -> None:
    archive = make_zip(
        {
            "proveedores.csv": b"RFC,Nombre\nXAXX010101000,ACME SA DE CV\n",
            "cp.csv": b"CP,Ciudad\n06700,CDMX\n",
        }
    )

    profile = profile_bytes(archive, jurisdictions=("MX",))

    assert profile["format"] == "zip"
    assert profile["structure"]["members"] == ["proveedores.csv", "cp.csv"]
    assert {table["sheet"] for table in profile["tables"]} == {"proveedores.csv", "cp.csv"}
    proveedores = next(table for table in profile["tables"] if table["sheet"] == "proveedores.csv")
    assert proveedores["headers"] == ["RFC", "Nombre"]
    columns = {column["header"]: column for column in proveedores["columns"]}
    assert columns["RFC"]["dominant_pattern"] == "tax_id_rfc"


def test_aggregate_count_sheet_is_not_an_entity_listing() -> None:
    xlsx = make_xlsx([("Totales", [["Entidad", "Registros"], ["A", 10], ["B", 20], ["C", 5]])])

    profile = profile_bytes(xlsx)

    table = profile["tables"][0]
    assert table["granularity"] == "aggregate"


def test_header_detection_skips_title_rows_and_tolerates_merged_headers() -> None:
    rows = [
        ["Reporte anual"],
        ["RFC", None, "Monto"],
        ["XAXX010101000", "ACME SA DE CV", 10],
    ]
    assert detect_header_row(rows) == 1
    profile = profile_table(rows, jurisdictions=("MX",))
    assert profile is not None
    assert profile.headers[1].startswith("column_")


def test_patterns_are_pluggable_and_jurisdiction_keyed() -> None:
    assert match_patterns("XAXX010101000", jurisdictions=("MX",)) == ["tax_id_rfc"]
    assert "tax_id_rfc" not in match_patterns("XAXX010101000")
    assert "tax_id_us_ein" in match_patterns("12-3456789", jurisdictions=("US",))
    assert "postal_mx" in match_patterns("06700", jurisdictions=("MX",))
    assert "legal_suffix" in match_patterns("ACME SA DE CV")


def test_unknown_format_raises_profile_failure() -> None:
    assert detect_format(b"\x00\x01\x02 not a document") == "unknown"
    with pytest.raises(ProfileFailure):
        profile_bytes(b"\x00\x01\x02 not a document")


def test_profile_row_receipts_carry_sheet_row_and_headers() -> None:
    xlsx = make_xlsx(
        [
            (
                "Proveedores",
                [
                    ["Directorio 2026"],
                    ["RFC", "Razon Social"],
                    ["XAXX010101000", "ACME SA DE CV"],
                    ["GODE561231GR8", "GOMEZ SC"],
                ],
            )
        ]
    )

    profile = profile_bytes(xlsx, jurisdictions=("MX",))
    entity = entity_rows(profile)

    assert len(entity) == 2
    first = entity[0]
    assert first["RFC"] == "XAXX010101000"
    assert first["Razon Social"] == "ACME SA DE CV"
    assert first["receipt"]["sheet"] == "Proveedores"
    assert first["receipt"]["row_number"] == 3
    assert first["receipt"]["headers"] == ["RFC", "Razon Social"]
    assert first["receipt"]["fingerprint"] == profile["tables"][0]["fingerprint"]


def test_entity_rows_exclude_aggregate_tables() -> None:
    xlsx = make_xlsx(
        [
            ("Detalle", [["RFC", "Monto"], ["XAXX010101000", 100]]),
            ("Resumen", [["Concepto", "Total"], ["Registros", 1], ["Monto", 100]]),
        ]
    )

    profile = profile_bytes(xlsx, jurisdictions=("MX",))
    entity = entity_rows(profile)

    assert {row["receipt"]["sheet"] for row in entity} == {"Detalle"}
    assert all("Concepto" not in row for row in entity)
    assert profile["tables"][1]["granularity"] == "aggregate"


def test_pdf_table_yields_refiner_rows_with_page_receipts() -> None:
    pdf = make_pdf(
        [
            [
                "RFC              NOMBRE                  IMPORTE",
                "XAXX010101000    ACME SA DE CV           1000.50",
            ],
            [
                "RFC              NOMBRE                  IMPORTE",
                "AAAA010101AAA    OTRA SA DE CV            300.00",
            ],
        ]
    )

    profile = profile_bytes(pdf, jurisdictions=("MX",))
    entity = entity_rows(profile)

    assert [row["RFC"] for row in entity] == ["XAXX010101000", "AAAA010101AAA"]
    assert entity[0]["receipt"]["page"] == 1
    assert entity[1]["receipt"]["page"] == 2
    assert entity[1]["receipt"]["row_number"] == 3
    assert "table spans pages [1, 2]" in profile["tables"][0]["notes"]
    assert profile["row_receipts"] == 2


def test_pod_runner_emits_the_profile_beside_rows(tmp_path: Path) -> None:
    runner = _load_pod_runner()
    xlsx = make_xlsx([("S1", [["Title"], ["RFC", "Nombre"], ["XAXX010101000", "ACME SA DE CV"]])])
    envelope = {
        "kind": "xlsx",
        "max_rows": 100,
        "base_url": "",
        "jurisdictions": ["MX"],
        "payload": base64.b64encode(xlsx).decode("ascii"),
    }
    source = tmp_path / "input.json"
    output = tmp_path / "output.json"
    source.write_text(json.dumps(envelope), encoding="utf-8")

    runner.run(source, output)

    document = json.loads(output.read_text())
    assert document["ok"] is True
    assert document["kind"] == "xlsx"
    assert document["rows"][0]["values"] == ["Title", None]
    assert document["profile"]["format"] == "xlsx"
    assert document["profile"]["table_count"] == 1
    assert document["profile"]["tables"][0]["headers"] == ["RFC", "Nombre"]


def test_pod_runner_profiles_auto_detected_zip_of_csv(tmp_path: Path) -> None:
    runner = _load_pod_runner()
    archive = make_zip(
        {"records.csv": b"RFC,Name\nXAXX010101000,Example One\nAAAA010101AAA,Example Two\n"}
    )
    envelope = {
        "kind": "auto",
        "max_rows": 100,
        "base_url": "",
        "jurisdictions": ["MX"],
        "payload": base64.b64encode(archive).decode("ascii"),
    }
    source = tmp_path / "input.json"
    output = tmp_path / "output.json"
    source.write_text(json.dumps(envelope), encoding="utf-8")

    runner.run(source, output)

    document = json.loads(output.read_text())
    assert document["ok"] is True
    assert document["kind"] == "zip"
    assert document["rows"] == []
    assert document["profile"]["format"] == "zip"
    table = document["profile"]["tables"][0]
    assert table["headers"] == ["RFC", "Name"]
    assert table["columns"][0]["dominant_pattern"] == "tax_id_rfc"
    assert document["profile"]["row_receipts"] == 2


def test_pod_runner_rejects_zip_without_supported_tables(tmp_path: Path) -> None:
    runner = _load_pod_runner()
    archive = make_zip({"image.bin": b"\x00\x01\x02"})
    envelope = {
        "kind": "auto",
        "max_rows": 100,
        "base_url": "",
        "payload": base64.b64encode(archive).decode("ascii"),
    }
    source = tmp_path / "input.json"
    output = tmp_path / "output.json"
    source.write_text(json.dumps(envelope), encoding="utf-8")

    runner.run(source, output)

    document = json.loads(output.read_text())
    assert document["ok"] is False
    assert document["error"]["code"] == "zip_no_supported_tables"
    assert document["profile"] == {}


def test_pod_runner_profile_failure_keeps_the_parse_contract(tmp_path: Path) -> None:
    runner = _load_pod_runner()
    envelope = {
        "kind": "auto",
        "max_rows": 100,
        "base_url": "",
        "payload": base64.b64encode(b"\x00\x01\x02 not a document").decode("ascii"),
    }
    source = tmp_path / "input.json"
    output = tmp_path / "output.json"
    source.write_text(json.dumps(envelope), encoding="utf-8")

    runner.run(source, output)

    document = json.loads(output.read_text())
    assert document["ok"] is False
    assert document["error"]["code"] == "unknown_document_format"
    assert document["profile"] == {}
