"""Untrusted bronze parser entry point; receives only opaque bytes over stdin staging."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import math
import os
import re
import resource
import socket
import sys
import time
import zipfile
from collections.abc import Mapping
from datetime import date, datetime
from datetime import time as datetime_time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from openpyxl import load_workbook
from pypdf import PdfReader

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_ROWS = 10_000
MAX_DOCUMENT_ITEMS = 100_000
MAX_JSON_DEPTH = 64
MAX_PAGE_TEXT_CHARS = 200_000
MAX_FORMS = 40
MAX_FORM_FIELDS = 200
MAX_FORM_TEXT_CHARS = 160
MAX_TABLES = 40
MAX_TABLE_HEADERS = 30
MAX_FORMAT_SNIFF_BYTES = 64 * 1024
_OLE_COMPOUND_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_SECRET_MARKERS = (
    "API_KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "CREDENTIAL",
    "VULTR_",
    "NETBIRD_",
    "AWS_",
    "JEV_",
)
_SAFE_XLS_EXCEPTION_TYPES = frozenset(
    {
        "AssertionError",
        "AttributeError",
        "CompDocError",
        "EOFError",
        "IndexError",
        "KeyError",
        "ModuleNotFoundError",
        "OSError",
        "OverflowError",
        "ParserError",
        "TypeError",
        "UnicodeDecodeError",
        "ValueError",
        "XLRDError",
        "error",
    }
)
_SENSITIVE_FIELD = re.compile(
    r"(?i)(?:password|secret|token|credential|authorization|bearer|csrf|session)"
)
_SEARCH_FORM_CUE = re.compile(r"(?i)\b(?:search|find|lookup|look up|query)\b")


class ParseFailure(ValueError):
    def __init__(self, code: str, *, message: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


def _json_no_constants(value: str) -> None:
    raise ParseFailure("invalid_json_constant")


def _bounded_document(document: Any) -> None:
    stack = [(document, 0)]
    count = 0
    while stack:
        value, depth = stack.pop()
        count += 1
        if count > MAX_DOCUMENT_ITEMS:
            raise ParseFailure("document_item_limit")
        if depth > MAX_JSON_DEPTH:
            raise ParseFailure("document_depth_limit")
        if isinstance(value, dict):
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)


def _flatten_json_records(value: Any, *, max_rows: int) -> list[dict[str, Any]]:
    """Flatten arrays of objects to bounded dotted-path records."""

    _bounded_document(value)

    def expand(node: Any, prefix: str = "") -> list[dict[str, Any]]:
        if isinstance(node, Mapping):
            records: list[dict[str, Any]] = [{}]
            for key, child in node.items():
                child_prefix = f"{prefix}.{key}" if prefix else str(key)
                branches = expand(child, child_prefix)
                merged: list[dict[str, Any]] = []
                for left in records:
                    for right in branches:
                        merged.append({**left, **right})
                        if len(merged) > max_rows:
                            raise ParseFailure("max_rows_exceeded")
                records = merged
            return records
        if isinstance(node, list):
            if node and all(isinstance(item, Mapping) for item in node):
                records: list[dict[str, Any]] = []
                for item in node:
                    records.extend(expand(item, prefix))
                    if len(records) > max_rows:
                        raise ParseFailure("max_rows_exceeded")
                return records
            return [{prefix: json.dumps(node, ensure_ascii=False, separators=(",", ":"))}]
        return [{prefix: node}] if prefix else [{}]

    records = expand(value)
    if len(records) > max_rows:
        raise ParseFailure("max_rows_exceeded")
    return records


def _row(sheet: str | None, row_number: int, values: Any) -> dict[str, Any]:
    return {
        "sheet": sheet,
        "row_number": row_number,
        "values": [_json_cell(value) for value in values],
    }


def _json_cell(value: Any) -> Any:
    if isinstance(value, (date, datetime, datetime_time)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ParseFailure("non_finite_cell")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _parse_csv(data: bytes, max_rows: int) -> list[dict[str, Any]]:
    text = data.decode("utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text[:4096])
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows: list[dict[str, Any]] = []
    for number, values in enumerate(reader, start=1):
        if number > max_rows:
            raise ParseFailure("max_rows_exceeded")
        if any(value != "" for value in values):
            rows.append(_row(None, number, values))
    return rows


def _parse_xlsx(data: bytes, max_rows: int) -> list[dict[str, Any]]:
    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    rows: list[dict[str, Any]] = []
    total_rows = 0
    try:
        for sheet in workbook.worksheets:
            for number, values in enumerate(sheet.iter_rows(values_only=True), start=1):
                total_rows += 1
                if total_rows > max_rows:
                    raise ParseFailure("max_rows_exceeded")
                if any(value is not None and value != "" for value in values):
                    rows.append(_row(sheet.title, number, values))
    finally:
        workbook.close()
    return rows


def _open_xls(data: bytes) -> tuple[Any, Any]:
    """Lazily open legacy BIFF bytes with the parse pod's xlrd dependency."""
    import xlrd

    return xlrd, xlrd.open_workbook(file_contents=data, on_demand=True)


