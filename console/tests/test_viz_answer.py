"""The answer page: a case's question answered from gold, one card per primary-class entity, each value one click from
its evidence; before gold, an honest live state. Built for the second living case (a city library's branches)."""

import json
import shutil

from test_viz_live import RUN, run_dir

from ontofill_console.viz import answer


def test_answer_from_gold(client):
    m = client.get("/cases/libraries/api/viz/answer").json()
    assert m["has_gold"] and m["n_cards"] > 0 and m["fields"]
    card = m["cards"][0]
    filled = [r for r in card["rows"] if r["filled"]]
    assert filled and all(r["value"] for r in filled)
    with_ev = next(r for c in m["cards"] for r in c["rows"] if r["evidence"])
    assert with_ev["evidence"][0]["host"] and with_ev["lineage_href"].startswith("/cases/libraries/lineage/")
    html = client.get("/cases/libraries/answer").text
    assert card["title"] in html and "Open a value to see where it came from" in html
    assert "None" not in html.replace("None stated", "")


def test_no_gold_says_so_and_shows_the_live_run(client, cases_dir):
    lake = cases_dir / "libraries" / "lake"
    shutil.rmtree(lake / "gold")
    m = client.get("/cases/libraries/api/viz/answer").json()
    assert not m["has_gold"] and m["cards"] == []
    assert m["live"]["run_id"] == RUN and m["live"]["n_steps"] > 0
    html = client.get("/cases/libraries/answer").text
    assert "No answer yet" in html and "Nothing on this" in html and f"/runs/{RUN}" in html


def test_values_read_as_words_not_reprs():
    assert answer.show(True) == "Yes" and answer.show(False) == "No"
    assert answer.show({"mon": "10-18", "sun": None}) == "mon: 10-18"
    assert answer.show(["a", None, "b"]) == "a\nb" and answer.show(None) == ""


def test_the_question_is_the_briefs_first_line(client):
    m = client.get("/cases/libraries/api/viz/answer").json()
    assert m["question"] and not m["question"].startswith("#")
    assert json.dumps(m)  # the JSON twin serializes
    assert run_dir  # fixture helpers importable


def test_the_heading_is_the_question_and_the_rest_follows(client, cases_dir):
    """Live: the SF brief is one paragraph, so the heading was the whole paragraph. It is now the question, with the
    rest of the brief under it."""
    (cases_dir / "libraries" / "case" / "brief.md").write_text(
        "Which branches offer free Wi-Fi, and when is each one open? List every branch with its address.\n"
    )
    m = client.get("/cases/libraries/api/viz/answer").json()
    assert m["question"] == "Which branches offer free Wi-Fi, and when is each one open?"
    assert m["brief_rest"] == "List every branch with its address."
    html = client.get("/cases/libraries/answer").text
    assert '<h1 class="ans-q">Which branches offer free Wi-Fi, and when is each one open?</h1>' in html


def test_a_checkpoint_wait_is_said_once(client, cases_dir):
    shutil.rmtree(cases_dir / "libraries" / "lake" / "gold")
    html = client.get("/cases/libraries/answer").text
    m = client.get("/cases/libraries/api/viz/answer").json()
    if m["live"]["checkpoint"]:
        assert html.count("waiting for") == 1
    assert m["question"].endswith("?") or "?" not in m["brief"][:300]
