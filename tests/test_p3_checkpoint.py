"""Restart and invalidation behavior for the durable P3 checkpoint."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

import ontofill.phases.p3_fanout.checkpoint as p3_checkpoint
from ontofill.phases.p3_fanout.checkpoint import (
    CAPTURE_TTL,
    CaptureRecord,
    CheckpointInputs,
    CriticVerdict,
    GapState,
    Lead,
    P3Checkpoint,
    QueryRun,
    check,
    checkpoint_path,
    content_digest,
    load,
    reuse,
    save,
)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
BRONZE_KEY = "sha256:" + "a" * 64
METADATA_DIGEST = "b" * 64


def make_inputs(**changes: str) -> CheckpointInputs:
    values = {
        "prd_digest": "1" * 64,
        "ontology_digest": "2" * 64,
        "authority_digest": "3" * 64,
        "approvals_digest": "4" * 64,
        "p3_algorithm_version": "p3-v1",
        "critic_version": "critic-v1",
    }
    values.update(changes)
    return CheckpointInputs(**values)


def make_checkpoint(inputs: CheckpointInputs | None = None) -> P3Checkpoint:
    inputs = inputs or make_inputs()
    return P3Checkpoint(
        inputs=inputs,
        updated_at=NOW - timedelta(minutes=2),
        query_runs=(QueryRun("supplier registry records", "search", 3),),
        leads=(
            Lead(
                "supplier registry records",
                "search",
                "https://Registry.Example.gov/datasets?id=12#top",
                "Registry Example",
            ),
        ),
        captures=(
            CaptureRecord(
                "https://registry.example.gov/datasets?id=12",
                "Registry Example",
                BRONZE_KEY,
                METADATA_DIGEST,
                NOW - timedelta(hours=1),
            ),
        ),
        critic_verdicts=(
            CriticVerdict(
                BRONZE_KEY,
                inputs.critic_version,
                "accepted",
                capability_ids=("Supplier/name",),
                reason_codes=("entity_records",),
            ),
        ),
        iteration_count=3,
        gap_state=(GapState("supplier/name", "open", "needs_source", 1),),
    )


def test_restart_loads_typed_state_and_returns_same_recent_capture(tmp_path) -> None:
    inputs = make_inputs()
    path = checkpoint_path(tmp_path)
    save(path, make_checkpoint(inputs))

    loaded = load(path)
    assert loaded.reason == "loaded"
    assert loaded.checkpoint is not None
    resumed = check(loaded.checkpoint, inputs, now=NOW)

    assert resumed.checkpoint.query_runs[0].query == "supplier registry records"
    assert resumed.checkpoint.iteration_count == 3
    assert resumed.checkpoint.gap_state[0].gap_id == "supplier/name"
    assert {"query_runs", "leads", "captures", "critic_verdicts"}.issubset(resumed.reused_parts)
    assert resumed.invalidated_parts == ()
    found = reuse(
        resumed.checkpoint,
        inputs,
        url="https://REGISTRY.example.gov/datasets?id=12#another-fragment",
        source_identity="Registry Example",
        verified_captures={(BRONZE_KEY, METADATA_DIGEST)},
        now=NOW,
    )
    assert found is not None
    assert found.bronze_key == BRONZE_KEY

    document = json.loads(path.read_text(encoding="utf-8"))
    assert "run_id" not in document
    assert "budget" not in document
    assert "raw_page_text" not in document
    assert "live_view_url" not in document
    assert document["generated_by"]["backend"] == "recorded"
    assert path.parent == tmp_path / "03-fanout" / "cache"


def test_url_query_values_are_not_persisted_but_remain_part_of_identity(tmp_path) -> None:
    inputs = make_inputs()
    secret_value = "opaque-unrecognized-signature-value"
    raw_url = f"https://registry.example.gov/datasets?custom={secret_value}"
    state = P3Checkpoint(
        inputs=inputs,
        updated_at=NOW,
        leads=(
            Lead(
                "registry records", "search", raw_url, "source-123", property_ids=("supplier/name",)
            ),
        ),
        captures=(
            CaptureRecord(
                raw_url,
                "source-123",
                BRONZE_KEY,
                METADATA_DIGEST,
                NOW,
            ),
        ),
    )
    path = checkpoint_path(tmp_path)
    save(
        path,
        state,
        generated_by={"backend": "recorded", "model": "synthetic", "at": NOW.isoformat()},
    )

    payload = path.read_text(encoding="utf-8")
    loaded = load(path).checkpoint
    assert secret_value not in payload
    assert "custom=" not in payload
    assert loaded is not None
    assert loaded.leads[0].url == "https://registry.example.gov/datasets"
    assert loaded.leads[0].property_ids == ("supplier/name",)
    assert (
        reuse(
            loaded,
            inputs,
            url=raw_url,
            source_identity="source-123",
            verified_captures={(BRONZE_KEY, METADATA_DIGEST)},
            now=NOW,
        )
        is not None
    )
    assert (
        reuse(
            loaded,
            inputs,
            url="https://registry.example.gov/datasets?custom=different-value",
            source_identity="source-123",
            verified_captures={(BRONZE_KEY, METADATA_DIGEST)},
            now=NOW,
        )
        is None
    )


def test_capture_reuse_expires_after_six_hours(tmp_path) -> None:
    inputs = make_inputs()
    state = make_checkpoint(inputs)
    capture = replace(state.captures[0], captured_at=NOW)
    state = replace(state, captures=(capture,))
    verified = {(capture.bronze_key, capture.metadata_digest)}

    at_limit = reuse(
        state,
        inputs,
        url="https://registry.example.gov/datasets?id=12",
        source_identity=capture.source_identity,
        verified_captures=verified,
        now=NOW + CAPTURE_TTL,
    )
    expired = reuse(
        state,
        inputs,
        url="https://registry.example.gov/datasets?id=12",
        source_identity=capture.source_identity,
        verified_captures=verified,
        now=NOW + CAPTURE_TTL + timedelta(seconds=1),
    )

    assert at_limit == capture
    assert expired is None
    checked = check(state, inputs, now=NOW + CAPTURE_TTL + timedelta(seconds=1))
    assert checked.checkpoint.captures == ()
    assert "captures" in checked.invalidated_parts


@pytest.mark.parametrize(
    ("changed", "kept", "dropped"),
    [
        (
            {"prd_digest": "5" * 64},
            {"captures"},
            {"query_runs", "leads", "critic_verdicts", "iteration_count", "gap_state"},
        ),
        (
            {"ontology_digest": "6" * 64},
            {"captures"},
            {"query_runs", "leads", "critic_verdicts", "iteration_count", "gap_state"},
        ),
        (
            {"approvals_digest": "7" * 64},
            {"query_runs", "iteration_count"},
            {"leads", "captures", "critic_verdicts", "gap_state"},
        ),
        (
            {"authority_digest": "8" * 64},
            {"query_runs", "iteration_count"},
            {"leads", "captures", "critic_verdicts", "gap_state"},
        ),
        (
            {"critic_version": "critic-v2"},
            {"query_runs", "leads", "captures", "iteration_count", "gap_state"},
            {"critic_verdicts"},
        ),
        (
            {"p3_algorithm_version": "p3-v2"},
            {"captures", "critic_verdicts"},
            {"query_runs", "leads", "iteration_count", "gap_state"},
        ),
    ],
)
def test_changed_inputs_invalidate_only_dependent_parts(changed, kept, dropped) -> None:
    previous = make_checkpoint()
    current_inputs = make_inputs(**changed)

    result = check(previous, current_inputs, now=NOW)

    state = result.checkpoint
    populated = {
        "query_runs": bool(state.query_runs),
        "leads": bool(state.leads),
        "captures": bool(state.captures),
        "critic_verdicts": bool(state.critic_verdicts),
        "iteration_count": state.iteration_count > 0,
        "gap_state": bool(state.gap_state),
    }
    assert {name for name, present in populated.items() if present} == kept
    assert dropped.issubset(result.invalidated_parts)
    assert result.changed_inputs == tuple(changed)


def test_reuse_requires_fresh_external_bronze_and_metadata_verification() -> None:
    inputs = make_inputs()
    state = make_checkpoint(inputs)
    capture = state.captures[0]

    assert (
        reuse(
            state,
            inputs,
            url=capture.url,
            source_identity=capture.source_identity,
            verified_captures=set(),
            now=NOW,
        )
        is None
    )
    assert (
        reuse(
            state,
            inputs,
            url=capture.url,
            source_identity="Different publisher",
            verified_captures={(capture.bronze_key, capture.metadata_digest)},
            now=NOW,
        )
        is None
    )
    changed_approval = make_inputs(approvals_digest="9" * 64)
    assert (
        reuse(
            state,
            changed_approval,
            url=capture.url,
            source_identity=capture.source_identity,
            verified_captures={(capture.bronze_key, capture.metadata_digest)},
            now=NOW,
        )
        is None
    )

    changed_prd = make_inputs(prd_digest="a" * 64)
    assert (
        reuse(
            state,
            changed_prd,
            url=capture.url,
            source_identity=capture.source_identity,
            verified_captures={(capture.bronze_key, capture.metadata_digest)},
            now=NOW,
        )
        is None
    )
    rechecked = check(state, changed_prd, now=NOW).checkpoint
    assert (
        reuse(
            rechecked,
            changed_prd,
            url="https://registry.example.gov/datasets?id=12",
            source_identity=capture.source_identity,
            verified_captures={(capture.bronze_key, capture.metadata_digest)},
            now=NOW,
        )
        == capture
    )


def test_corrupt_and_unmodeled_cache_content_fails_closed(tmp_path) -> None:
    path = tmp_path / "p3-checkpoint.json"
    path.write_text("{broken", encoding="utf-8")
    assert load(path).reason == "invalid"

    save(path, make_checkpoint())
    document = json.loads(path.read_text(encoding="utf-8"))
    document["leads"][0]["url"] += "?opaque=unrecognized-signed-token"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert load(path).reason == "invalid"

    document["leads"][0]["url"] = "https://registry.example.gov/datasets"
    document["raw_page_text"] = "captured page bytes must not be persisted"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert load(path).checkpoint is None

    empty = check(None, make_inputs(), now=NOW)
    assert empty.checkpoint.query_runs == ()
    assert empty.reused_parts == ()


def test_urls_with_credentials_are_rejected_before_cache_write() -> None:
    with pytest.raises(ValueError, match="credentials|credential"):
        Lead(
            "public data registry",
            "search",
            "https://records.example.gov/search?access_token=secret-value",
            "Records Publisher",
        )


@pytest.mark.parametrize(
    "url",
    (
        "http://localhost/data",
        "https://metadata.internal/data",
        "https://192.0.2.10/data",
        "https://127.0.0.1/data",
    ),
)
def test_checkpoint_refuses_non_public_hosts(url: str) -> None:
    with pytest.raises(ValueError, match="public DNS host"):
        Lead("public data registry", "search", url, "Records Publisher")

    state = make_checkpoint()
    capture = state.captures[0]
    assert (
        reuse(
            state,
            state.inputs,
            url=url,
            source_identity=capture.source_identity,
            verified_captures={(capture.bronze_key, capture.metadata_digest)},
            now=NOW,
        )
        is None
    )


def test_checkpoint_accepts_authorship_backend_from_common_schema(tmp_path) -> None:
    path = tmp_path / "p3-checkpoint.json"
    save(
        path,
        make_checkpoint(),
        generated_by={"backend": "jev", "model": "reviewer-v1", "at": NOW.isoformat()},
    )

    loaded = load(path)
    assert loaded.reason == "loaded"
    assert loaded.checkpoint is not None


def test_save_replaces_atomically_and_preserves_old_file_on_replace_error(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "p3-checkpoint.json"
    original = make_checkpoint()
    save(path, original)
    previous_bytes = path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(p3_checkpoint.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        save(path, replace(original, iteration_count=4))

    assert path.read_bytes() == previous_bytes
    assert list(tmp_path.glob(".p3-checkpoint.json.*.tmp")) == []
    assert path.stat().st_mode & 0o077 == 0


def test_load_rejects_duplicate_keys_and_oversized_files(tmp_path, monkeypatch) -> None:
    path = tmp_path / "p3-checkpoint.json"
    path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
    assert load(path).reason == "invalid"

    monkeypatch.setattr(p3_checkpoint, "MAX_CHECKPOINT_BYTES", 4)
    path.write_bytes(b"12345")
    assert load(path).reason == "invalid"

    monkeypatch.setattr(p3_checkpoint, "MAX_CHECKPOINT_BYTES", 8 * 1024 * 1024)
    path.write_text("[" * 2_000 + "0" + "]" * 2_000, encoding="utf-8")
    assert load(path).reason == "invalid"


def test_content_digest_uses_exact_input_bytes() -> None:
    assert content_digest(b"prd") == content_digest("prd")
    assert content_digest("prd\n") != content_digest("prd")
