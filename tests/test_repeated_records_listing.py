"""A listing laid out as repeated blocks (a CMS "views" list of places) is a listing, not only a <table>. Live
(sf-library-branches run-09ed86537750) the publisher's own page carried every branch as a repeated block with name,
address, telephone and weekly hours, but P3's critic rejected it ("captured page has no tabular listing structure")
because the parser only counted <table> rows. Synthetic markup only."""

from tests.r17_helpers import _PARSER

CARD = """<div class="place place--overview"><h3 class="place__title"><a href="/places/{slug}">{name}</a></h3>
<div class="place__address"><div class="label">Address</div><span class="line1">{n} Example Street</span>
<span class="locality">Example City</span></div><div class="place__phone"><div class="label">Telephone</div>
<span>555-0{n:03d}</span></div><div class="hours"><span class="day">Mon</span><span>9 - 6</span>
<span class="day">Tue</span><span>10 - 8</span></div></div>"""
NAV = "".join(f'<li class="menu-item"><a href="/m{i}">Menu {i}</a></li>' for i in range(12))


def _page(cards: int) -> bytes:
    body = "".join(CARD.format(slug=f"p{i}", name=f"Branch Ejemplo {i}", n=i) for i in range(cards))
    return f"<html><body><ul>{NAV}</ul>{body}</body></html>".encode()


def test_repeated_blocks_are_a_listing_with_shared_labels() -> None:
    rows, *_ = _PARSER._parse(_page(5), "html", 1000, "https://library.example/")
    records = [row for row in rows if row["sheet"] == "html-records-1"]
    assert len(records) == 5
    headers = _PARSER._parse_table_headers(_page(5))
    assert headers and {"Address", "Telephone", "Mon", "Tue"} <= set(headers[-1])
    assert all(
        "Branch Ejemplo" not in label for label in headers[-1]
    )  # a record's own name is not a label


def test_a_navigation_menu_is_not_a_listing() -> None:
    page = f"<html><body><ul>{NAV}</ul></body></html>".encode()
    rows, *_ = _PARSER._parse(page, "html", 1000, "https://library.example/")
    assert not [row for row in rows if row["sheet"].startswith("html-records-")]
    assert _PARSER._parse_table_headers(page) == []


def test_two_blocks_are_not_enough_and_tables_are_unchanged() -> None:
    rows, *_ = _PARSER._parse(_page(2), "html", 1000, "https://library.example/")
    assert not [row for row in rows if row["sheet"].startswith("html-records-")]
    table = b"<table><tr><th>Name</th><th>City</th></tr><tr><td>A</td><td>B</td></tr></table>"
    rows, *_ = _PARSER._parse(table, "html", 1000, "https://library.example/")
    assert [row["sheet"] for row in rows] == ["html-table-1", "html-table-1"]
    assert _PARSER._parse_table_headers(table) == [["Name", "City"]]
