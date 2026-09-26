# Sandbox capture and proof

## Browser cells

`CellManager.create("native", allowed_domains, limits, "sandbox_vm")` starts a
one-session gVisor Chromium hands pod and an allowlist egress proxy on a unique
internal Docker network. It returns a browser CDP WebSocket URL bound to
control-plane loopback. Remote Docker (`ONTOFILL_SANDBOX_DOCKER_HOST=ssh://...`)
uses an SSH loopback tunnel; no public CDP port is needed. `destroy(cell_id)`
removes the pod, proxy, and network, then verifies teardown. A timer kills cells
at `timeout_s`; `record_step` stops a cell at `max_steps`. The controller must
call `record_step` before each browser action and `report_task_result` when the
task completes. Direct CDP clients are not counted by the substrate.

`CellManager(lake=..., case_id=..., run_id=..., on_trace=...)` appends an honest
six-checkpoint `jobs.jsonl` row on teardown and emits proof trace steps as they
occur. A missing task result is recorded as failed, never inferred from an open
browser. The hands image has no key material or host mounts; its preflight
measures pod identity, blocked non-allowlisted access, read-only filesystem,
metadata/mesh isolation, and secret names without reading their values.

`serve_cells(manager, token=..., port=8766)` provides a loopback-only JSON API:
`POST /cells`, `GET /cells/{id}`, `DELETE /cells/{id}`,
`POST /cells/{id}/steps`, and `POST /cells/{id}/task-result`. All routes require
the configured bearer token. Keep this token on the control plane only.

For a Skyvern cell, pass `skyvern={...}` and `brain_env={...}` to `create`
or in `POST /cells`:

```json
{
  "image": "public.ecr.aws/skyvern/skyvern@sha256:<64 lowercase hex>",
  "postgres_image": "postgres:14-alpine@sha256:<64 lowercase hex>",
  "expires_at": "<ISO-8601 timestamp within one hour>"
}
```

```json
{
  "OPENAI_COMPATIBLE_API_BASE": "http://100.x.y.z:<port>",
  "OPENAI_COMPATIBLE_API_KEY": "<session-only token>"
}
```

`brain_env` accepts exactly those two keys. The gateway address must be a
NetBird IPv4 address with an explicit port. The
token must expire within an hour; the cell is destroyed by its expiry or its
wall-clock limit, whichever comes first. The gateway issues and enforces the
token's budget; this substrate validates its expiry and never writes its value
to the job feed, trace, logs, labels, or Docker CLI arguments. Docker stores
the session token in the brain's environment until teardown. The brain gets
`OPENAI_API_KEY` as that token and `OPENAI_API_BASE` pointing to a fixed relay;
its upstream profile is `OPENAI_GPT5_5`, with the gateway selecting the actual
Vultr model. No Vultr, Jev, NetBird, or AWS key is passed. The database uses per-cell tmpfs
and local trust authentication, so it has no persistent password.

Skyvern's brain network uses Docker's isolated gateway mode. It contains the
brain, database, CDP relay to its own hands, and gateway relay. The hands and
public-page proxy remain on a different internal network. The gateway relay
and the loopback API relay join a separate per-cell uplink bridge. No other
cell joins that uplink. Exact `DOCKER-USER` rules allow the gateway relay's
uplink IP to the configured NetBird gateway port and drop other new outbound
connections. Tagged `INPUT` rules also block new connections from both bridge
relays into the sandbox host itself.
The brain API is reached through a separate fixed relay published only on
Docker-host loopback and then an SSH loopback tunnel to the control plane.
Creation requires Docker Engine 28 or newer, a working remote sandbox SSH target,
host `sudo -n iptables`,
gateway reachability, Skyvern heartbeat, and brain network/credential probes;
any failure removes the cell and reports the failed proof.

`throwaway_vx1` placement still fails closed until Vultr admission is cleared.
`live_view_port` is `null` until a separate per-cell viewer is implemented.
The local Docker daemon lacks `runsc`, so current lifecycle tests use a
synthetic Docker driver. Live checks still need to confirm the pinned Skyvern
image starts with read-only tmpfs paths, the browser CDP connection works, the
gateway is reachable under the NetBird and host firewall policies, and the
controller can authenticate to the per-cell Skyvern API.

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
