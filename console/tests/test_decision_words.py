"""The decision history names each entry by what it was: an approval is "approved", a runner start is "started a
run", the kill switch is "kill switch on", and so on. A runner action never reads as an approval."""

import json


def test_runner_and_case_entries_are_not_approvals(client, cases_dir):
    log = cases_dir / "libraries" / "case" / "decisions.jsonl"
    rows = [
        {
            "ts": "2031-01-01T00:00:00+00:00",
            "checkpoint": "runner",
            "decision": "start",
            "run_id": "run-x",
            "approver": "approvers",
            "unverified_name": "Ana",
            "identity_source": "sso-group",
        },
        {
            "ts": "2031-01-01T00:01:00+00:00",
            "checkpoint": "runner_kill",
            "decision": "on",
            "approver": "approvers",
            "unverified_name": "Ana",
            "identity_source": "sso-group",
        },
        {
            "ts": "2031-01-01T00:02:00+00:00",
            "checkpoint": "case",
            "decision": "archive",
            "approver": "approvers",
            "unverified_name": "Ana",
            "identity_source": "sso-group",
        },
    ]
    with log.open("a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    html = client.get("/cases/libraries/approvals").text
    assert "started a run" in html and "kill switch on" in html and "archived" in html
    table = html.split('class="ledger decisions"', 1)[1].split("</table>", 1)[0]
    assert "<td> · " not in table  # no dangling separator when an entry has no phase_dir
    for row in table.split("<tr>")[1:]:
        if any(k in row for k in ("<td>runner", "<td>kill switch", "<td>case record")):
            assert "approved" not in row, row
