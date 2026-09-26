"""Declared action and failure metadata for the hackathon tool surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .models import FailureKind


@dataclass(frozen=True)
class ToolContract:
    name: str
    risk: Literal["SAFE", "LOW"]
    rung: str
    idempotent: bool
    termination: str
    failures: tuple[FailureKind, ...]


def _contract(
    name: str, risk: Literal["SAFE", "LOW"], rung: str, termination: str, *failures: FailureKind
) -> ToolContract:
    return ToolContract(name, risk, rung, True, termination, failures)


TOOL_CONTRACTS = {
    tool.name: tool
    for tool in (
        _contract("page.snapshot", "SAFE", "B2", "captured page parsed", FailureKind.BLOCKED),
        _contract("page.query", "SAFE", "B2", "selector evaluated", FailureKind.STALE_BINDING),
        _contract(
            "page.forms", "SAFE", "B2", "forms classified; no submission", FailureKind.BLOCKED
        ),
        _contract("page.links", "SAFE", "B2", "links listed; no navigation", FailureKind.BLOCKED),
        _contract("page.pagination", "SAFE", "B2", "pagination links listed", FailureKind.BLOCKED),
        _contract(
            "file.fetch",
            "LOW",
            "B7",
            "bounded bytes returned",
            FailureKind.NETWORK,
            FailureKind.BLOCKED,
            FailureKind.RATE_LIMITED,
        ),
        _contract(
            "file.parse", "SAFE", "B7", "typed rows or text returned", FailureKind.VALIDATION_FAILED
        ),
        _contract(
            "source.discover",
            "LOW",
            "B7",
            "search result candidates returned",
            FailureKind.EMPTY_YIELD,
        ),
        _contract(
            "extract.selector", "SAFE", "B2", "nonempty values returned", FailureKind.EMPTY_YIELD
        ),
        _contract(
            "extract.llm",
            "LOW",
            "B2",
            "schema-constrained fields returned",
            FailureKind.VALIDATION_FAILED,
        ),
        _contract("entity.lookup", "SAFE", "B7", "one match or no match", FailureKind.CONFLICT),
        _contract("ontology.gaps", "SAFE", "B7", "missing fields listed"),
        _contract(
            "emit.observation",
            "LOW",
            "B7",
            "validated silver write succeeds",
            FailureKind.VALIDATION_FAILED,
        ),
        _contract("code.write", "LOW", "B5", "script in sandbox workspace", FailureKind.BLOCKED),
        _contract(
            "code.test",
            "LOW",
            "B5",
            "all replay pages run in sandbox",
            FailureKind.VALIDATION_FAILED,
        ),
        _contract(
            "code.promote",
            "LOW",
            "B5",
            "passing macro spec returned",
            FailureKind.VALIDATION_FAILED,
        ),
    )
}
