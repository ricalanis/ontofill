"""Validate literal table receipts returned by the networkless document profiler."""

from __future__ import annotations

from collections.abc import Mapping

from ontofill_scrape.models import ParsedFile, ParsedRow


def complete_profile_tables(profile: object) -> list[dict] | None:
    """Return complete tables, or refuse any partial/ambiguous document profile."""
    if not isinstance(profile, Mapping):
        return None
    if profile.get("complete") is False or profile.get("row_receipts_truncated") is not False:
        return None
    tables = profile.get("tables")
    if not isinstance(tables, list) or not tables:
        return None
    if profile.get("table_count") != len(tables):
        return None
    for table in tables:
        if not isinstance(table, Mapping):
            return None
        headers = table.get("headers")
        rows = table.get("rows")
        if (
            not isinstance(headers, list)
            or not headers
            or not all(isinstance(header, str) and header.strip() for header in headers)
            or len(set(headers)) != len(headers)
            or not isinstance(rows, list)
            or table.get("row_count") != len(rows)
        ):
            return None
        if any(
            not isinstance(row, Mapping)
            or type(row.get("row_number")) is not int
            or row["row_number"] < 1
            or type(row.get("page")) is not int
            or row["page"] < 1
            or not isinstance(row.get("values"), Mapping)
            or not set(headers).issubset(row["values"])
            for row in rows
        ):
            return None
    return [dict(table) for table in tables]


def profile_to_parsed_file(
    parsed_result: object,
) -> tuple[ParsedFile, dict[tuple[str, int], int]] | None:
    """Keep PDF table rows literal, with absolute page coordinates for evidence."""
    if getattr(parsed_result, "format", None) != "pdf":
        return None
    profile = getattr(parsed_result, "profile", None)
    tables = complete_profile_tables(profile)
    if tables is None:
        return None
    rows: list[ParsedRow] = []
    pages: dict[tuple[str, int], int] = {}
    for table in tables:
        if table.get("granularity") not in {"entity", "entity_candidate"}:
            continue
        sheet = str(table.get("sheet") or f"page_{table.get('page')}")
        headers = tuple(table["headers"])
        rows.append(ParsedRow(sheet, 0, headers))
        for receipt in table["rows"]:
            number = receipt["row_number"]
            key = (sheet, number)
            if key in pages:
                return None
            pages[key] = receipt["page"]
            rows.append(
                ParsedRow(
                    sheet,
                    number,
                    tuple(receipt["values"][header] for header in headers),
                )
            )
    if not pages:
        return None
    return ParsedFile("pdf", tuple(rows)), pages
