"""Static web assets are not source leads or browser capture jobs."""

from __future__ import annotations

import pytest

from ontofill.inference import RecordedDecisionClient
from ontofill.phases.p3_fanout.discovery_loop import NoConfirmedSources, _static_asset_reason
from ontofill.phases.p3_fanout.phase import discover_objectives
from tests.test_discovery_loop import PAGE, StaticProvider, _library_case, _loop


@pytest.mark.parametrize(
    "url,content_type",
    [
        ("https://libraries.example.test/theme-light.CSS?v=3", None),
        ("https://libraries.example.test/favicon.ico", None),
        ("https://libraries.example.test/app.js.map", None),
        ("https://libraries.example.test/font.woff2", None),
        ("https://libraries.example.test/api/resource", "image/x-icon"),
        ("https://libraries.example.test/api/resource", "text/css; charset=utf-8"),
        ("https://libraries.example.test/styleswitcher/css/theme?version=1", None),
        ("https://libraries.example.test/fonts/branch-display", None),
    ],
)
def test_static_asset_classification(url: str, content_type: str | None) -> None:
    assert _static_asset_reason(url, content_type) is not None


@pytest.mark.parametrize("suffix", ["csv", "xls", "xlsx", "zip", "json", "pdf"])
def test_data_document_formats_remain_candidates(suffix: str) -> None:
    assert _static_asset_reason(f"https://libraries.example.test/records.{suffix}") is None
    assert _static_asset_reason(f"https://libraries.example.test/css/records.{suffix}") is None


def test_suffixless_data_api_remains_a_candidate() -> None:
    assert (
        _static_asset_reason("https://libraries.example.test/api/branch-records?format=json")
        is None
    )


def test_static_leads_skip_pods_and_emit_trace(tmp_path) -> None:
    ontology = _library_case(tmp_path)
    page = "https://libraries.example.test/branches"
    static = [
        "https://libraries.example.test/theme-light.css?v=3",
        "https://libraries.example.test/favicon.ico",
        "https://libraries.example.test/logo.webp",
    ]
    props = ("name", "free_internet", "opening_hours")
    provider = StaticProvider("synthetic", {**dict.fromkeys(static, props), page: props})
    loop, capture = _loop(tmp_path, [provider], {page: PAGE.format(title="Branches")})

    # The single fixture page need not satisfy the library's two-publisher DoD.
    with pytest.raises(NoConfirmedSources):
        discover_objectives(tmp_path, ontology, RecordedDecisionClient({}), loop)

    assert page in capture.calls
    assert not set(static) & set(capture.calls)
    skipped = [step for step in loop.trace if step["requested"].get("tool") == "lead.skip"]
    assert {step["requested"]["url"] for step in skipped} == {
        url.split("?", 1)[0] for url in static
    }
    assert all(step["evaluated"]["reason"] == "static_asset" for step in skipped)