def _xls_failure_message(stage: str, error: Exception) -> str:
    stage_label = {
        "open": "Legacy XLS workbook could not be opened by xlrd",
        "decode": "Legacy XLS worksheet could not be decoded by xlrd",
    }[stage]
    error_type = type(error).__name__
    if error_type not in _SAFE_XLS_EXCEPTION_TYPES:
        error_type = "ParserError"
    return f"{stage_label} ({error_type})."


def _parse_xls(data: bytes, max_rows: int) -> list[dict[str, Any]]:
    try:
        xlrd, workbook = _open_xls(data)
    except Exception as exc:  # noqa: BLE001 - malformed BIFF must fail closed in the pod
        raise ParseFailure("invalid_xls", message=_xls_failure_message("open", exc)) from None

    rows: list[dict[str, Any]] = []
    total_rows = 0
    total_cells = 0
    try:
        for sheet_index in range(workbook.nsheets):
            sheet = workbook.sheet_by_index(sheet_index)
            if sheet.nrows > max_rows - total_rows:
                raise ParseFailure("max_rows_exceeded")
            total_rows += sheet.nrows
            for row_index in range(sheet.nrows):
                if total_cells + sheet.ncols > MAX_DOCUMENT_ITEMS:
                    raise ParseFailure("document_item_limit")
                total_cells += sheet.ncols
                values: list[Any] = []
                for cell in sheet.row(row_index):
                    if cell.ctype in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK}:
                        value = None
                    elif cell.ctype == xlrd.XL_CELL_DATE:
                        value = xlrd.xldate_as_datetime(cell.value, workbook.datemode)
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        value = bool(cell.value)
                    elif cell.ctype == xlrd.XL_CELL_ERROR:
                        value = xlrd.error_text_from_code.get(int(cell.value), "#ERROR!")
                    else:
                        value = cell.value
                    values.append(value)
                if any(value is not None and value != "" for value in values):
                    rows.append(_row(sheet.name, row_index + 1, values))
    except ParseFailure:
        raise
    except Exception as exc:  # noqa: BLE001 - malformed BIFF must fail closed in the pod
        raise ParseFailure("invalid_xls", message=_xls_failure_message("decode", exc)) from None
    finally:
        workbook.release_resources()
    return rows


