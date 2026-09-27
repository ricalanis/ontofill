"""Code-only document profiler: structure, tables, headers and column profiles.

Runs in the networkless parse pod *before any model*. It never asserts case
vocabulary; it reports what is measurable (fill rate, uniqueness, matched
patterns, granularity) plus bounded samples so a later model only has to map
profiled columns to ontology properties.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from profiler.patterns import match_patterns

MAX_SAMPLE_ROWS = 5
MAX_TABLES = 40
MAX_PROFILE_ROWS = 2000
MAX_PROFILE_TOTAL_ROWS = 20_000
_HEADER_MIN_FILL = 0.6
_EMPTY = re.compile(r"^\s*$")


@dataclass
class TableProfile:
    sheet: str | None
    page: int | None
    row_count: int
    header_row: int
    headers: list[str]
    columns: list[dict]
    sample_rows: list[list[Any]]
    fingerprint: str
    granularity: str
    rows: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "sheet": self.sheet,
            "page": self.page,
            "row_count": self.row_count,
            "header_row": self.header_row,
            "headers": self.headers,
            "columns": self.columns,
            "sample_rows": self.sample_rows,
            "fingerprint": self.fingerprint,
            "granularity": self.granularity,
            "rows": self.rows,
            "notes": self.notes,
        }


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _is_multi_page_continuation(header_cells: list[str], previous: list[str]) -> bool:
    """A repeated banner row on a new PDF page, not a real header change."""
    return bool(previous) and [cell.casefold() for cell in header_cells] == [
        cell.casefold() for cell in previous
    ]


def detect_header_row(rows: list[list[Any]], *, max_scan: int = 10) -> int:
    """Pick the most likely header row index, tolerating title and merged-header rows.

    A title row is narrow (often one merged cell) or mostly numeric; the first row below
    it that is wide, mostly filled and mostly text is the header. Falls back to the first
    non-empty row so single-column documents still profile.
    """
    limit = min(max_scan, len(rows))
    first_non_empty = 0
    seen_non_empty = False
    for index in range(limit):
        cells = [cell for cell in (_norm(value) for value in rows[index]) if cell]
        if not cells:
            continue
        if not seen_non_empty:
            first_non_empty, seen_non_empty = index, True
        width = len(cells)
        fill = width / max(1, len(rows[index]))
        texty = sum(not re.fullmatch(r"[\d.,%$\s-]+", cell) for cell in cells) / width
        if width >= 2 and fill >= _HEADER_MIN_FILL and texty >= 0.6:
            return index
    return first_non_empty


def _columns_from_rows(
    headers: list[str], data_rows: list[list[Any]], jurisdictions: tuple[str, ...]
) -> list[dict]:
    columns = []
    total = len(data_rows)
    for position, header in enumerate(headers):
        values = [
            _norm(row[position]) if position < len(row) else ""
            for row in data_rows
        ]
        non_empty = [value for value in values if value]
        counter = Counter(non_empty)
        pattern_hits: Counter[str] = Counter()
        for value in non_empty:
            for name in match_patterns(value, jurisdictions=jurisdictions):
                pattern_hits[name] += 1
        dominant = pattern_hits.most_common(1)
        columns.append(
            {
                "index": position,
                "header": header or f"column_{position + 1}",
                "fill_rate": round(len(non_empty) / total, 4) if total else 0.0,
                "distinct": len(counter),
                "unique": bool(non_empty) and len(counter) == len(non_empty),
                "max_repeat": max(counter.values(), default=0),
                "patterns": dict(pattern_hits),
                "dominant_pattern": dominant[0][0] if dominant else None,
                "dominant_share": round(dominant[0][1] / len(non_empty), 4) if dominant else 0.0,
                "sample_values": [value for value, _ in counter.most_common(MAX_SAMPLE_ROWS)],
            }
        )
    return columns


def _fingerprint(headers: list[str], columns: list[dict]) -> str:
    import hashlib

    payload = "|".join(
        f"{header}:{column['dominant_pattern']}:{column['fill_rate']}"
        for header, column in zip(headers, columns)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


_IDENTIFIER_PATTERNS = {
    "tax_id_rfc",
    "tax_id_curp",
    "tax_id_us_ein",
    "email",
    "url",
    "phone",
}
_NUMERIC_PATTERNS = {"integer", "amount", "amount_currency", "decimal", "percentage"}


def _granularity(data_rows: list[list[Any]], columns: list[dict]) -> str:
    """Verdict: an entity listing (a row per thing) vs an aggregate roll-up.

    An identifier-shaped column means a per-entity listing. Otherwise a short table whose
    first column is unique text labels and whose other columns are mostly numeric is a
    roll-up (label column + measure column), which the extraction path must not treat as rows.
    """
    if any(column["dominant_pattern"] in _IDENTIFIER_PATTERNS for column in columns):
        return "entity"
    if len(data_rows) <= 8 and not any(column["dominant_pattern"] for column in columns):
        return "entity"
    numeric = [column for column in columns if column["dominant_pattern"] in _NUMERIC_PATTERNS]
    numeric_share = len(numeric) / len(columns) if columns else 0.0
    first_is_label = bool(columns) and columns[0]["unique"] and columns[0]["fill_rate"] >= 0.9
    if (
        numeric
        and numeric_share >= 0.5
        and first_is_label
        and columns[0]["dominant_pattern"] is None
        and len(data_rows) <= 25
    ):
        return "aggregate"
    return "entity_candidate"


def profile_table(
    rows: list[list[Any]],
    *,
    sheet: str | None = None,
    page: int | None = None,
    jurisdictions: tuple[str, ...] = (),
) -> TableProfile | None:
    """Profile one table: header row, per-column stats and a bounded sample."""
    if not rows:
        return None
    header_row = detect_header_row(rows)
    headers = [_norm(value) for value in rows[header_row]]
    while headers and not headers[-1]:
        headers.pop()
    if not headers:
        return None
    data_rows = rows[header_row + 1 :]
    notes = []
    title_rows = [_norm(" ".join(_norm(cell) for cell in row if _norm(cell))) for row in rows[:header_row]]
    title_rows = [row for row in title_rows if row]
    if title_rows:
        notes.append(f"title rows above header: {title_rows[:2]}")
    # Drop repeated banner rows (multi-page PDF continuation).
    cleaned: list[list[Any]] = []
    previous = headers
    for row in data_rows:
        cells = [_norm(value) for value in row]
        non_empty = [cell for cell in cells if cell]
        if non_empty and _is_multi_page_continuation(non_empty, previous):
            notes.append("repeated header row on a new page was removed")
            continue
        cleaned.append(row)
    columns = _columns_from_rows(headers, cleaned, jurisdictions)
    if any(_EMPTY.fullmatch(_norm(value)) for value in rows[0][header_row + 1 : header_row + 2]):
        notes.append("the first data row has an empty leading cell; merged headers are likely")
    header_names = [header or f"column_{position + 1}" for position, header in enumerate(headers)]
    receipts = [
        {
            "row_number": header_row + 1 + index + 1,
            "values": {header_names[position]: _norm(value) for position, value in enumerate(row[: len(headers)])},
        }
        for index, row in enumerate(cleaned[:MAX_PROFILE_ROWS])
        if any(_norm(value) for value in row)
    ]
    return TableProfile(
        sheet=sheet,
        page=page,
        row_count=len(cleaned),
        header_row=header_row,
        headers=header_names,
        columns=columns,
        sample_rows=[
            [_norm(value) for value in row[: len(headers)]] for row in cleaned[:MAX_SAMPLE_ROWS]
        ],
        fingerprint=_fingerprint(headers, columns),
        granularity=_granularity(cleaned, columns),
        rows=receipts,
        notes=notes,
    )


def profile_document(
    tables: list[tuple[str | None, int | None, list[list[Any]]]],
    *,
    jurisdictions: tuple[str, ...] = (),
) -> dict:
    """Profile every table found in a document, capped and fingerprinted."""
    profiles = []
    total_rows = 0
    truncated = False
    for sheet, page, rows in tables[:MAX_TABLES]:
        profile = profile_table(rows, sheet=sheet, page=page, jurisdictions=jurisdictions)
        if profile is None:
            continue
        budget = max(0, MAX_PROFILE_TOTAL_ROWS - total_rows)
        if len(profile.rows) > budget:
            profile.rows = profile.rows[:budget]
            profile.notes.append("row receipts truncated to the profile budget")
            truncated = True
        total_rows += len(profile.rows)
        profiles.append(profile.as_dict())
    return {
        "table_count": len(profiles),
        "row_receipts": total_rows,
        "row_receipts_truncated": truncated,
        "tables": profiles,
    }


def entity_rows(profile: dict) -> list[dict]:
    """Flatten a profile's entity-granularity row receipts into refiner-shaped rows.

    Each returned row keeps its header-keyed values plus a receipt: the table's
    sheet/page and the original row number, so P5 can attach evidence without a model.
    """
    rows: list[dict] = []
    for table in profile.get("tables", []):
        if table.get("granularity") == "aggregate":
            continue
        for receipt in table.get("rows", []):
            rows.append(
                {
                    **receipt["values"],
                    "receipt": {
                        "sheet": table.get("sheet"),
                        "page": table.get("page"),
                        "row_number": receipt["row_number"],
                        "headers": table.get("headers"),
                        "fingerprint": table.get("fingerprint"),
                    },
                }
            )
    return rows
