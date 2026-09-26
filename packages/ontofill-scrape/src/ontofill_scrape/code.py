"""Sandbox-only code artifacts and replay contracts; no host execution."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import FailureKind, ToolFailure


@dataclass(frozen=True)
class SandboxWorkspace:
    root: Path
    replay_root: Path

    def __post_init__(self) -> None:
        root = self.root.resolve()
        replay = self.replay_root.resolve()
        if not root.is_dir() or not replay.is_dir() or not replay.is_relative_to(root):
            raise ToolFailure(
                FailureKind.BLOCKED, "explicit sandbox/replay directories are required"
            )

    def inside(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise ToolFailure(FailureKind.BLOCKED, "path leaves sandbox workspace")
        return resolved


@dataclass(frozen=True)
class ReplayResult:
    passed: bool
    pages_tested: int
    detail: str = ""


class SandboxRunner(Protocol):
    """Runs untrusted code in a separate container with TDD egress restrictions."""

    def run(self, script: Path, replay_pages: Sequence[Path]) -> ReplayResult: ...


@dataclass(frozen=True)
class PromotedMacro:
    script_sha256: str
    script_name: str
    pages_tested: int
    replay_detail: str


def code_write(workspace: SandboxWorkspace, name: str, source: str) -> Path:
    if Path(name).name != name or not name.endswith(".py") or name.startswith("."):
        raise ToolFailure(FailureKind.BLOCKED, "script name must be a simple .py filename")
    if not source.strip():
        raise ToolFailure(FailureKind.VALIDATION_FAILED, "script source is empty")
    scripts = workspace.inside(workspace.root / "scripts")
    scripts.mkdir(exist_ok=True)
    path = workspace.inside(scripts / name)
    if path.is_symlink():
        raise ToolFailure(FailureKind.BLOCKED, "script symlinks are not allowed")
    path.write_text(source, encoding="utf-8")
    return path


def code_test(
    workspace: SandboxWorkspace,
    script: Path,
    replay_pages: Sequence[Path],
    runner: SandboxRunner,
) -> ReplayResult:
    script = workspace.inside(script)
    if not script.is_file() or script.suffix != ".py":
        raise ToolFailure(FailureKind.VALIDATION_FAILED, "sandbox script is missing")
    if not replay_pages:
        raise ToolFailure(FailureKind.VALIDATION_FAILED, "replay needs at least one captured page")
    pages = []
    replay_root = workspace.replay_root.resolve()
    for page in replay_pages:
        path = workspace.inside(page)
        if not path.is_relative_to(replay_root) or not path.is_file() or path.suffix != ".html":
            raise ToolFailure(
                FailureKind.BLOCKED, "replay input must be an HTML capture in replay_root"
            )
        pages.append(path)
    result = runner.run(script, tuple(pages))
    if result.pages_tested != len(pages):
        raise ToolFailure(FailureKind.VALIDATION_FAILED, "sandbox runner did not test every page")
    return result


def code_promote(workspace: SandboxWorkspace, script: Path, result: ReplayResult) -> PromotedMacro:
    script = workspace.inside(script)
    if not script.is_file() or not result.passed or result.pages_tested < 1:
        raise ToolFailure(
            FailureKind.VALIDATION_FAILED, "only replay-passing scripts can be promoted"
        )
    return PromotedMacro(
        hashlib.sha256(script.read_bytes()).hexdigest(),
        script.name,
        result.pages_tested,
        result.detail,
    )
