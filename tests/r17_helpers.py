"""Synthetic pod executor for R17 workflow tests; production uses DockerParseExecutor."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

from ontofill.sandbox import ParseExecution

_ROOT = Path(__file__).resolve().parents[1]
_RUNNER = _ROOT / "sandbox/parse-pod/runner.py"
_SPEC = importlib.util.spec_from_file_location("r17_test_parse_runner", _RUNNER)
assert _SPEC is not None and _SPEC.loader is not None
_PARSER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_PARSER)

_HOST = {"docker_host": "synthetic-test-host", "runtime": "runsc", "runtime_available": True}
_POD = {
    "hostname": "synthetic-parse-pod",
    "uname": {"system": "Linux", "release": "synthetic", "machine": "x86_64"},
    "cpu_virtualization_flags": [],
    "dev_kvm_present": False,
}
_ISOLATION = {
    "probes": [
        {"probe": "network_non_allowlisted", "blocked": True},
        {"probe": "write_outside_pod", "blocked": True},
        {"probe": "write_outside_writable_mount", "blocked": True},
        {"probe": "no_host_mounts", "blocked": True},
    ],
    "work_write_allowed": True,
}
_SECRETS = {
    "env_keys_found": 0,
    "files_with_keys": 0,
    "metadata_ip": "BLOCKED",
    "mesh": "BLOCKED",
    "ok": True,
}
_TEARDOWN = {"pod_gone": True, "proxy_gone": True, "network_removed": True, "verified": True}


class SyntheticParseExecutor:
    """Exercise caller wiring while the pod parser supplies deterministic outputs."""

    def run(self, payload, *, kind, format, max_rows, base_url, limits):
        if kind == "auto":
            kind = _PARSER._detect_document_format(payload)
        rows, text, page_text, links, skeleton, challenge = _PARSER._parse(
            payload, kind, max_rows, base_url
        )
        forms = _PARSER._parse_forms(payload) if kind == "html" else []
        table_headers = _PARSER._parse_table_headers(payload) if kind == "html" else []
        return ParseExecution(
            output={
                "ok": True,
                "kind": kind,
                "rows": rows,
                "text": text,
                "page_text": page_text,
                "links": links,
                "forms": forms,
                "table_headers": table_headers,
                "dom_skeleton_hash": skeleton,
                "challenge_detected": challenge,
                "truncated": False,
                "error": None,
            },
            host=_HOST,
            pod=_POD,
            isolation=_ISOLATION,
            secrets=_SECRETS,
            teardown=_TEARDOWN,
            peak_memory_mb=1.0,
            wall_s=0.01,
            steps=1,
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
        if len(payload) > max_bytes or hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise ValueError("synthetic pod rejected bronze input")
        return self.run(
            payload,
            kind=kind,
            format=format,
            max_rows=max_rows,
            base_url=base_url,
            limits=limits,
        )
