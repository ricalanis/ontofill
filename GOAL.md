# Goal
Deliver C4: provider-pluggable sandbox discovery with authority review, multi-source evidence and gap recovery, plus dry-run Vultr infrastructure plans.

DONE local: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests` plus a `mock-` real-brief run (expected exit 3) followed by `ontofill export --run-id mock-...` (exit 0); no latest pointer moves.

DONE live (pending credentials and approvals): `ontofill run ../proveedor-abierto/case` resumes through all three human checkpoints with Vultr inference, exports evidence-backed gold, and passes the app's DoD. Recorded output never counts toward this check.

DONE C4: `uv run pytest -q && uv run ruff check src tests packages infra && uv run ruff format --check src tests packages infra` plus `uv run pytest -q tests/test_discovery_providers.py tests/test_multisource_workflow.py tests/test_vultr_plan.py`; the multi-source check must show at least 3 distinct synthetic source types and a traced gap-triggered fan-out, while infra dry-run succeeds without credentials.

Constraints: local Git only; no committed data or secrets; source discovery without hard-coded source URLs; all captures in a sandbox; local bronze uses file:// and Vultr bronze uses S3; approvals use the contract protocol.
