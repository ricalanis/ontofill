"""USD per 1M tokens (input, output) for the Vultr models we use, from GET /v1/models as recorded in
docs/reference/vultr.md §2.3 (2026-09-26). Jev: $0.042 per 1M input tokens, output free (docs/reference/jev.md)."""

VULTR = {
    "qwen3.8-flash-next": (0.10, 0.20),
    "qwen3.8-27b": (0.15, 1.00),
    "glm-5.3": (0.75, 3.00),
    "glm-5.3-flash": (0.10, 0.35),
    "minimax-m3": (0.20, 0.90),
    "nemotron-3.5-content-safety": (0.05, 0.15),
    "nemotron-3-nano-omni-30b-a3b-reasoning": (0.10, 0.25),
    "deepseek-v4.1-flash": (0.15, 0.60),
}
JEV_INPUT_PER_M = 0.042


def vultr_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = VULTR.get(model, (0.75, 3.00))  # unknown model: priced as the most expensive we use
    return input_tokens / 1e6 * price_in + output_tokens / 1e6 * price_out


def jev_usd(input_tokens: int) -> float:
    return input_tokens / 1e6 * JEV_INPUT_PER_M
