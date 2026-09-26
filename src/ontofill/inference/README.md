# inference

Vultr Serverless Inference is the primary typed decision backend. It discovers model IDs from `/v1/models`, forces one `emit` tool call for each JSON response, and validates the result locally. Generation uses a GLM model and critique uses a Qwen or DeepSeek model from another family.

The recorded client is a test double. The `DecisionClient` protocol also allows an optional Jev supporting client for pre-filtering or a first-pass entity match; Vultr must confirm decisions that affect gold or checkpoints. The Jev adapter awaits the synced reference guide.
