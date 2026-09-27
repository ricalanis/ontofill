# inference

Vultr Serverless Inference is the primary typed decision backend. It discovers model IDs from `/v1/models`, forces one strict `emit` tool call for each JSON response, and validates the result locally. General typed decisions use `glm-5.3-flash`; PRD drafting and extraction use `qwen3.8-flash-next`; critique uses `minimax-m3` from another family. PRD drafting disables Qwen reasoning, expands local schema references, and limits response length because the live GLM Flash PRD call exhausted its token budget. A failed extraction or critique can retry on `glm-5.3`. Every attempt records response token usage and estimated cost; unknown prices stay null.

The recorded client is a test double. The `DecisionClient` protocol also allows an optional Jev supporting client for pre-filtering or a first-pass entity match; Vultr must confirm decisions that affect gold or checkpoints. The Jev adapter awaits the synced reference guide.

The Vultr client accepts `run_id` and optional `step_id` in `from_env()` for the
initial `/models` request. If no step is supplied, that catalog request gets a
generated `step:<uuid>` bootstrap id. Wrap execution in
`inference_attribution(run_id, step_id)` to apply an existing trace step to
nested calls; omit `step_id` to generate and record a distinct trace id for each
`/chat/completions` request. Call log entries include only these ids and request
metadata; authorization tokens are never included.
