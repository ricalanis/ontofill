# Sandbox capture and proof

`capture_url` and `fetch_url` dispatch disposable browser/file pods behind an
allowlist proxy. Each call accepts `limits=SandboxLimits(...)` or a complete
mapping with `memory_mb`, `cpus`, `pids`, `timeout_s`, and `max_steps`. Defaults
are 1024 MiB, 1 CPU, 256 PIDs, 90 seconds, and 32 actions. Docker enforces
memory, CPU and process caps; the controller enforces the deadline; the pod
counts browser/fetch actions. A limit stop raises `SandboxLimitExceeded` with
`reason` and trace rows including `event: limit_kill`, `evaluated.status:
hard_stop`, and a verified teardown checkpoint.

Successful results include the enforced `limits`, measured `usage`
(`peak_memory_mb`, `wall_s`, `steps`), and six trace checkpoints: task result,
host runtime, pod identity, isolation, secret hygiene, and teardown. The pod
probes its own environment and known credential-file locations by name only;
it does not read or log secret values. It also tests direct metadata-IP and
mesh connections. A successful secret checkpoint requires zero visible key
names/files and both network probes blocked. All model calls remain on the
control plane; no API key is passed to the pod.

`build_job_record(result, job_id=..., value_ids=...)` creates a validated
`jobs.jsonl` row containing all six checkpoints, limits and usage.
`append_job_record(lake, case_id, record)` appends it under
`runs/<case_id>/<run_id>/jobs.jsonl`; the last row for each `job_id` wins.
`SandboxLimitExceeded.result` builds an honest failed job row too: each
checkpoint that did not run is `{ok: false, not_run: true}` (or empty probes),
while teardown reports its actual result. A caller can append that row when
it catches the exception.

The runtime defaults to Docker's configured runtime on local development and
`runsc` for remote Docker. Set `ONTOFILL_SANDBOX_RUNTIME=runsc` to require
gVisor locally. The remote Docker target is
`ONTOFILL_SANDBOX_DOCKER_HOST=ssh://...`. The host's control-plane NetBird IP
can be set as `ONTOFILL_CONTROL_NETBIRD_IP` for the mesh probe.

Run the containment fixtures explicitly with
`ONTOFILL_RUN_CONTAINMENT=1 uv run pytest -q tests/test_sandbox.py tests/test_sandbox_limits.py`.
The hostile page exercises blocked metadata and other-host requests. The
extractor fixture attempts a destructive command and loops inside a separate,
read-only, unprivileged `runsc` container with no mounts or network; its test
skips when `runsc` is unavailable. Injection detection belongs to the
control-plane safety model, not to the capture pod.
