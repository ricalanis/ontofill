"""A city brief can ground its primary publisher at the country level."""

from copy import deepcopy

from ontofill.phases.p1_scope.phase import (
    _authority_policy_check,
    _inherit_primary_jurisdictions,
    _jurisdiction_matches,
)
from tests.test_scope_thresholds import _prd_response


def test_country_name_matches_city_scope_without_matching_another_country() -> None:
    assert _jurisdiction_matches("Cedar City, Utah, USA", "United States")
    assert _jurisdiction_matches("Cedar City, Utah, USA", "US")
    assert not _jurisdiction_matches("Cedar City, Utah, USA", "United Kingdom")


def test_missing_primary_jurisdiction_inherits_policy_scope() -> None:
    document = deepcopy(_prd_response())
    policy = document["authority_policy"]
    policy["jurisdiction"] = "Cedar City, Utah, USA"
    primary = next(p for p in policy["trusted_publishers"] if p.get("tier") == "primary")
    primary.pop("jurisdiction", None)
    _inherit_primary_jurisdictions(document)
    assert primary["jurisdiction"] == policy["jurisdiction"]
    assert _authority_policy_check(document, []).passed


def test_unmatched_primary_objection_names_actual_jurisdictions() -> None:
    document = deepcopy(_prd_response())
    policy = document["authority_policy"]
    policy["jurisdiction"] = "Cedar City, Utah, USA"
    for publisher in policy["trusted_publishers"]:
        if publisher.get("tier") == "primary":
            publisher["jurisdiction"] = "United Kingdom"
    result = _authority_policy_check(document, [])
    assert not result.passed
    assert any(
        "Cedar City, Utah, USA" in issue and "United Kingdom" in issue
        for issue in result.objections
    )
