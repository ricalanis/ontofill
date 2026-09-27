"""Bounded sandbox code testing and repair against immutable captures."""

from ontofill.repair.pattern_a import HtmlRepairResult, repair_html_extractor
from ontofill.repair.runner import (
    CaptureCase,
    DockerRepairExecutor,
    RepairFeedback,
    RepairOutcome,
    run_code_repair,
)

__all__ = [
    "CaptureCase",
    "DockerRepairExecutor",
    "HtmlRepairResult",
    "RepairFeedback",
    "RepairOutcome",
    "repair_html_extractor",
    "run_code_repair",
]
