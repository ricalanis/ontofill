"""Format detection and table extraction feeding the document profiler.

Every function returns ``(tables, structure)`` where each table is
``(sheet_or_name, page_or_none, rows)``. All parsing is code-only and bounded.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from typing import Any

from bs4 import BeautifulSoup
from openpyxl import load_workbook
from pypdf import PdfReader

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10_000
MAX_TABLES = 40
MAX_ZIP_ENTRIES = 64
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class ProfileFailure(ValueError):
    """A format the profiler cannot read; the caller keeps its own fallbacks."""


def detect_format(data: bytes) -> str:
    """Return one of csv/xlsx/xlsm/xls/pdf/zip/json/xml/html/unknown."""
    if len(data) > MAX_INPUT_BYTES:
        raise ProfileFailure("input_too_large")
    prefix = data[:65536]
    if prefix.startswith(b"%PDF-"):
        return "pdf"
    if prefix.startswith(_OLE):
        return "xls"
    if prefix.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist()[:MAX_ZIP_ENTRIES])
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise ProfileFailure("bad_zip") from exc
        if "xl/workbook.xml" in names:
            return "xlsm" if "xl/vbaProject.bin" in names else "xlsx"
        return "zip"
    try:
        text = prefix.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ProfileFailure("undecodable") from exc
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    if stripped.startswith("<"):
        return "xml" if re.match(r"<\?xml", stripped) else "html"
    if "\n" in text:
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
            sample = list(csv.reader(io.StringIO("\n".join(text.splitlines()[:3])), dialect))
        except csv.Error:
            sample = []
        if len(sample) >= 2 and len(sample[0]) > 1 and len(sample[0]) == len(sample[1]):
            return "csv"
    return "unknown"


def _csv_table(data: bytes) -> tuple[list[tuple], dict]:
    text = data.decode("utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [list(row) for row in csv.reader(io.StringIO(text), dialect)][:MAX_ROWS]
    return [(None, None, rows)], {"delimiter": getattr(dialect, "delimiter", ",")}


def _xlsx_tables(data: bytes) -> tuple[list[tuple], dict]:
    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    tables = []
    sheets = []
    for name in workbook.sheetnames[:MAX_TABLES]:
        sheet = workbook[name]
        rows = []
        for row in sheet.iter_rows(values_only=True):
            rows.append(list(row))
            if len(rows) >= MAX_ROWS:
                break
        tables.append((name, None, rows))
        sheets.append(name)
    workbook.close()
    return tables, {"sheets": sheets}


def _pdf_tables(data: bytes) -> tuple[list[tuple], dict]:
    reader = PdfReader(io.BytesIO(data), strict=False)
    per_page: list[tuple[int, list[list[str]]]] = []
    pages_with_text = 0
    for number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if not text.strip():
            continue
        pages_with_text += 1
        per_page.append((number, _text_rows(text)))
        if number >= MAX_TABLES:
            break
    tables: list[tuple] = []
    header: list[str] | None = None
    merged_pages: list[int] = []
    merged_rows: list[list[str]] = []
    for number, rows in per_page:
        rows = [row for row in rows if any(cell.strip() for cell in row)]
        if not rows:
            continue
        first = [cell.casefold() for cell in rows[0] if cell.strip()]
        if header is not None and first == header:
            # A repeated banner row: the table continues onto this page.
            merged_pages.append(number)
            merged_rows.extend(rows[1:])
            continue
        if merged_rows or (header is not None):
            tables.append((f"pages_{merged_pages[0]}", merged_pages[0], merged_rows))
        header = first
        merged_pages = [number]
        merged_rows = rows
    if merged_rows:
        tables.append((f"pages_{merged_pages[0]}", merged_pages[0], merged_rows))
    return tables, {
        "pages": len(reader.pages),
        "pages_with_text": pages_with_text,
        "page_span": {name: merged_pages for name, _page, _rows in tables},
    }


_WHITESPACE_SPLIT = re.compile(r"\s{2,}|\t")


def _text_rows(text: str) -> list[list[str]]:
    """Split PDF text into whitespace-aligned columns; blank lines separate tables."""
    rows = []
    for line in text.splitlines():
        cells = [cell.strip() for cell in _WHITESPACE_SPLIT.split(line.strip()) if cell.strip()]
        rows.append(cells)
    return rows


def _zip_tables(data: bytes) -> tuple[list[tuple], dict]:
    tables = []
    members = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for index, name in enumerate(archive.namelist()):
            if index >= MAX_ZIP_ENTRIES or name.endswith("/"):
                continue
            members.append(name)
            try:
                inner = archive.read(name)
            except (OSError, RuntimeError, zipfile.BadZipFile):
                continue
            if len(inner) > MAX_INPUT_BYTES:
                continue
            try:
                fmt = detect_format(inner)
            except ProfileFailure:
                continue
            if fmt != "csv":
                continue
            rows = [list(row) for row in csv.reader(io.StringIO(inner.decode("utf-8-sig")))][
                :MAX_ROWS
            ]
            tables.append((name, None, rows))
    tables = [table for table in tables if table[2]]
    return tables, {"members": members[:MAX_ZIP_ENTRIES]}


def _json_tables(data: bytes) -> tuple[list[tuple], dict]:
    document = json.loads(data.decode("utf-8-sig"))
    records = document if isinstance(document, list) else _records_from_json(document)
    records = [row for row in records if isinstance(row, dict)][:MAX_ROWS]
    if not records:
        return [], {"json_records": 0}
    headers = tuple(dict.fromkeys(key for row in records for key in row))
    rows = [list(headers)] + [[row.get(header) for header in headers] for row in records]
    return [(None, None, rows)], {"json_records": len(records)}


def _records_from_json(document: Any, depth: int = 0) -> list[dict]:
    if depth > 8:
        return []
    if isinstance(document, list):
        return [item for item in document if isinstance(item, dict)]
    if isinstance(document, dict):
        for value in document.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
        for value in document.values():
            found = _records_from_json(value, depth + 1)
            if found:
                return found
    return []


def _html_tables(data: bytes) -> tuple[list[tuple], dict]:
    soup = BeautifulSoup(data.decode("utf-8-sig", errors="replace"), "html.parser")
    tables = []
    for index, table in enumerate(soup.select("table")[:MAX_TABLES], start=1):
        rows = []
        for tr in table.select("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.select("td, th")]
            if cells:
                rows.append(cells)
        if rows:
            tables.append((f"table_{index}", None, rows))
    return tables, {"tables": len(tables)}


def _xml_tables(data: bytes) -> tuple[list[tuple], dict]:
    soup = BeautifulSoup(data.decode("utf-8-sig", errors="replace"), "xml")
    records = []
    for element in soup.find_all(True):
        if element.find(True) is not None:
            continue
        parent = element.parent
        if parent is None:
            continue
        values = {child.name: child.get_text(strip=True) for child in parent.find_all(True, recursive=False)}
        if values:
            records.append(values)
        if len(records) >= MAX_ROWS:
            break
    if not records:
        return [], {"xml_records": 0}
    headers = tuple(dict.fromkeys(key for row in records for key in row))
    rows = [list(headers)] + [[row.get(header, "") for header in headers] for row in records]
    return [(None, None, rows)], {"xml_records": len(records)}


_EXTRACTORS = {
    "csv": _csv_table,
    "xlsx": _xlsx_tables,
    "xlsm": _xlsx_tables,
    "pdf": _pdf_tables,
    "zip": _zip_tables,
    "json": _json_tables,
    "xml": _xml_tables,
    "html": _html_tables,
}


def extract_tables(data: bytes) -> tuple[str, list[tuple], dict]:
    """Detect the format then return its tables and a structural summary."""
    fmt = detect_format(data)
    extractor = _EXTRACTORS.get(fmt)
    if extractor is None:
        raise ProfileFailure(f"unsupported_format:{fmt}")
    tables, structure = extractor(data)
    return fmt, tables, structure
