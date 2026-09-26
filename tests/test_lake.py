import hashlib

import pytest

from ontofill.lake import FileLake


def test_file_lake_keeps_bronze_content_addressed_and_gold_layout(tmp_path) -> None:
    lake = FileLake(tmp_path.as_uri())
    content = b"<html><title>Example</title></html>"
    key = lake.put_bytes(content)
    assert key == f"sha256:{hashlib.sha256(content).hexdigest()}"
    assert lake.put_bytes(content) == key
    assert lake.read_key(key) == content
    assert (tmp_path / "bronze/sha256" / key.removeprefix("sha256:")).read_bytes() == content
    metadata = lake.read_metadata(key)
    assert set(metadata) == {"content_type", "url", "captured_at", "source_id", "step_id"}
    lake.write_key("gold/example/r1/suppliers.jsonl", b"{}\n")
    assert (tmp_path / "gold/example/r1/suppliers.jsonl").read_bytes() == b"{}\n"
    lake.write_key("runs/example/r1/trace.live.jsonl", b"{}\n")
    assert lake.read_key("runs/example/r1/trace.live.jsonl") == b"{}\n"


def test_lake_rejects_path_escape(tmp_path) -> None:
    lake = FileLake(tmp_path)
    with pytest.raises(ValueError):
        lake.write_key("gold/../outside", b"bad")
