"""Display filters for engine values (no routes): trace fields keep JSON's true/false through the shared `brief`
filter, and a gold value renders booleans as yes/no instead of Python's True/False."""

from __future__ import annotations

from .core import VizContext
from .health_common import jsonish

ORDER = -100  # before any view renders


def value_text(value) -> str:
    """A gold value as text: booleans read yes/no, a missing value is empty."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "" if value is None else str(value)


def install(ctx: VizContext) -> None:
    brief = ctx.env.filters.get("brief")
    if brief is not None and not getattr(brief, "jsonish", False):

        def brief_json(value, limit: int = 240) -> str:
            return brief(jsonish(value), limit)

        brief_json.jsonish = True
        ctx.env.filters["brief"] = brief_json
    ctx.env.filters["value_text"] = value_text
