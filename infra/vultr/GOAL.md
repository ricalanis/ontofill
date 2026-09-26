# C4c goal

Provision two idempotent Vultr VX1 hosts, a private VPC network, and an Object Storage bucket; bootstrap NetBird peers and dispatch sandbox Docker jobs to VM #2 over NetBird. Live provisioning is paused at one healthy control VM until Vultr clears the account admission gate.

DONE: `uv run pytest -q tests/test_vultr_plan.py tests/test_sandbox_remote.py && uv run ruff check infra/vultr src/ontofill/sandbox/capture.py sandbox/agent-pod/capture.py tests/test_vultr_plan.py tests/test_sandbox_remote.py && uv run ruff format --check infra/vultr src/ontofill/sandbox/capture.py sandbox/agent-pod/capture.py tests/test_vultr_plan.py tests/test_sandbox_remote.py`.

Constraints: dry-run reads no credentials and makes no network calls; apply requires explicit credentials; no secrets or instance data in Git; no real provisioning in this slice; public inbound firewall rules remain empty.
