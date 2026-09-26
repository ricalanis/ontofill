# Goal
Deliver C1 through C3 of the ontofill engine: a validated contract, sandboxed evidence capture, and a five-phase one-source run with gold export.

DONE local: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests` plus a `mock-` real-brief run (expected exit 3) followed by `ontofill export --run-id mock-...` (exit 0); no latest pointer moves.

DONE live (pending credentials and approvals): `ontofill run ../proveedor-abierto/case` resumes through all three human checkpoints with Vultr inference, exports evidence-backed gold, and passes the app's DoD. Recorded output never counts toward this check.

Constraints: local Git only; no committed data or secrets; source discovery without hard-coded source URLs; all captures in a sandbox; local bronze uses file:// and Vultr bronze uses S3; approvals use the contract protocol.
