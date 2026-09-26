# sandbox

Control side of sandboxes: start/stop spider and agent pods on the sandbox host, burst throwaway Vultr instances via API, per-pod egress from the TDD's allowed domains.

`capture_url` and `fetch_url` accept optional `generated_by` provenance and
return a `proof` object plus five trace rows. The first row remains the capture
or fetch result; its `evaluated.proof_checkpoint` is `dispatch_result`. The
other checkpoints are `host_check`, `pod_identity`, `isolation_probe` and
`teardown`. The isolation row reports both the blocked proxy request and denied
writes outside the pod's writable mounts. Teardown is checked after the pod,
proxy and network are removed. Every row carries `generated_by` (recorded
sandbox test provenance by default).

The runtime defaults to Docker's configured runtime, recorded as `runc` on a
typical local installation. Set `ONTOFILL_SANDBOX_RUNTIME=runsc` on the Linux
sandbox host to dispatch the agent pod through gVisor; the job fails if that
runtime is unavailable. The local test exercises Docker containment, while
the `runsc` target needs a Linux host with gVisor installed.

After a capture or fetch returns, call `build_job_record(result, job_id=..., value_ids=...)`
to collapse its five checkpoints into a v0.4 `jobs.jsonl` record. The returned
record includes the captured start time, verified teardown time and provenance.
`append_job_record(lake, case_id, record)` validates and appends it at
`runs/<case_id>/<run_id>/jobs.jsonl`; the last record for each `job_id` wins.
Pass the same `job_id` when adding value IDs after refinement. The writer
serializes appends within one engine process.
