"""Synthetic contract and transport tests for the isolated bronze parser."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ontofill.lake import FileLake
from ontofill.sandbox import (
    ParseExecution,
    SandboxLimits,
    SandboxParseError,
    parse_bronze,
    parse_bronze_json,
)
from ontofill.sandbox import parse as parse_module

_PARSE_RUNNER_PATH = Path(__file__).resolve().parents[1] / "sandbox/parse-pod/runner.py"

PROVENANCE = {
    "backend": "recorded",
    "model": "synthetic-parse-test",
    "at": "2026-09-26T12:00:00Z",
}
HOST = {
    "docker_host": "synthetic-sandbox",
    "runtime": "runsc",
    "runtime_available": True,
}
POD = {
    "hostname": "parse-pod-synthetic",
    "uname": {"system": "Linux", "release": "synthetic", "machine": "x86_64"},
    "cpu_virtualization_flags": [],
    "dev_kvm_present": False,
}
ISOLATION = {
    "probes": [
        {"probe": "external_network", "blocked": True, "target": "1.1.1.1:53"},
        {
            "probe": "write_outside_writable_mount",
            "blocked": True,
            "target": "/etc/.ontofill-write-probe",
        },
    ]
}
SECRETS = {
    "ok": True,
    "env_keys_found": 0,
    "files_with_keys": 0,
    "metadata_ip": "BLOCKED",
    "mesh": "BLOCKED",
}
TEARDOWN = {
    "pod_gone": True,
    "proxy_gone": True,
    "network_removed": True,
    "verified": True,
}


def _output(
    rows: list[dict] | None = None,
    *,
    error: dict | None = None,
    page_text: str = "",
    links: list[dict[str, str]] | None = None,
    forms: list[dict] | None = None,
    dom_skeleton_hash: str | None = None,
) -> dict:
    return {
        "ok": error is None,
        "rows": rows or [],
        "text": "",
        "page_text": page_text,
        "links": links or [],
        "forms": forms or [],
        "dom_skeleton_hash": dom_skeleton_hash,
        "truncated": False,
        "error": error,
        "proof": {"pod": POD, "isolation": ISOLATION, "secrets": SECRETS},
        "usage": {"peak_memory_mb": 24.0, "wall_s": 0.02, "steps": 1},
    }


class FakeExecutor:
    def __init__(self, output: dict | None = None, **overrides) -> None:
        self.output = output or _output(
            [{"sheet": None, "row_number": 1, "values": ["name", "value"]}]
        )
        self.overrides = overrides
        self.calls: list[dict] = []
        self.file_calls: list[dict] = []

    def run(self, payload, *, kind, format, max_rows, base_url, limits):
        self.calls.append(
            {
                "payload": payload,
                "kind": kind,
                "format": format,
                "max_rows": max_rows,
                "base_url": base_url,
                "limits": limits,
            }
        )
        return ParseExecution(
            output=self.output,
            host=self.overrides.get("host", HOST),
            pod=self.overrides.get("pod", POD),
            isolation=self.overrides.get("isolation", ISOLATION),
            secrets=self.overrides.get("secrets", SECRETS),
            teardown=self.overrides.get("teardown", TEARDOWN),
            peak_memory_mb=24.0,
            wall_s=0.02,
            steps=1,
            error=self.overrides.get("error"),
            limit_reason=self.overrides.get("limit_reason"),
        )

    def run_from_file(
        self,
        path,
        *,
        expected_sha256,
        max_bytes,
        kind,
        format,
        max_rows,
        base_url,
        limits,
    ):
        payload = path.read_bytes()
        if len(payload) > max_bytes:
            raise ValueError("input_too_large")
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise ValueError("bronze bytes do not match the requested key digest")
        self.file_calls.append(
            {"path": path, "expected_sha256": expected_sha256, "max_bytes": max_bytes}
        )
        return self.run(
            payload,
            kind=kind,
            format=format,
            max_rows=max_rows,
            base_url=base_url,
            limits=limits,
        )


def test_parse_bronze_transfers_opaque_bytes_and_builds_six_checkpoint_job(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    payload = b"synthetic captured bytes, not a real case"
    key = lake.put_bytes(payload, {"content_type": "text/csv"})
    executor = FakeExecutor(
        _output([{"sheet": None, "row_number": 1, "values": ["name", "Example 01"]}])
    )

    result = parse_bronze(
        lake,
        key,
        format="csv",
        max_rows=12,
        run_id="mock-parse-test",
        source_id="source:synthetic",
        step_id="step:synthetic-parse",
        generated_by=PROVENANCE,
        executor=executor,
    )

    assert executor.file_calls[0]["path"] == lake.bronze_path(key)
    assert executor.file_calls[0]["expected_sha256"] == key.removeprefix("sha256:")
    assert executor.calls[0]["kind"] == "csv"
    assert executor.calls[0]["max_rows"] == 12
    assert result.rows[0]["values"] == ["name", "Example 01"]
    assert result.as_parsed_file().rows[0].values == ("name", "Example 01")
    assert set(result.job_record["checkpoints"]) == {
        "host",
        "task",
        "where",
        "isolation",
        "secrets",
        "teardown",
    }
    assert result.job_record["checkpoints"]["host"]["runtime"] == "runsc"
    assert result.job_record["checkpoints"]["task"]["ok"] is True
    assert result.job_record["outcome"] == {"status": "completed"}
    assert all(
        item["result"] == "BLOCKED"
        for item in result.job_record["checkpoints"]["isolation"]["probes"]
    )
    assert result.job_record["checkpoints"]["secrets"]["ok"] is True
    assert result.job_record["checkpoints"]["teardown"]["ok"] is True
    assert len(result.trace) == 6


def test_parse_bronze_returns_profile_from_the_networkless_pod(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    key = lake.put_bytes(b"name,value\nExample,1\n", {"content_type": "text/csv"})
    profile = {"format": "csv", "table_count": 1, "tables": [{"headers": ["name", "value"]}]}
    output = _output([{"sheet": None, "row_number": 1, "values": ["name", "value"]}])
    output["profile"] = profile

    result = parse_bronze(lake, key, format="csv", executor=FakeExecutor(output))

    assert result.profile == profile


def test_parse_bronze_accepts_zip_profile_without_claiming_table_rows(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    key = lake.put_bytes(b"synthetic zip envelope", {"content_type": "application/zip"})
    profile = {"format": "zip", "table_count": 1, "tables": [{"headers": ["name"]}]}
    output = _output()
    output.update({"kind": "zip", "profile": profile})

    result = parse_bronze(lake, key, format="auto", executor=FakeExecutor(output))

    assert result.format == "zip"
    assert result.rows == ()
    assert result.profile == profile


def test_parse_auto_detects_extensionless_octet_stream_csv_in_pod(tmp_path: Path) -> None:
    from tests.r17_helpers import SyntheticParseExecutor

    lake = FileLake(tmp_path / "lake")
    payload = b"record_key,established_on\nrec-01,2001-04-03\n"
    key = lake.put_bytes(payload, {"content_type": "application/octet-stream"})

    result = parse_bronze(
        lake,
        key,
        format="auto",
        max_rows=12,
        run_id="mock-auto-parse",
        source_id="source:synthetic-download",
        generated_by=PROVENANCE,
        executor=SyntheticParseExecutor(),
    )

    assert result.format == "csv"
    assert result.rows[0]["values"] == ["record_key", "established_on"]
    assert result.rows[1]["values"] == ["rec-01", "2001-04-03"]


@pytest.mark.parametrize("requested_format", ["xls", "auto"])
def test_synthetic_biff_parses_in_pod(tmp_path: Path, requested_format: str) -> None:
    from tests.r17_helpers import SyntheticParseExecutor

    lake = FileLake(tmp_path / "lake")
    payload = (Path(__file__).parent / "fixtures" / "r40_synthetic.xls").read_bytes()
    key = lake.put_bytes(payload, {"content_type": "application/octet-stream"})

    result = parse_bronze(
        lake,
        key,
        format=requested_format,
        max_rows=12,
        run_id="mock-xls-parse",
        source_id="source:synthetic-xls-download",
        generated_by=PROVENANCE,
        executor=SyntheticParseExecutor(),
    )

    assert result.format == "xls"
    assert [row["sheet"] for row in result.rows] == ["Synthetic records"] * 2
    assert result.rows[0]["values"] == ["record_id", "amount", "as_of"]
    assert result.rows[1]["values"] == ["SYN-01", 12.5, "2001-04-03T00:00:00"]
    assert set(result.job_record["checkpoints"]) == {
        "host",
        "task",
        "where",
        "isolation",
        "secrets",
        "teardown",
    }
    assert len(result.trace) == 6


def test_parse_auto_keeps_ooxml_xlsx_distinct(tmp_path: Path) -> None:
    from openpyxl import Workbook

    from tests.r17_helpers import SyntheticParseExecutor

    workbook = Workbook()
    workbook.active.append(["record_id", "amount"])
    workbook.active.append(["SYN-XLSX-01", 4.5])
    output = io.BytesIO()
    workbook.save(output)

    lake = FileLake(tmp_path / "lake")
    key = lake.put_bytes(output.getvalue(), {"content_type": "application/octet-stream"})
    result = parse_bronze(
        lake,
        key,
        format="auto",
        executor=SyntheticParseExecutor(),
    )

    assert result.format == "xlsx"
    assert result.rows[1]["values"] == ["SYN-XLSX-01", 4.5]


def test_synthetic_biff_respects_row_limit_without_partial_rows() -> None:
    from tests.r17_helpers import _PARSER

    payload = (Path(__file__).parent / "fixtures" / "r40_synthetic.xls").read_bytes()

    with pytest.raises(_PARSER.ParseFailure, match="max_rows_exceeded"):
        _PARSER._parse(payload, "xls", 1, "")


def test_malformed_biff_diagnostic_is_bounded_and_recorded_without_payload_text(
    tmp_path: Path, monkeypatch
) -> None:
    from tests.r17_helpers import _PARSER

    secret_marker = b"AWS_SECRET_ACCESS_KEY=synthetic-leak API_TOKEN=synthetic-leak"
    payload = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + secret_marker
    monkeypatch.setattr(
        _PARSER,
        "_proof",
        lambda: {"pod": POD, "isolation": ISOLATION, "secrets": SECRETS},
    )

    class MalformedBiffExecutor(FakeExecutor):
        def run(self, payload_bytes, *, kind, format, max_rows, base_url, limits):
            input_path = tmp_path / "parse-input.json"
            output_path = tmp_path / "parse-output.json"
            input_path.write_text(
                json.dumps(
                    {
                        "kind": kind,
                        "max_rows": max_rows,
                        "base_url": base_url,
                        "payload": base64.b64encode(payload_bytes).decode("ascii"),
                    }
                ),
                encoding="utf-8",
            )
            _PARSER.run(input_path, output_path)
            self.output = json.loads(output_path.read_text(encoding="utf-8"))
            return super().run(
                payload_bytes,
                kind=kind,
                format=format,
                max_rows=max_rows,
                base_url=base_url,
                limits=limits,
            )

    lake = FileLake(tmp_path / "lake")
    key = lake.put_bytes(payload, {"content_type": "application/vnd.ms-excel"})

    with pytest.raises(SandboxParseError, match="invalid_xls") as raised:
        parse_bronze(
            lake,
            key,
            format="xls",
            run_id="mock-malformed-xls",
            source_id="source:synthetic-xls-download",
            generated_by=PROVENANCE,
            executor=MalformedBiffExecutor(),
        )

    record = raised.value.job_record
    task_result = record["checkpoints"]["task"]["result"]
    message = task_result["message"]
    assert task_result["reason"] == "invalid_xls"
    assert message.startswith("Legacy XLS workbook could not be opened by xlrd (")
    assert message.endswith(").")
    assert len(message) <= 160
    assert record["outcome"]["reason"] == f"invalid_xls: {message}"
    assert len(record["checkpoints"]) == 6
    assert raised.value.trace[0]["executed"]["message"] == message
    assert raised.value.trace[0]["evaluated"]["reason"] == message
    receipts = json.dumps({"job": record, "trace": raised.value.trace})
    assert secret_marker.decode("ascii") not in receipts
    assert "AWS_SECRET_ACCESS_KEY" not in receipts
    assert "API_TOKEN" not in receipts
    assert "synthetic-leak" not in receipts
    assert payload.hex() not in receipts
    assert (
        parse_module._safe_parse_diagnostic(
            {
                "error": {
                    "code": "invalid_xls",
                    "message": "Legacy XLS workbook could not be opened by xlrd (SecretToken).",
                }
            }
        )
        is None
    )


def test_file_lake_uses_docker_file_staging_without_python_byte_reads(tmp_path: Path, monkeypatch):
    lake = FileLake(tmp_path / "lake")
    payload = b"synthetic staged bytes"
    key = lake.put_bytes(payload)
    executor = FakeExecutor()
    observed = {}

    def run_from_file(path, **kwargs):
        observed["path"] = path
        observed.update(kwargs)
        return executor.run(
            b"",
            kind=kwargs["kind"],
            format=kwargs["format"],
            max_rows=kwargs["max_rows"],
            base_url=kwargs["base_url"],
            limits=kwargs["limits"],
        )

    executor.run_from_file = run_from_file
    monkeypatch.setattr(
        lake,
        "read_key",
        lambda _key: (_ for _ in ()).throw(AssertionError("application read bronze bytes")),
    )

    result = parse_bronze(lake, key, format="csv", executor=executor)

    assert observed["path"] == lake.bronze_path(key)
    assert observed["expected_sha256"] == key.removeprefix("sha256:")
    assert result.job_record["checkpoints"]["host"]["runtime"] == "runsc"


def test_docker_executor_stages_local_bronze_via_cli_for_remote_daemon(monkeypatch, tmp_path: Path):
    executor = parse_module.DockerParseExecutor()
    bronze = tmp_path / "bronze.csv"
    bronze.write_bytes(b"synthetic transfer content")
    calls: list[tuple[tuple[str, ...], dict]] = []
    output = json.dumps(_output()).encode()

    def fake_docker(*args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ("image", "inspect"):
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ("info", "--format"):
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"Name": "synthetic-remote", "Runtimes": {"runsc": {}}}), ""
            )
        if args and args[0] == "exec" and "wc" in args:
            return subprocess.CompletedProcess(args, 0, f"{len(output)} /work/output.json\n", "")
        if args and args[0] == "exec" and "cat" in args and "/work/output.json" in args:
            return subprocess.CompletedProcess(args, 0, output.decode(), "")
        if args and args[0] == "inspect":
            return subprocess.CompletedProcess(args, 1, "", "No such container")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(parse_module, "_docker", fake_docker)
    monkeypatch.setattr(executor, "_image", lambda: "synthetic-parse-image")

    result = executor.run_from_file(
        bronze,
        expected_sha256=hashlib.sha256(bronze.read_bytes()).hexdigest(),
        max_bytes=1024,
        kind="csv",
        format="csv",
        max_rows=10,
        base_url="",
        limits=SandboxLimits(memory_mb=256, pids=32, timeout_s=5, max_steps=1),
    )

    run_args = next(args for args, _kwargs in calls if args and args[0] == "run")
    copy_args = next(args for args, _kwargs in calls if args and args[0] == "cp")
    assert "--runtime" in run_args and run_args[run_args.index("--runtime") + 1] == "runsc"
    assert "--network" in run_args and run_args[run_args.index("--network") + 1] == "none"
    assert "--mount" not in run_args and "-v" not in run_args
    assert copy_args[1] == str(bronze.resolve())
    assert copy_args[2].endswith(":/work/bronze")
    staged = next(kwargs["input_text"] for args, kwargs in calls if "input_text" in kwargs)
    assert json.loads(staged)["bronze_path"] == "/work/bronze"
    assert "synthetic transfer content" not in staged
    assert result.teardown["verified"] is True


def test_parse_bronze_json_returns_pod_decoded_mapping(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    payload = b'{"success":true,"result":{"results":[{"id":"synthetic-01"}]}}'
    key = lake.put_bytes(payload, {"content_type": "application/json"})
    expected = {"success": True, "result": {"results": [{"id": "synthetic-01"}]}}
    executor = FakeExecutor(_output([{"document": expected}]))

    result = parse_bronze_json(
        lake,
        key,
        run_id="mock-ckan-lead",
        source_id="source:synthetic-catalog",
        step_id="step:synthetic-json",
        generated_by=PROVENANCE,
        executor=executor,
    )

    assert executor.file_calls[0]["path"] == lake.bronze_path(key)
    assert executor.calls[0]["kind"] == "json_document"
    assert result.document == expected
    assert result.job_record["checkpoints"]["task"]["ok"]


def test_html_result_returns_pod_tables_links_text_and_skeleton(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    key = lake.put_bytes(
        b"synthetic HTML capture",
        {
            "content_type": "text/html",
            "url": "https://synthetic.example.test/catalog/list?page=1&token=removed",
        },
    )
    executor = FakeExecutor(
        _output(
            [{"sheet": "html-table-1", "row_number": 1, "values": ["Name", "Example 01"]}],
            page_text="Example 01 public data",
            links=[
                {
                    "url": "https://synthetic.example.test/catalog/item/1",
                    "text": "Example 01",
                    "rel": "next",
                }
            ],
            forms=[
                {
                    "label": "Search public records",
                    "role": "search",
                    "search_like": True,
                    "fields": [
                        {
                            "type": "search",
                            "label": "Record identifier",
                            "placeholder": "Enter a record identifier",
                            "name": "identifier",
                        }
                    ],
                    "submit_labels": ["Search"],
                }
            ],
            dom_skeleton_hash="a" * 64,
        )
    )

    result = parse_bronze(lake, key, format="html", executor=executor)

    assert executor.calls[0]["base_url"] == "https://synthetic.example.test/catalog/list"
    assert result.as_parsed_file().rows[0].values == ("Name", "Example 01")
    assert result.links[0]["url"].endswith("/catalog/item/1")
    assert result.page_text == "Example 01 public data"
    assert result.forms[0]["fields"][0]["label"] == "Record identifier"
    assert result.dom_skeleton_hash == "a" * 64


@pytest.mark.parametrize(
    ("key", "expected"),
    [("../not-a-key", "bronze_key must be"), ("sha256:" + "0" * 64, "do not match")],
)
def test_bronze_key_and_digest_are_checked_before_dispatch(
    tmp_path: Path, monkeypatch, key: str, expected: str
) -> None:
    lake = FileLake(tmp_path / "lake")
    valid_key = lake.put_bytes(b"synthetic bytes")
    executor = FakeExecutor()
    if key.startswith("sha256:"):
        key = valid_key
        lake.bronze_path(key).write_bytes(b"different synthetic bytes")

    with pytest.raises(ValueError, match=expected):
        parse_bronze(lake, key, format="csv", executor=executor)
    assert executor.calls == []


def test_input_limit_fails_closed_with_job_record(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    key = lake.put_bytes(b"synthetic input over the caller limit")
    executor = FakeExecutor()

    with pytest.raises(SandboxParseError, match="input_too_large") as raised:
        parse_bronze(lake, key, format="csv", max_bytes=8, executor=executor)

    assert executor.calls == []
    record = raised.value.job_record
    assert record["checkpoints"]["task"]["ok"] is False
    assert record["checkpoints"]["task"]["result"]["reason"] == "input_too_large"
    assert record["checkpoints"]["host"] == {"ok": False, "not_run": True}
    assert record["checkpoints"]["teardown"]["ok"] is True
    assert len(raised.value.trace) == 6


def test_parse_failure_returns_no_partial_rows_and_failed_job(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    key = lake.put_bytes(b"synthetic csv rows")
    executor = FakeExecutor(_output(error={"code": "max_rows_exceeded"}), error="max_rows_exceeded")

    with pytest.raises(SandboxParseError, match="max_rows_exceeded") as raised:
        parse_bronze(lake, key, format="csv", max_rows=1, executor=executor)

    assert raised.value.job_record["checkpoints"]["task"]["ok"] is False
    assert raised.value.job_record["checkpoints"]["task"]["result"]["row_count"] == 0
    assert raised.value.job_record["checkpoints"]["task"]["result"]["reason"] == (
        "max_rows_exceeded"
    )


def test_max_links_parse_failure_records_sanitized_source_url(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    key = lake.put_bytes(
        b"synthetic HTML bytes",
        {
            "content_type": "text/html",
            "url": "https://synthetic.example.test/catalog/list?page=2",
        },
    )
    executor = FakeExecutor(
        _output(error={"code": "max_links_exceeded"}),
        error="max_links_exceeded",
    )

    with pytest.raises(SandboxParseError, match="max_links_exceeded") as raised:
        parse_bronze(lake, key, format="html", executor=executor)

    expected_url = "https://synthetic.example.test/catalog/list"
    task_request = raised.value.job_record["checkpoints"]["task"]["requested"]
    assert task_request["url"] == expected_url
    assert raised.value.trace[0]["requested"]["url"] == expected_url
    assert raised.value.job_record["checkpoints"]["task"]["result"]["reason"] == (
        "max_links_exceeded"
    )


def test_invalid_html_metadata_does_not_return_partial_rows(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    key = lake.put_bytes(b"synthetic HTML bytes")
    executor = FakeExecutor(
        _output(
            [{"sheet": "html-table-1", "row_number": 1, "values": ["Example 01"]}],
            page_text={"invalid": "metadata"},
        )
    )

    with pytest.raises(SandboxParseError, match="invalid_html_metadata") as raised:
        parse_bronze(lake, key, format="html", executor=executor)

    task_result = raised.value.job_record["checkpoints"]["task"]["result"]
    assert task_result["row_count"] == 0
    assert task_result["page_text_chars"] == 0


def test_parse_runner_extracts_html_and_decodes_json_in_worker_code() -> None:
    spec = importlib.util.spec_from_file_location("ontofill_parse_pod_runner", _PARSE_RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    html = (
        b"<html><body><script>synthetic hidden text</script><h1>Example 01</h1>"
        b"<table><tr><th>Name</th></tr><tr><td>Example 01</td></tr></table>"
        b"<a href='item/1' rel='next'>Open record</a>"
        b"<a href='https://user:password@synthetic.example.test/private'>Drop</a>"
        b"</body></html>"
    )
    rows, text, page_text, links, skeleton, challenge = runner._parse(
        html,
        "html",
        10,
        "https://synthetic.example.test/catalog/list?token=discarded",
    )
    document = b'{"success":true,"result":{"id":"synthetic-01"}}'
    json_rows, _, _, _, _, _ = runner._parse(document, "json_document", 1, "")

    assert text == ""
    assert rows[0]["values"] == ["Name"]
    assert rows[1]["values"] == ["Example 01"]
    assert page_text == "Example 01 Name Example 01 Open record Drop"
    assert links == [
        {
            "url": "https://synthetic.example.test/catalog/item/1",
            "text": "Open record",
            "rel": "next",
            "title": "Open record",
            "context": "Open record",
            "result_kind": "other",
        }
    ]
    assert challenge is False
    assert len(skeleton) == 64
    assert json_rows == [{"document": {"success": True, "result": {"id": "synthetic-01"}}}]


def test_parse_runner_extracts_search_form_labels_without_form_values() -> None:
    spec = importlib.util.spec_from_file_location("ontofill_parse_pod_runner", _PARSE_RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    html = (
        b"<form role='search' aria-label='Search public records'>"
        b"<label for='record'>Record identifier</label>"
        b"<input id='record' type='search' name='identifier' "
        b"placeholder='Enter a record identifier' value='sample-value'>"
        b"<input type='password' name='password' value='never-copy'>"
        b"<button type='submit'>Search</button></form>"
    )

    forms = runner._parse_forms(html)

    assert forms == [
        {
            "label": "Search public records",
            "role": "search",
            "search_like": True,
            "fields": [
                {
                    "type": "search",
                    "label": "Record identifier",
                    "placeholder": "Enter a record identifier",
                    "name": "identifier",
                }
            ],
            "submit_labels": ["Search"],
        }
    ]
    assert "sample-value" not in json.dumps(forms)
    assert "never-copy" not in json.dumps(forms)


def test_parse_runner_rejects_row_limit_instead_of_returning_partial_rows() -> None:
    spec = importlib.util.spec_from_file_location("ontofill_parse_pod_runner", _PARSE_RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    with pytest.raises(runner.ParseFailure, match="max_rows_exceeded"):
        runner._parse(b"name\nExample 01\nExample 02\n", "csv", 2, "")


def test_unverified_secret_checkpoint_fails_closed(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    key = lake.put_bytes(b"synthetic csv bytes")
    secrets = {**SECRETS, "mesh": "ALLOWED", "ok": False}

    with pytest.raises(SandboxParseError, match="proof did not pass") as raised:
        parse_bronze(lake, key, format="csv", executor=FakeExecutor(secrets=secrets))

    assert raised.value.job_record["checkpoints"]["task"]["ok"] is True
    assert raised.value.job_record["checkpoints"]["secrets"]["ok"] is False


def test_docker_executor_uses_runsc_network_none_and_opaque_stdin(monkeypatch) -> None:
    executor = parse_module.DockerParseExecutor()
    calls: list[tuple[tuple[str, ...], dict]] = []
    output = json.dumps(_output()).encode()

    def fake_docker(*args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ("image", "inspect"):
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ("info", "--format"):
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"Name": "synthetic", "Runtimes": {"runsc": {}}}), ""
            )
        if args[:2] == ("exec", "wc") or (args and args[0] == "exec" and "wc" in args):
            return subprocess.CompletedProcess(args, 0, f"{len(output)} /work/output.json\n", "")
        if args and args[0] == "exec" and "cat" in args and "/work/output.json" in args:
            return subprocess.CompletedProcess(args, 0, output.decode(), "")
        if args and args[0] == "inspect":
            return subprocess.CompletedProcess(args, 1, "", "No such container")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(parse_module, "_docker", fake_docker)
    monkeypatch.setattr(executor, "_image", lambda: "synthetic-parse-image")

    result = executor.run(
        b"synthetic bytes",
        kind="csv",
        format="csv",
        max_rows=10,
        base_url="https://synthetic.example.test/data.csv",
        limits=SandboxLimits(memory_mb=256, pids=32, timeout_s=5, max_steps=1),
    )

    run_args = next(args for args, _kwargs in calls if args and args[0] == "run")
    assert "--runtime" in run_args and run_args[run_args.index("--runtime") + 1] == "runsc"
    assert "--network" in run_args and run_args[run_args.index("--network") + 1] == "none"
    assert "--read-only" in run_args
    assert "--memory" in run_args and "256m" in run_args
    assert "-e" not in run_args and "-v" not in run_args and "--mount" not in run_args
    staged = next(kwargs["input_text"] for args, kwargs in calls if "input_text" in kwargs)
    envelope = json.loads(staged)
    assert base64.b64decode(envelope["payload"]) == b"synthetic bytes"
    assert "AWS_SECRET_ACCESS_KEY" not in staged
    assert result.teardown["verified"] is True


def test_docker_executor_cleans_up_after_runtime_start_timeout(monkeypatch) -> None:
    executor = parse_module.DockerParseExecutor()
    calls: list[tuple[str, ...]] = []

    def fake_docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("info", "--format"):
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"Name": "synthetic", "Runtimes": {"runsc": {}}}), ""
            )
        if args and args[0] == "run":
            raise parse_module.DockerTimeout("synthetic run timeout")
        if args and args[0] == "inspect":
            return subprocess.CompletedProcess(args, 1, "", "No such container")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(parse_module, "_docker", fake_docker)
    monkeypatch.setattr(executor, "_image", lambda: "synthetic-parse-image")

    result = executor.run(
        b"synthetic bytes",
        kind="csv",
        format="csv",
        max_rows=10,
        base_url="",
        limits=SandboxLimits(memory_mb=256, pids=32, timeout_s=5, max_steps=1),
    )

    assert result.limit_reason == "timeout"
    assert result.error == "pod exceeded wall-clock limit"
    assert any(args[:2] == ("rm", "-f") for args in calls)
    assert result.teardown["verified"] is True


@pytest.mark.parametrize(
    ("requested_format", "payload_kind"),
    [
        ("csv", "csv"),
        ("xls", "xls"),
    ],
    ids=["csv", "xls"],
)
def test_default_parser_refuses_when_runsc_is_unavailable(
    monkeypatch, tmp_path: Path, requested_format: str, payload_kind: str
) -> None:
    lake = FileLake(tmp_path / "lake")
    payload = (
        b"name,value\nExample 01,42\n"
        if payload_kind == "csv"
        else (Path(__file__).parent / "fixtures" / "r40_synthetic.xls").read_bytes()
    )
    key = lake.put_bytes(payload)
    calls: list[tuple[str, ...]] = []

    def no_runsc(*args, **_kwargs):
        calls.append(args)
        if args[:2] == ("info", "--format"):
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"Name": "synthetic-no-runsc", "Runtimes": {"runc": {}}}), ""
            )
        raise AssertionError("default parser must refuse before creating a container")

    monkeypatch.setattr(parse_module, "_docker", no_runsc)
    with pytest.raises(SandboxParseError) as raised:
        parse_bronze(
            lake,
            key,
            format=requested_format,
            run_id="mock-no-runsc",
            source_id="source:synthetic",
            generated_by=PROVENANCE,
        )

    assert raised.value.reason == "runtime_unavailable"
    assert raised.value.job_record["checkpoints"]["host"]["ok"] is False
    assert calls == [("info", "--format", "{{json .}}")]


def test_parser_docker_info_uses_configured_sandbox_host(monkeypatch) -> None:
    monkeypatch.setenv("ONTOFILL_SANDBOX_DOCKER_HOST", "ssh://root@100.64.0.2")
    observed = []

    def fake_run(argv, **kwargs):
        observed.append((argv, kwargs["env"].get("DOCKER_HOST")))
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"Name": "synthetic", "Runtimes": {"runc": {}}}), ""
        )

    monkeypatch.setattr(parse_module.subprocess, "run", fake_run)
    result = parse_module.DockerParseExecutor().run(
        b"synthetic bytes",
        kind="csv",
        format="csv",
        max_rows=10,
        base_url="",
        limits=SandboxLimits(memory_mb=256, pids=32, timeout_s=5, max_steps=1),
    )

    assert result.limit_reason == "runtime_unavailable"
    assert observed == [(["docker", "info", "--format", "{{json .}}"], "ssh://root@100.64.0.2")]


@pytest.mark.skipif(
    os.environ.get("ONTOFILL_RUN_PARSE_CONTAINMENT") != "1" or shutil.which("docker") is None,
    reason="set ONTOFILL_RUN_PARSE_CONTAINMENT=1 to run the real runsc parser proof",
)
def test_recorded_csv_parses_in_real_runsc_pod(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    key = lake.put_bytes(b"name,value\nExample 01,42\n")
    result = parse_bronze(
        lake,
        key,
        format="csv",
        max_rows=10,
        run_id="mock-parse-runsc",
        source_id="source:synthetic",
        generated_by=PROVENANCE,
    )
    assert result.rows == (
        {"sheet": None, "row_number": 1, "values": ["name", "value"]},
        {"sheet": None, "row_number": 2, "values": ["Example 01", "42"]},
    )
    assert result.job_record["checkpoints"]["host"]["runtime"] == "runsc"
    assert set(result.job_record["checkpoints"]) == {
        "host",
        "task",
        "where",
        "isolation",
        "secrets",
        "teardown",
    }
    assert len(result.trace) == 6
    assert result.job_record["checkpoints"]["task"]["ok"] is True
    assert result.job_record["checkpoints"]["teardown"]["ok"] is True


@pytest.mark.skipif(
    os.environ.get("ONTOFILL_RUN_PARSE_CONTAINMENT") != "1" or shutil.which("docker") is None,
    reason="set ONTOFILL_RUN_PARSE_CONTAINMENT=1 to run the real runsc parser proof",
)
def test_recorded_html_returns_links_text_and_skeleton_from_runsc(tmp_path: Path) -> None:
    lake = FileLake(tmp_path / "lake")
    html = (
        b"<html><body><script>hidden payload</script><h1>Example 01</h1>"
        b"<table><tr><th>Name</th></tr><tr><td>Example 01</td></tr></table>"
        b"<a href='item/1' rel='next'>Open record</a></body></html>"
    )
    key = lake.put_bytes(
        html,
        {
            "content_type": "text/html",
            "url": "https://synthetic.example.test/catalog/list?token=discarded",
        },
    )

    result = parse_bronze(lake, key, format="html", max_rows=20)

    assert result.rows[0]["values"] == ["Name"]
    assert result.page_text == "Example 01 Name Example 01 Open record"
    assert "hidden payload" not in result.page_text
    assert result.links == (
        {
            "url": "https://synthetic.example.test/catalog/item/1",
            "text": "Open record",
            "rel": "next",
            "title": "Open record",
            "context": "Open record",
            "result_kind": "other",
        },
    )
    assert result.dom_skeleton_hash and len(result.dom_skeleton_hash) == 64
