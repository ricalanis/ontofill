"""The case's question (brief.md) is on the case list, the case overview and every approval review page."""

from ontofill_console.approvals import CaseDir, approvals


def _question(case_dir):
    text = (case_dir / "brief.md").read_text()
    return next(ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#"))


def test_question_on_list_overview_and_every_review(client, cases_dir):
    lib = cases_dir / "libraries" / "case"
    question = _question(lib)
    home = client.get("/").text
    assert question in home and _question(cases_dir / "parks" / "case") in home  # one line per case
    overview = client.get("/cases/libraries").text
    assert "The question" in overview and question in overview
    items = approvals(CaseDir(lib))
    assert {a.checkpoint for a in items} >= {"prd", "factors", "ontology", "action"}
    for a in items:
        page = client.get(f"/cases/libraries/approvals/{a.phase_dir}").text
        assert "The question" in page and question in page, a.phase_dir
        assert page.index("The question") < page.index("Decision") if "Decision" in page else True


def test_case_without_brief_says_so(client, cases_dir):
    (cases_dir / "parks" / "case" / "brief.md").unlink()
    page = client.get("/cases/parks").text
    assert 'has no <span class="mono">brief.md</span> yet' in page