def _parse_html(
    data: bytes, max_rows: int, base_url: str
) -> tuple[list[dict[str, Any]], str, list[dict[str, str]], str, bool]:
    document = BeautifulSoup(data.decode("utf-8-sig"), "html.parser")
    rows: list[dict[str, Any]] = []
    links: list[dict[str, str]] = []
    for table_number, table in enumerate(document.select("table"), start=1):
        for number, tr in enumerate(table.select("tr"), start=1):
            values = tuple(cell.get_text(" ", strip=True) for cell in tr.select("th, td"))
            if values:
                if len(rows) >= max_rows:
                    raise ParseFailure("max_rows_exceeded")
                rows.append(_row(f"html-table-{table_number}", number, values))
    for tag in document.select("a[href], link[href]"):
        href = str(tag.get("href", ""))
        url = urljoin(base_url, href) if base_url else href
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError:
            continue
        if (
            len(url) > 8192
            or parsed.scheme not in {"http", "https"}
            or not hostname
            or parsed.username
            or parsed.password
        ):
            continue
        if len(links) >= max_rows:
            raise ParseFailure("max_links_exceeded")
        rel_value = tag.get("rel") or []
        rel = (
            " ".join(str(item) for item in rel_value)
            if isinstance(rel_value, list)
            else str(rel_value)
        )
        container = next(
            (
                parent
                for parent in tag.parents
                if getattr(parent, "name", None) in {"article", "li"}
                or any(
                    "result" in str(class_name).casefold() or str(class_name).casefold() == "b_algo"
                    for class_name in parent.get("class", [])
                )
            ),
            None,
        )
        container_classes = (
            [str(item).casefold() for item in container.get("class", [])]
            if container is not None
            else []
        )
        if container is not None and container.name == "article":
            result_kind = "article"
        elif container is not None and "b_algo" in container_classes:
            result_kind = "bing"
        elif container is not None and any("result" in item for item in container_classes):
            result_kind = "result"
        else:
            result_kind = "other"
        context = (
            " ".join(container.get_text(" ", strip=True).split())
            if container is not None
            else tag.get_text(" ", strip=True)
        )[:900]
        heading = container.find(["h2", "h3", "h4", "h5"]) if container is not None else None
        links.append(
            {
                "url": url,
                "text": tag.get_text(" ", strip=True),
                "rel": rel,
                "title": heading.get_text(" ", strip=True)
                if heading
                else tag.get_text(" ", strip=True),
                "context": context,
                "result_kind": result_kind,
            }
        )
    skeleton = " ".join(tag.name for tag in document.find_all(True))
    skeleton_hash = hashlib.sha256(skeleton.encode("utf-8")).hexdigest()
    for tag in document(("script", "style", "noscript", "template")):
        tag.decompose()
    body = document.body or document
    page_text = " ".join(body.get_text(" ", strip=True).split())
    if len(page_text) > MAX_PAGE_TEXT_CHARS:
        raise ParseFailure("page_text_limit")
    challenge_detected = bool(
        document.select('input[type="password"][name*="captcha" i], input[name*="captcha" i]')
        or "captcha" in str(document.title.string if document.title else "").casefold()
    )
    return rows, page_text, links, skeleton_hash, challenge_detected


def _parse_forms(data: bytes) -> list[dict[str, Any]]:
    """Return bounded, value-free form labels from HTML inside the parse pod."""
    document = BeautifulSoup(data.decode("utf-8-sig"), "html.parser")
    forms: list[dict[str, Any]] = []
    field_count = 0

    def clean(value: Any) -> str:
        return " ".join(str(value or "").split())[:MAX_FORM_TEXT_CHARS]

    for form in document.find_all("form")[:MAX_FORMS]:
        fields: list[dict[str, str]] = []
        for field in form.select("input, select, textarea"):
            if field_count >= MAX_FORM_FIELDS:
                break
            field_type = clean(
                field.get("type") or ("select" if field.name == "select" else "text")
            )
            field_type = field_type.casefold()[:32]
            name = clean(field.get("name"))
            if (
                field_type in {"hidden", "password", "file", "submit", "button", "image"}
                or field.get("aria-hidden") == "true"
                or _SENSITIVE_FIELD.search(name)
            ):
                continue
            field_id = field.get("id")
            labels = (
                document.find_all("label", attrs={"for": field_id})
                if isinstance(field_id, str) and field_id
                else []
            )
            parent_label = field.find_parent("label")
            label = clean(
                " ".join(item.get_text(" ", strip=True) for item in labels)
                or (parent_label.get_text(" ", strip=True) if parent_label else "")
                or field.get("aria-label")
                or field.get("title")
            )
            placeholder = clean(field.get("placeholder"))
            if not any((label, placeholder, name)):
                continue
            fields.append(
                {
                    "type": field_type,
                    "label": label,
                    "placeholder": placeholder,
                    "name": name,
                }
            )
            field_count += 1

        submit_labels = []
        for button in form.select("button, input[type=submit], input[type=button]")[:12]:
            label = clean(
                button.get("aria-label")
                or button.get("title")
                or button.get("value")
                or button.get_text(" ", strip=True)
            )
            if label and not _SENSITIVE_FIELD.search(label):
                submit_labels.append(label)

        form_label = clean(form.get("aria-label") or form.get("title"))
        role = clean(form.get("role")).casefold()
        if not fields and not submit_labels and not form_label:
            continue
        forms.append(
            {
                "label": form_label,
                "role": role,
                "search_like": bool(
                    role == "search"
                    or any(field["type"] == "search" for field in fields)
                    or _SEARCH_FORM_CUE.search(" ".join([form_label, *submit_labels]))
                ),
                "fields": fields,
                "submit_labels": submit_labels,
            }
        )
    return forms


