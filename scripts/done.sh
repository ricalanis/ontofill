#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  G3)
    uv run pytest -q tests/genericity tests/test_no_case_vocabulary.py
    ;;
  *)
    printf 'usage: %s G3\n' "$0" >&2
    exit 2
    ;;
esac
