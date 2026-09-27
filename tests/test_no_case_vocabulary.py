"""Guard the reusable engine against vocabulary from one case."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (ROOT / "src", *(ROOT / "packages").glob("*/src"), ROOT / "schemas")
BANNED = re.compile(
    r"supplier|proveedor|\brfc\b|tax_id|tax_list_status|sanction|procurement|"
    r"licitaci|contrato|gob\.mx|ocds|legal_name|founding_date|open.contracting",
    re.IGNORECASE,
)
TEXT_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".txt"}


def _case_vocabulary_hits(roots: tuple[Path, ...]) -> list[str]:
    hits = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            label = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name
            if match := BANNED.search(path.name):
                hits.append(f"{label}: filename: {match.group()}")
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if match := BANNED.search(line):
                    hits.append(f"{label}:{line_number}: {match.group()}")
    return hits


def test_engine_has_no_case_vocabulary() -> None:
    hits = _case_vocabulary_hits(SOURCE_ROOTS)
    assert not hits, "Case vocabulary leaked into engine source:\n" + "\n".join(hits)


def test_guard_rejects_domain_named_schema(tmp_path: Path) -> None:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "procurement.schema.json").write_text("{}", encoding="utf-8")
    assert _case_vocabulary_hits((schema_dir,)) == [
        "procurement.schema.json: filename: procurement"
    ]
