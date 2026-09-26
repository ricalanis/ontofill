"""Guard the reusable engine against vocabulary from one case."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (ROOT / "src", *(ROOT / "packages").glob("*/src"))
BANNED = re.compile(
    r"supplier|proveedor|\brfc\b|tax_id|tax_list_status|sanction|procurement|"
    r"licitaci|contrato|gob\.mx|ocds|legal_name|founding_date|open.contracting",
    re.IGNORECASE,
)
TEXT_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".txt"}


def test_engine_has_no_case_vocabulary() -> None:
    hits = []
    for root in SOURCE_ROOTS:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if match := BANNED.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{line_number}: {match.group()}")
    assert not hits, "Case vocabulary leaked into engine source:\n" + "\n".join(hits)
