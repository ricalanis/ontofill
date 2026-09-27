"""Test path setup: the parse-pod module lives outside the installed package."""

from __future__ import annotations

import sys
from pathlib import Path

_POD = Path(__file__).resolve().parents[1] / "sandbox/parse-pod"
if str(_POD) not in sys.path:
    sys.path.insert(0, str(_POD))
