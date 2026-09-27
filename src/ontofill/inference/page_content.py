"""Mark untrusted captured page text for gateway screening."""

from __future__ import annotations

import re

_DELIMITER = re.compile(r"<\s*/?\s*page_content\b", re.IGNORECASE)


def screened_page_content(text: str) -> str:
    """Keep page-provided delimiters inside one screened span."""
    safe = _DELIMITER.sub(lambda match: "&lt;" + match.group()[1:], text)
    return f"<page_content>{safe}</page_content>"
