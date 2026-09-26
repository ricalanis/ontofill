# inference

Vultr Serverless Inference is the primary typed decision backend. It discovers model IDs from `/v1/models`, forces one strict `emit` tool call for each JSON response, and validates the result locally. General typed decisions use `glm-5.3-flash`; PRD drafting and extraction use `qwen3.8-flash-next`; critique uses `minimax-m3` from another family. PRD drafting disables Qwen reasoning, expands local schema references, and limits response length because the live GLM Flash PRD call exhausted its token budget. A failed extraction or critique can retry on `glm-5.3`. Every attempt records response token usage and estimated cost; unknown prices stay null.

The recorded client is a test double. The `DecisionClient` protocol also allows an optional Jev supporting client for pre-filtering or a first-pass entity match; Vultr must confirm decisions that affect gold or checkpoints. The Jev adapter awaits the synced reference guide.
