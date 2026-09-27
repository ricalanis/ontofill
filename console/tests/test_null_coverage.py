"""R10: a taxonomy's coverage (or soundness) may be null ("not yet classified"). The ontology review, the Definition
view, the run page and the Output view must render it as such, never raise on formatting."""

import json

import pytest


@pytest.fixture
def null_coverage(cases_dir):
    path = cases_dir / "libraries" / "case" / "02-ontology" / "ontology.json"
    onto = json.loads(path.read_text())
    assert onto.get("taxonomies"), "fixture has taxonomies"
    onto["taxonomies"][0]["coverage"] = None
    onto["taxonomies"][0]["coverage_basis"] = "none"
    if len(onto["taxonomies"]) > 1:
        onto["taxonomies"][1]["soundness"] = None
        onto["taxonomies"][1]["coverage"] = None
    path.write_text(json.dumps(onto))
    metrics_paths = list((cases_dir / "libraries" / "lake").rglob("metrics.json"))
    for mp in metrics_paths:
        m = json.loads(mp.read_text())
        if isinstance(m.get("level_ratio_coverage"), dict):
            m["level_ratio_coverage"] = {k: [None for _ in v] for k, v in m["level_ratio_coverage"].items()}
            mp.write_text(json.dumps(m))
    return cases_dir


def test_ontology_review_with_null_coverage(client, null_coverage):
    page = client.get("/cases/libraries/approvals/02-ontology")
    assert page.status_code == 200
    assert "not yet classified" in page.text and "basis: none" in page.text
    for path in (
        "/cases/libraries/definition",
        "/cases/libraries/output",
        "/cases/libraries/tour",
        "/tour?case=libraries",
        "/cases/libraries/runs/run-libraries-0001",
    ):
        r = client.get(path)
        assert r.status_code in (200, 404), path  # 404 only for a route that doesn't exist, never a 500
        assert "Traceback" not in r.text
    assert client.get("/cases/libraries/runs/run-libraries-0001").status_code == 200
    assert client.get("/cases/libraries/api/runs/run-libraries-0001").status_code == 200
