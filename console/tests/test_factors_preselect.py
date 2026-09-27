"""The factors review makes its default visible: a grounded factor with no evidence starts at Reject, so a reviewer who
approves as-is drops it. The page says how many start at each answer and tags each preselected reject."""

import json
import re

PAGE = "/cases/libraries/approvals/02-ontology/factors"


def test_preselection_is_summarised_and_tagged(client, cases_dir):
    path = cases_dir / "libraries" / "case" / "02-ontology" / "factors" / "factors.json"
    doc = json.loads(path.read_text())
    doc["factors"] += [
        {"id": "hours_grounded", "label": "Opening hours", "description": "d", "kind": "grounded", "evidence": []},
        {"id": "wifi_grounded", "label": "Wi-Fi", "description": "d", "kind": "grounded"},
        {"id": "access_idea", "label": "Access", "description": "d", "kind": "conceptual", "evidence": []},
    ]
    path.write_text(json.dumps(doc))
    html = client.get(PAGE).text
    n = len(doc["factors"])
    assert f"Preselected: {n - 2} accept, 2 reject." in html
    assert html.count("Preselected: reject") == 2
    signoff = html.split('id="signoff-h"', 1)[1]
    assert f"Preselected: {n - 2} accept, 2 reject." in signoff and "rejects Opening hours, Wi-Fi" in signoff
    assert 'value="reject" checked' in html  # the rule itself is unchanged


def test_no_summary_when_every_grounded_factor_has_evidence(client):
    assert "Preselected:" not in client.get(PAGE).text


def test_requested_time_is_readable(client):
    html = client.get(PAGE).text
    assert re.search(r'Requested <span class="mono" title="[^"]*">(\d{4}-\d\d-\d\d \d\d:\d\d UTC|—)</span>', html)
