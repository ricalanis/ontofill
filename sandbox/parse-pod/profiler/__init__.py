"""Document profiler meta-tool: format + structure + tables + columns, no model."""

from __future__ import annotations

from typing import Any

from profiler.extract import ProfileFailure, detect_format, extract_tables
from profiler.patterns import register_patterns
from profiler.profiler import profile_document

__all__ = ["ProfileFailure", "detect_format", "profile_bytes", "register_patterns"]


def profile_bytes(
    data: bytes, *, jurisdictions: tuple[str, ...] = (), patterns: object = None
) -> dict[str, Any]:
    """Return a bounded, code-only profile of one document's bytes.

    The result carries the detected format, a structural summary, and one profile
    per table (headers, per-column stats, sample rows, fingerprint, granularity).
    A format the profiler cannot read raises ``ProfileFailure`` so the caller can
    fall back instead of failing the run.
    """
    if patterns is not None:
        register_patterns(patterns)
    fmt, tables, structure = extract_tables(data)
    page_spans = structure.get("page_span") if isinstance(structure, dict) else None
    document = profile_document(tables, jurisdictions=jurisdictions, page_spans=page_spans)
    return {
        "format": fmt,
        "structure": structure,
        "table_count": document["table_count"],
        "row_receipts": document["row_receipts"],
        "row_receipts_truncated": document["row_receipts_truncated"],
        "tables": document["tables"],
    }
