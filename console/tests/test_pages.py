"""Spend and evidence from saved files, replay into a scratch lake, and the no-domain-words guard."""

import json
import re
from pathlib import Path

from ontofill_console import cli

PKG = Path(__file__).resolve().parents[1] / "ontofill_console"
DENYLIST = (
    "supplier",
    "proveedor",
    "rfc",
    "sat",
    "compranet",
    "procurement",
    "mexico",
    "méxico",
    "contrato",
    "licitación",
    "licitacion",
    "tax list",
    "sanction registry",
)


def test_no_domain_words_in_the_console():
    """The console is generic: every domain word must come from a case's ontology, never from code or templates."""
    hits = []
    for path in PKG.rglob("*"):
        if path.suffix not in (".py", ".html", ".js", ".css") or not path.is_file():
            continue
        text = path.read_text(errors="replace").lower()
        for word in DENYLIST:
            if re.search(rf"\b{re.escape(word)}\b", text):
                hits.append(f"{path.relative_to(PKG)}: {word}")
    assert not hits, hits


def test_spend_from_saved_history(client, tmp_path, monkeypatch):
    assert "No spend snapshot yet" in client.get("/spend").text
    hist = tmp_path / "history.jsonl"
    rows = [
        {"ts": "2026-09-26T18:00:00+00:00", "credit_total": 200.0, "credit_used": 1.0, "credit_remaining": 199.0},
        {
            "ts": "2026-09-26T20:00:00+00:00",
            "credit_total": 200.0,
            "credit_used": 2.0,
            "credit_remaining": 198.0,
            "by_category": {"inference": 0.5},
        },
    ]
    hist.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setenv("ONTOFILL_CONSOLE_SPEND_HISTORY", str(hist))
    html = client.get("/spend").text
    assert "$2.00" in html and "of $200" in html


def test_evidence_from_saved_files(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOFILL_CONSOLE_EVIDENCE_DIR", str(tmp_path))
    (tmp_path / "verify-remote.txt").write_text("  PASS  VM port 22 closed\nRESULT: PASS\n")
    html = client.get("/evidence").text
    assert "Requirement by requirement" in html and "PASS · VM port 22 closed" in html
    assert "ev-status--proven" in html and "/cases/libraries/runs/run-libraries-0001#proof-h" in html


def test_replay_writes_only_to_a_scratch_copy(cases_dir, tmp_path):
    lake = cases_dir / "libraries" / "lake"
    before = sorted(p.name for p in (lake / "runs" / "fixture-libraries").iterdir())
    scratch = tmp_path / "scratch"
    assert (
        cli.main(["replay", str(lake), "--scratch", str(scratch), "--duration", "0.5", "--new-run-id", "run-replayed"])
        == 0
    )
    assert (scratch / "runs" / "fixture-libraries" / "run-replayed" / "status.json").is_file()
    assert sorted(p.name for p in (lake / "runs" / "fixture-libraries").iterdir()) == before


def test_fixtures_cli_prints_the_registry(tmp_path, capsys):
    assert cli.main(["fixtures", str(tmp_path / "fx")]) == 0
    assert capsys.readouterr().out.startswith("ONTOFILL_CONSOLE_CASES=libraries=")
