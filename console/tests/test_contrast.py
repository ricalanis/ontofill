"""AA contrast for the Tira tokens, in both themes, computed from the OKLCH values in static/tokens.css."""

import math
import re
from pathlib import Path

import pytest

TOKENS = (Path(__file__).resolve().parents[1] / "ontofill_console" / "static" / "tokens.css").read_text()
VAR = re.compile(r"--([a-z0-9-]+):\s*oklch\(([\d.]+)%\s+([\d.]+)\s+([\d.]+)\)")


def _block(start: str) -> dict:
    i = TOKENS.index(start)
    j = TOKENS.index("}", i)
    return {m[0]: tuple(float(x) for x in m[1:]) for m in VAR.findall(TOKENS[i:j])}


DARK = _block(":root {")
LIGHT = {**DARK, **_block(':root[data-theme="light"]')}


def _luminance(lch) -> float:
    lightness, c, h = lch[0] / 100, lch[1], math.radians(lch[2])
    a, b = c * math.cos(h), c * math.sin(h)
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    rgb = (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )
    rgb = [min(1.0, max(0.0, x)) for x in rgb]  # linear sRGB, gamut-clipped
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def ratio(t: dict, fg: str, bg: str) -> float:
    a, b = sorted((_luminance(t[fg]), _luminance(t[bg])), reverse=True)
    return (a + 0.05) / (b + 0.05)


STATES = ("st-run", "st-need", "st-pause", "st-block", "st-quar", "st-done")


@pytest.mark.parametrize("theme", [DARK, LIGHT], ids=["dark", "light"])
def test_text_contrast(theme):
    for fg in ("fg", "fg-2", "fg-3"):
        for bg in ("bg", "panel", "strip"):
            assert ratio(theme, fg, bg) >= 4.5, (fg, bg, round(ratio(theme, fg, bg), 2))
    for st in STATES:  # state words and glyphs are text (chips, strip glyphs) on strips and panels
        for bg in ("panel", "strip", "bg"):
            assert ratio(theme, st, bg) >= 4.5, (st, bg, round(ratio(theme, st, bg), 2))
        assert ratio(theme, "on-state", st) >= 4.5, ("on-state", st, round(ratio(theme, "on-state", st), 2))


@pytest.mark.parametrize("theme", [DARK, LIGHT], ids=["dark", "light"])
def test_ui_edges_contrast(theme):
    assert ratio(theme, "line-strong", "strip") >= 3.0  # missing-cell dashes, focusable borders
    assert ratio(theme, "focus", "bg") >= 3.0
