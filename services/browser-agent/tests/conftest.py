"""Markers registered here (pyproject stays untouched): `docker` tests start real local containers and only run
when selected explicitly with `-m docker`."""

from __future__ import annotations

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "docker: starts real local containers (cells docker stub); run with -m docker")


def pytest_collection_modifyitems(config, items):
    if "docker" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="needs Docker; run with -m docker")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip)