def _parse_table_headers(data: bytes) -> list[list[str]]:
    """Return only explicit table header cells, never record rows."""
    document = BeautifulSoup(data.decode("utf-8-sig"), "html.parser")
    headers: list[list[str]] = []
    for table in document.select("table")[:MAX_TABLES]:
        for row in table.select("tr"):
            cells = [
                " ".join(cell.get_text(" ", strip=True).split())[:MAX_FORM_TEXT_CHARS]
                for cell in row.select("th")[:MAX_TABLE_HEADERS]
            ]
            cells = [cell for cell in cells if cell]
            if cells:
                headers.append(cells)
                break
    return headers


def _parse_json(data: bytes, max_rows: int) -> list[dict[str, Any]]:
    document = json.loads(data.decode("utf-8-sig"), parse_constant=_json_no_constants)
    records = _flatten_json_records(document, max_rows=max_rows)
    if not records:
        return []
    headers = tuple(dict.fromkeys(key for row in records for key in row))
    return [_row(None, 1, headers)] + [
        _row(None, index, tuple(record.get(header) for header in headers))
        for index, record in enumerate(records, start=2)
    ]


def _parse_pdf(data: bytes, max_rows: int) -> list[dict[str, Any]]:
    reader = PdfReader(io.BytesIO(data), strict=True)
    if len(reader.pages) > max_rows:
        raise ParseFailure("max_rows_exceeded")
    return [
        {"page_number": number, "text": page.extract_text() or ""}
        for number, page in enumerate(reader.pages, start=1)
    ]


def _detect_document_format(data: bytes) -> str:
    """Infer a bounded supported format from document bytes inside the parse pod."""
    if len(data) > MAX_INPUT_BYTES:
        raise ParseFailure("input_too_large")
    prefix = data[:MAX_FORMAT_SNIFF_BYTES]
    if prefix.startswith(b"%PDF-"):
        return "pdf"
    if prefix.startswith(_OLE_COMPOUND_SIGNATURE):
        try:
            _xlrd, workbook = _open_xls(data)
            try:
                if workbook.nsheets < 1:
                    raise ValueError("workbook has no sheets")
                workbook.sheet_by_index(0)
            finally:
                workbook.release_resources()
        except Exception:  # noqa: BLE001 - malformed OLE input must be rejected by the pod
            raise ParseFailure("unknown_document_format") from None
        return "xls"
    if prefix.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist()[:10_000])
        except (OSError, zipfile.BadZipFile, RuntimeError):
            raise ParseFailure("unknown_document_format") from None
        if "xl/workbook.xml" in names:
            return "xlsm" if "xl/vbaProject.bin" in names else "xlsx"
        raise ParseFailure("unknown_document_format")
    try:
        text = prefix.decode("utf-8-sig").strip()
    except UnicodeDecodeError:
        raise ParseFailure("unknown_document_format") from None
    if text.startswith(("{", "[")):
        return "json"
    if "\n" in text:
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
            rows = list(csv.reader(io.StringIO("\n".join(text.splitlines()[:3])), dialect))
        except csv.Error:
            rows = []
        if len(rows) >= 2 and len(rows[0]) > 1 and len(rows[0]) == len(rows[1]):
            return "csv"
    raise ParseFailure("unknown_document_format")


def _parse_document(data: bytes) -> dict[str, Any]:
    document = json.loads(data.decode("utf-8-sig"), parse_constant=_json_no_constants)
    if not isinstance(document, dict):
        raise ParseFailure("json_document_root_must_be_object")
    _bounded_document(document)
    return document


def _parse(
    data: bytes, kind: str, max_rows: int, base_url: str
) -> tuple[list[dict[str, Any]], str, str, list[dict[str, str]], str | None, bool]:
    if len(data) > MAX_INPUT_BYTES:
        raise ParseFailure("input_too_large")
    if kind == "csv":
        return _parse_csv(data, max_rows), "", "", [], None, False
    if kind == "xls":
        return _parse_xls(data, max_rows), "", "", [], None, False
    if kind in {"xlsx", "xlsm"}:
        return _parse_xlsx(data, max_rows), "", "", [], None, False
    if kind == "html":
        rows, page_text, links, skeleton_hash, challenge = _parse_html(data, max_rows, base_url)
        return rows, "", page_text, links, skeleton_hash, challenge
    if kind == "json":
        return _parse_json(data, max_rows), "", "", [], None, False
    if kind == "pdf":
        rows = _parse_pdf(data, max_rows)
        return rows, "\n".join(row["text"] for row in rows), "", [], None, False
    if kind == "json_document":
        return [{"document": _parse_document(data)}], "", "", [], None, False
    raise ParseFailure("unsupported_format")


