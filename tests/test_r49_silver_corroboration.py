"""Silver retains all source receipts and counts independent corroboration."""

from ontofill.refiner import MemorySilverStore, Observation


def _observation(source: str, publisher: str, value: str) -> Observation:
    return Observation(
        run_id="mock-r49-silver",
        entity_id="Record:one",
        entity_class="Record",
        property_id="status",
        value=value,
        evidence={"source_id": source},
        step_id=f"step:{source}",
        generated_by={"backend": "recorded", "model": "synthetic", "at": "2026-01-01T00:00:00Z"},
        authority_tier="secondary",
        publisher_id=publisher,
    )


def test_silver_counts_independent_publishers_per_literal_value() -> None:
    store = MemorySilverStore()
    store.add(_observation("mirror-a", "publisher-one", "current"))
    store.add(_observation("mirror-b", "publisher-one", "current"))
    store.add(_observation("registry", "publisher-two", "current"))
    store.add(_observation("archive", "publisher-three", "former"))

    observed = store.list_for_run("mock-r49-silver")

    assert len(observed) == 4
    assert {item.authority_tier for item in observed} == {"secondary"}
    assert {item.corroboration_count for item in observed if item.value == "current"} == {2}
    assert {item.corroboration_count for item in observed if item.value == "former"} == {1}
