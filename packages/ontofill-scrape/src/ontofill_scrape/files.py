"""Sandbox-client backed, read-only file fetch and offline parsing."""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from openpyxl import load_workbook
from pypdf import PdfReader

from .models import FailureKind, FetchedFile, ParsedFile, ParsedRow, ToolFailure


class FetchResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    content: bytes


class ReadOnlyFetchClient(Protocol):
    """Implemented by a sandbox browser/proxy; this package makes no network call."""

    def get(self, url: str) -> FetchResponse: ...


def _allowed(url: str, allowed_domains: set[str]) -> None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        raise ToolFailure(FailureKind.BLOCKED, "only public HTTP(S) reads are allowed")
    if parsed.username or parsed.password:
        raise ToolFailure(FailureKind.BLOCKED, "URL credentials are not allowed")
    if not any(host == domain or host.endswith("." + domain) for domain in allowed_domains):
        raise ToolFailure(FailureKind.BLOCKED, f"domain {host} is outside the TDD allowlist")


def file_fetch(
    url: str,
    allowed_domains: set[str] | tuple[str, ...],
    client: ReadOnlyFetchClient,
    *,
    max_bytes: int = 10_000_000,
    max_redirects: int = 3,
) -> FetchedFile:
    domains = {domain.lower().rstrip(".") for domain in allowed_domains}
    if not domains:
        raise ToolFailure(FailureKind.BLOCKED, "TDD allowed_domains is empty")
    current = url
    for _ in range(max_redirects + 1):
        _allowed(current, domains)
        try:
            response = client.get(current)
        except Exception as exc:
            raise ToolFailure(FailureKind.NETWORK, f"fetch failed: {current}") from exc
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location") or response.headers.get("Location")
            if not location:
                raise ToolFailure(FailureKind.NETWORK, "redirect has no Location")
            current = urljoin(current, location)
            continue
        if response.status_code in {401, 403}:
            raise ToolFailure(FailureKind.BLOCKED, "login or access wall")
        if response.status_code == 429:
            raise ToolFailure(FailureKind.RATE_LIMITED, "source rate limited")
        if response.status_code >= 400:
            raise ToolFailure(FailureKind.NETWORK, f"source returned HTTP {response.status_code}")
        content = response.content
        if len(content) > max_bytes:
            raise ToolFailure(FailureKind.BLOCKED, "file exceeds TDD byte limit")
        content_type = response.headers.get("content-type", "application/octet-stream")
        if b"captcha" in content[:4096].lower() or b"sign in to continue" in content[:4096].lower():
            raise ToolFailure(FailureKind.BLOCKED, "login or captcha wall")
        return FetchedFile(current, content, content_type)
    raise ToolFailure(FailureKind.BLOCKED, "redirect limit exceeded")


def file_parse(
    data: FetchedFile | bytes,
    *,
    format: str | None = None,
    filename: str | None = None,
    max_rows: int = 10_000,
    max_pages: int = 100,
) -> ParsedFile:
    content = data.content if isinstance(data, FetchedFile) else data
    name = filename or (
        PurePosixPath(urlsplit(data.url).path).name if isinstance(data, FetchedFile) else ""
    )
    kind = (format or PurePosixPath(name).suffix.removeprefix(".")).lower()
    if kind == "csv":
        text = content.decode("utf-8-sig")
        try:
            dialect = csv.Sniffer().sniff(text[:4096])
        except csv.Error:
            dialect = csv.excel
        rows = tuple(
            ParsedRow(None, index, tuple(row))
            for index, row in enumerate(csv.reader(io.StringIO(text), dialect), start=1)
            if index <= max_rows and any(value != "" for value in row)
        )
        return ParsedFile("csv", rows)
    if kind in {"xlsx", "xlsm"}:
        workbook = load_workbook(
            io.BytesIO(content), read_only=True, data_only=True, keep_links=False
        )
        rows: list[ParsedRow] = []
        try:
            for sheet in workbook.worksheets:
                for index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                    if index > max_rows:
                        break
                    if any(value is not None and value != "" for value in row):
                        rows.append(ParsedRow(sheet.title, index, tuple(row)))
        finally:
            workbook.close()
        return ParsedFile("xlsx", tuple(rows))
    if kind == "pdf":
        reader = PdfReader(io.BytesIO(content))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:max_pages])
        return ParsedFile("pdf", text=text)
    raise ToolFailure(
        FailureKind.VALIDATION_FAILED, f"unsupported file format: {kind or 'unknown'}"
    )