def _probe_socket(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return False
    except OSError:
        return True


def _probe_write(path: Path) -> bool:
    try:
        with path.open("xb") as stream:
            stream.write(b"probe")
        path.unlink(missing_ok=True)
    except OSError:
        return True
    return False


def _files_with_secret_names() -> int:
    roots = (Path("/run/secrets"), Path("/root"), Path("/home"), Path("/work"))
    count = 0
    visited = 0
    for root in roots:
        if not root.exists():
            continue
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if not Path(directory, name).is_symlink()]
            for name in files:
                visited += 1
                if visited > 1000:
                    return count
                if any(marker.lower() in name.lower() for marker in _SECRET_MARKERS):
                    count += 1
    return count


def _proof() -> dict[str, Any]:
    env_keys = sum(any(marker in name.upper() for marker in _SECRET_MARKERS) for name in os.environ)
    metadata_blocked = _probe_socket("169.254.169.254", 80)
    mesh_blocked = _probe_socket("100.64.0.1", 22)
    external_blocked = _probe_socket("1.1.1.1", 53)
    work_write_allowed = not _probe_write(Path("/work/.write-probe"))
    etc_write_blocked = _probe_write(Path("/etc/.ontofill-write-probe"))
    uname = os.uname()
    flags: list[str] = []
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("flags"):
                flags = sorted(set(line.split(":", 1)[-1].split()))
                break
    except OSError:
        pass
    probes = [
        {
            "probe": "network_non_allowlisted",
            "blocked": external_blocked,
            "target": "1.1.1.1:53",
        },
        {
            "probe": "write_outside_pod",
            "blocked": _probe_write(Path("/host/.ontofill-write-probe")),
            "target": "/host/.ontofill-write-probe",
        },
        {
            "probe": "write_outside_writable_mount",
            "blocked": etc_write_blocked,
            "target": "/etc/.ontofill-write-probe",
        },
        {
            "probe": "no_host_mounts",
            "blocked": not Path("/host").exists(),
            "target": "/host",
        },
    ]
    secrets = {
        "env_keys_found": env_keys,
        "files_with_keys": _files_with_secret_names(),
        "metadata_ip": "BLOCKED" if metadata_blocked else "ALLOWED",
        "mesh": "BLOCKED" if mesh_blocked else "ALLOWED",
    }
    secrets["ok"] = (
        secrets["env_keys_found"] == 0
        and secrets["files_with_keys"] == 0
        and secrets["metadata_ip"] == "BLOCKED"
        and secrets["mesh"] == "BLOCKED"
    )
    return {
        "pod": {
            "hostname": socket.gethostname(),
            "uname": {"system": uname.sysname, "release": uname.release, "machine": uname.machine},
            "cpu_virtualization_flags": flags,
            "dev_kvm_present": Path("/dev/kvm").exists(),
        },
        "isolation": {"probes": probes, "work_write_allowed": work_write_allowed},
        "secrets": secrets,
        "network_probes": {
            "external": external_blocked,
            "metadata_ip": metadata_blocked,
            "mesh": mesh_blocked,
        },
    }


def _peak_memory_mb() -> float:
    # Linux reports ru_maxrss in KiB.
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3)


