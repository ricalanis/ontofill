"""Tour (a guided walkthrough of the ten §7a questions and the track requirements): the page and its JSON twin render
for a full case and a brief-only case, readiness chips follow the data, every link on the page resolves, unknown case
and run ids are 404, and a live run is marked live by operation's rule."""

import html
import json
import re
from datetime import UTC, datetime

import jsonschema
from test_viz_health import R2, two_runs  # noqa: F401 (fixture)

BAD = ("built-in method", "Undefined", "Traceback")
LIVE_STATUS = "libraries/lake/runs/fixture-libraries/run-libraries-0001/status.json"

GAP = {"type": ["object", "null"], "required": ["what", "source", "row", "row_desc"]}
STATE = {"enum": ["ready", "partial", "empty"]}
STEP = {"type": "object",
        "required": ["n", "id", "question", "proves", "href", "view", "hint", "state", "css", "state_label", "why", "gap", "also"],
        "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 10}, "state": STATE,
                       "css": {"enum": ["done", "pause", "none"]}, "href": {"type": "string", "pattern": "^/"},
                       "gap": GAP, "also": {"type": "array", "items": {"type": "object", "required": ["label", "href"],
                                                                        "properties": {"href": {"pattern": "^/"}}}}}}
TRACK = {"type": "object", "required": ["requirement", "proof", "href", "view", "state", "css", "state_label", "why", "gap"],
         "properties": {"state": STATE, "href": {"type": "string", "pattern": "^/"}, "gap": GAP}}
RUN = {"type": "object", "required": ["run_id", "live", "kind", "latest", "state", "updated_at", "has_feed", "selected", "href"],
       "properties": {"live": {"type": "boolean"}, "kind": {"enum": ["live", "recorded"]}, "href": {"pattern": "^/tour\\?"}}}
COUNTS = {"type": "object", "required": ["ready", "partial", "empty"],
          "additionalProperties": {"type": "integer", "minimum": 0}}
TOUR = {"type": "object",
        "required": ["case_id", "title", "question", "cases", "run_id", "runs", "run_live", "steps", "track", "counts",
                     "track_counts", "start_href", "backend", "sources", "empty"],
        "properties": {"cases": {"type": "array", "items": {"type": "object", "required": ["id", "title", "has_run", "selected"]}},
                       "runs": {"type": "array", "items": RUN},
                       "steps": {"type": "array", "items": STEP, "minItems": 10, "maxItems": 10},
                       "track": {"type": "array", "items": TRACK, "minItems": 8},
                       "counts": COUNTS, "track_counts": COUNTS, "empty": GAP}}


def _ok(page: str) -> None:
    for bad in BAD:
        assert bad not in page, bad


def _by(rows, key, value):
    return next(r for r in rows if value in r[key])


def test_tour_json_and_page_on_the_full_case(client):
    m = client.get("/api/viz/tour").json()
    jsonschema.validate(m, TOUR)
    assert m["case_id"] == "libraries" and m["run_id"] == "run-libraries-0001"  # the first case with a run
    assert [s["n"] for s in m["steps"]] == list(range(1, 11))
    assert m["start_href"] == m["steps"][0]["href"] == "/inbox"
    assert m["counts"]["ready"] >= 6 and m["counts"]["empty"] == 0
    steps = {s["n"]: s for s in m["steps"]}
    for n in (1, 2, 5, 6, 7, 8):
        assert steps[n]["state"] == "ready", (n, steps[n]["why"])
    assert steps[4]["state"] == "partial" and steps[4]["gap"]["row"] == "R15"  # pages yes, site graphs not yet
    assert steps[5]["href"].startswith("/cases/libraries/lineage/") and "?run=run-libraries-0001" in steps[5]["href"]
    assert steps[10]["href"] == "/compare?a=libraries&b=parks"
    track = m["track"]
    for req in ("process isolation", "credentials", "caps", "destroy", "Vision", "approve-before-submit",
                "hostile page", "endless loop"):
        assert _by(track, "requirement", req)["state"] == "ready", req
    assert _by(track, "requirement", "Vultr")["state"] == "partial"  # the fixture run is recorded inference
    assert _by(track, "requirement", "Zero inbound ports")["state"] == "empty"
    assert _by(track, "requirement", "approve-before-submit")["href"] == "/cases/libraries/approvals"
    assert _by(track, "requirement", "Vision")["href"] == "/cases/libraries/runs/run-libraries-0001"

    page = client.get("/tour").text
    _ok(page)
    assert page.count('class="tour-step st-') == 10 and page.count('class="tour-req st-') == len(track)
    assert f"ready {m['counts']['ready']}" in page and f"partial {m['counts']['partial']}" in page
    assert "Start the tour" in page and 'aria-current="page">Tour<' in page
    for s in m["steps"]:
        assert html.escape(s["question"]) in page and html.escape(s["href"]) in page


