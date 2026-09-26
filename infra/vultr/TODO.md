# C4c scope

FILES: `infra/vultr/**`, `tests/test_vultr_plan.py`, `src/ontofill/sandbox/capture.py`, `sandbox/agent-pod/capture.py`, `tests/test_sandbox_remote.py`, `.env.example`.
TASK: Build a dry-run/apply Vultr plan, NetBird peer bootstrap and remote-safe sandbox output transfer.
DONE: `uv run pytest -q tests/test_vultr_plan.py tests/test_sandbox_remote.py`.
FORMAT: `uv run ruff check infra/vultr src/ontofill/sandbox/capture.py sandbox/agent-pod/capture.py tests/test_vultr_plan.py tests/test_sandbox_remote.py && uv run ruff format --check infra/vultr src/ontofill/sandbox/capture.py sandbox/agent-pod/capture.py tests/test_vultr_plan.py tests/test_sandbox_remote.py`.

- [x] Confirm current API/resource shapes from primary documentation.
- [x] Implement pure credential-free plan and idempotent apply.
- [x] Bootstrap NetBird with a short-lived key in cloud-init `write_files`, absent from shell arguments and the persistent script.
- [x] Transfer remote sandbox outputs with `docker cp`; preserve local behavior and proof.
- [x] Run DONE, dry-run, sandbox and full-suite checks; commit.
- [x] Resume sandbox VM and Object Storage only after Vultr clears the monthly-fee admission gate.
- [x] Verify runsc, NetBird policy finalization and S3 on live VM #2.
- [ ] Run the sandbox proof checkpoints and a Chromium/Playwright cell on live VM #2.
