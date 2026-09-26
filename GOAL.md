# Goal
Deliver C1 through C3 of the ontofill engine: a validated contract, sandboxed evidence capture, and a five-phase one-source run with gold export.

DONE: `uv run pytest -q && uv run ontofill run ../proveedor-abierto/case --run-id c3-check && uv run ontofill export ../proveedor-abierto/case --run-id c3-check`

Constraints: local Git only; no committed data or secrets; source discovery without hard-coded URLs; all captures in a sandbox; approvals use the contract protocol.
