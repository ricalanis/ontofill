"""An absent value renders as nothing or "—", never as the Python "None": an ontology whose classes and properties have
no alignment (aligned_to: null, schema-valid) must read cleanly on every view that lists them."""

import json
import re

import pytest

PAGES = (
    "/cases/libraries/approvals/02-ontology",
    "/cases/libraries",
    "/cases/libraries/definition",
    "/cases/libraries/entities",
    "/cases/libraries/graph",
    "/cases/libraries/output",
    "/cases/libraries/summary",
    "/tour?case=libraries",
)


@pytest.fixture
def unaligned(cases_dir):
    path = cases_dir / "libraries" / "case" / "02-ontology" / "ontology.json"
    onto = json.loads(path.read_text())
    for item in (*onto["classes"], *onto["properties"]):
        item["aligned_to"] = None
    path.write_text(json.dumps(onto))
    return onto


def visible(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def test_no_view_prints_none(client, unaligned):
    for page in PAGES:
        r = client.get(page)
        assert r.status_code == 200, page
        assert not re.search(r"\bNone\b", visible(r.text)), page


def test_ontology_review_lists_each_class_with_its_label(client, unaligned):
    html = client.get("/cases/libraries/approvals/02-ontology").text
    for c in unaligned["classes"]:
        assert c["label"] in html and c["description"] in html