def _safe_profile(data: bytes, kind: str, envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Profile the document without letting an unreadable format fail the parse.

    The profiler is a meta-tool: on any failure it returns an empty profile so the
    existing per-kind parse result stays the contract.
    """
    jurisdictions = envelope.get("jurisdictions")
    allowed = ()
    if isinstance(jurisdictions, list):
        allowed = tuple(str(item) for item in jurisdictions if isinstance(item, str))
    try:
        from profiler import ProfileFailure, profile_bytes
    except ImportError:  # pragma: no cover - the pod always ships the profiler
        return {}
    try:
        result = profile_bytes(data, jurisdictions=allowed, patterns=envelope.get("patterns"))
    except (ProfileFailure, ValueError, OSError, KeyError, TypeError, IndexError, RecursionError):
        return {}
    if kind and kind not in {result.get("format"), "html", "json_document"}:
        result["kind_mismatch"] = kind
    return result


def run(input_path: Path, output_path: Path) -> None:
    started = time.monotonic()
    proof: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    text = ""
    page_text = ""
    links: list[dict[str, str]] = []
    forms: list[dict[str, Any]] = []
    table_headers: list[list[str]] = []
    profile: dict[str, Any] = {}
    skeleton_hash: str | None = None
    challenge_detected = False
    error: dict[str, str] | None = None
    kind = ""
    max_rows = 0
    try:
        envelope = json.loads(input_path.read_text(encoding="utf-8"))
        kind = str(envelope.get("kind", ""))
        max_rows = envelope.get("max_rows")
        if type(max_rows) is not int or not 1 <= max_rows <= MAX_ROWS:
            raise ParseFailure("invalid_row_limit")
        proof = _proof()
        base_url = envelope.get("base_url", "")
        if not isinstance(base_url, str):
            raise ParseFailure("invalid_base_url")
        bronze_path = envelope.get("bronze_path")
        if bronze_path is not None:
            if bronze_path != "/work/bronze":
                raise ParseFailure("invalid_bronze_path")
            expected_sha256 = envelope.get("expected_sha256")
            max_input_bytes = envelope.get("max_input_bytes")
            if (
                not isinstance(expected_sha256, str)
                or len(expected_sha256) != 64
                or any(char not in "0123456789abcdef" for char in expected_sha256)
                or type(max_input_bytes) is not int
                or not 1 <= max_input_bytes <= MAX_INPUT_BYTES
            ):
                raise ParseFailure("invalid_bronze_digest")
            path = Path(bronze_path)
            try:
                with path.open("rb") as stream:
                    payload = stream.read(max_input_bytes + 1)
            except OSError as exc:
                raise ParseFailure("bronze_read_failed") from exc
            if len(payload) > max_input_bytes:
                raise ParseFailure("input_too_large")
            if hashlib.sha256(payload).hexdigest() != expected_sha256:
                raise ParseFailure("bronze_digest_mismatch")
        else:
            payload = base64.b64decode(envelope.get("payload", ""), validate=True)
        if kind == "auto":
            kind = _detect_document_format(payload)
        rows, text, page_text, links, skeleton_hash, challenge_detected = _parse(
            payload, kind, max_rows, base_url
        )
        if kind == "html":
            forms = _parse_forms(payload)
            table_headers = _parse_table_headers(payload)
        profile = _safe_profile(payload, kind, envelope)
    except ParseFailure as exc:
        error = {"code": exc.code}
        if exc.message is not None:
            error["message"] = exc.message
    except Exception as exc:  # noqa: BLE001 - all input parsing is confined to this pod
        error = {"code": "parse_error", "exception": type(exc).__name__}
    finally:
        if not proof:
            try:
                proof = _proof()
            except Exception as exc:  # noqa: BLE001 - emit an honest failed proof
                error = {"code": "proof_error", "exception": type(exc).__name__}

    document: dict[str, Any] = {
        "ok": error is None,
        "kind": kind,
        "rows": rows if error is None else [],
        "text": text if error is None else "",
        "page_text": page_text if error is None else "",
        "links": links if error is None else [],
        "forms": forms if error is None else [],
        "table_headers": table_headers if error is None else [],
        "profile": profile if error is None else {},
        "dom_skeleton_hash": skeleton_hash if error is None else None,
        "challenge_detected": challenge_detected if error is None else False,
        "truncated": False,
        "error": error,
        "proof": proof,
        "usage": {
            "peak_memory_mb": _peak_memory_mb(),
            "wall_s": round(time.monotonic() - started, 3),
            "steps": 1,
        },
    }
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
        document.update(
            {
                "ok": False,
                "rows": [],
                "text": "",
                "page_text": "",
                "links": [],
                "forms": [],
                "table_headers": [],
                "profile": {},
                "dom_skeleton_hash": None,
                "error": {"code": "output_too_large"},
            }
        )
        encoded = json.dumps(document, separators=(",", ":"), allow_nan=False)
    output_path.write_text(encoded, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: runner.py INPUT OUTPUT")
    run(Path(sys.argv[1]), Path(sys.argv[2]))
