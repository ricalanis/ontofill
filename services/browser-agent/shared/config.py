"""Environment variable names. Values come only from the environment; nothing here is a secret.

Only the GATEWAY process may read VULTR_INFERENCE_API_KEY and JEV_API_KEY. The controller and every backend
(including Skyvern brains) talk to the gateway with a short-lived per-session token instead.
"""

from __future__ import annotations

import os

# Gateway-only secrets
VULTR_KEY_ENV = "VULTR_INFERENCE_API_KEY"
JEV_KEY_ENV = "JEV_API_KEY"
# Upstreams
VULTR_BASE_ENV = "VULTR_INFERENCE_BASE_URL"  # default https://api.vultrinference.com/v1
JEV_BASE_ENV = "JEV_BASE_URL"  # default https://api.typesafe.ai
# Gateway admin (controller -> gateway, to mint/revoke session tokens); not an upstream credential
GATEWAY_ADMIN_TOKEN_ENV = "BA_GATEWAY_ADMIN_TOKEN"
GATEWAY_URL_ENV = "BA_GATEWAY_URL"  # e.g. http://127.0.0.1:8700
GATEWAY_LOG_ENV = "BA_GATEWAY_LOG"  # JSONL call log path

DEFAULTS = {
    VULTR_BASE_ENV: "https://api.vultrinference.com/v1",
    JEV_BASE_ENV: "https://api.typesafe.ai",
    GATEWAY_URL_ENV: "http://127.0.0.1:8700",
}

# Models (Vultr catalog, docs/reference/vultr.md §2.3): verified ids; override per deployment.
PLANNER_MODEL = os.environ.get("BA_PLANNER_MODEL", "qwen3.8-flash-next")
VISION_MODEL = os.environ.get("BA_VISION_MODEL", "qwen3.8-27b")
SAFETY_MODEL = os.environ.get("BA_SAFETY_MODEL", "nemotron-3.5-content-safety")
JEV_MODEL = os.environ.get("BA_JEV_MODEL", "jev-latest")


def env(name: str) -> str | None:
    return os.environ.get(name) or DEFAULTS.get(name)
