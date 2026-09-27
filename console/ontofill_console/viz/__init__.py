"""Read-only visualization views of the engine (CONTRACT v1.0.4b; spec: architecture §7a).

Every module in this package that defines `install(ctx)` is discovered and installed in `ORDER` order. A module
registers its GET routes (an HTML page and a JSON view-model under `/api/viz/...`) and, if it is a case view, a
`View` for the case sub-nav. Nothing here writes: approvals, identity and deploy stay in `web.py`.
"""

from __future__ import annotations

import importlib
import pkgutil

from .core import GLOBAL_VIEWS, VIEWS, VizContext

__all__ = ["register", "VIEWS", "GLOBAL_VIEWS", "VizContext"]


def register(ctx: VizContext) -> None:
    modules = []
    for info in pkgutil.iter_modules(__path__):
        if info.name in ("core",):
            continue
        mod = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(mod, "install"):
            modules.append(mod)
    for mod in sorted(modules, key=lambda m: (getattr(m, "ORDER", 100), m.__name__)):
        mod.install(ctx)
    ctx.views.sort(key=lambda v: v.order)
    ctx.global_views.sort(key=lambda v: v.order)
    ctx.env.globals.update(case_views=ctx.views, global_views=ctx.global_views)