def test_tour_brief_only_case_is_honest(client):
    m = client.get("/api/viz/tour?case=parks").json()
    jsonschema.validate(m, TOUR)
    assert m["case_id"] == "parks" and m["run_id"] is None and m["runs"] == [] and m["empty"]
    assert m["counts"]["ready"] == 0 and m["counts"]["empty"] >= 6
    rows = {s["gap"]["row"] for s in m["steps"] if s["state"] == "empty" and s["gap"]}
    assert {"R4", "R8", "R15"} <= rows, rows
    assert all(t["state"] == "empty" for t in m["track"])
    assert {"R3", "R4", "R8"} <= {t["gap"]["row"] for t in m["track"] if t["gap"]}
    page = client.get("/tour?case=parks").text
    _ok(page)
    assert "Produced by gap R8" in page and "no run yet" in page


def test_unknown_case_and_run_are_404(client):
    for url in ("/tour?case=nope", "/api/viz/tour?case=nope", "/tour?run=nope", "/api/viz/tour?case=parks&run=x"):
        assert client.get(url).status_code == 404, url


def test_every_link_on_the_tour_resolves(client):
    for url in ("/tour", "/tour?case=parks"):
        page = client.get(url).text
        hrefs = {html.unescape(h) for h in re.findall(r'href="(/[^"#]*)', page)}
        assert len(hrefs) > 15
        for h in sorted(hrefs):
            assert client.get(h).status_code == 200, (url, h)


def test_runs_listed_and_live_marked(make_client, cases_dir, two_runs):  # noqa: F811
    path = cases_dir / LIVE_STATUS
    status = json.loads(path.read_text())
    status.update(state="running", updated_at=datetime.now(UTC).isoformat())
    path.write_text(json.dumps(status))
    c = make_client()
    m = c.get("/api/viz/tour").json()
    runs = {r["run_id"]: r for r in m["runs"]}
    assert {"run-libraries-0001", R2} <= set(runs)
    assert runs["run-libraries-0001"]["live"] and runs["run-libraries-0001"]["kind"] == "live"
    assert not runs[R2]["live"] and runs[R2]["kind"] == "recorded"
    m2 = c.get(f"/api/viz/tour?run={R2}").json()
    jsonschema.validate(m2, TOUR)
    assert m2["run_id"] == R2 and not m2["run_live"]
    assert all(f"run={R2}" in s["href"] for s in m2["steps"] if s["href"].startswith("/cases/") and "/lineage/" not in s["href"])
    page = c.get(f"/tour?run={R2}").text
    _ok(page)
    for h in {html.unescape(h) for h in re.findall(r'href="(/cases/[^"#]*)', page)}:
        assert c.get(h).status_code == 200, h


def test_zero_port_row_reads_the_saved_remote_check(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOFILL_CONSOLE_EVIDENCE_DIR", str(tmp_path))
    (tmp_path / "verify-remote.txt").write_text("  PASS  VM port 22 closed\n  PASS  VM port 443 closed\nRESULT: PASS\n")
    row = _by(client.get("/api/viz/tour").json()["track"], "requirement", "Zero inbound ports")
    assert row["state"] == "ready" and "2 of 2" in row["why"]
