"""Tests for CI workflow policy."""

from __future__ import annotations

import re
from pathlib import Path

ACTION_USE_PATTERN = re.compile(r"^\s*uses:\s+(?P<reference>[^#\s]+)")
FULL_SHA_REFERENCE_PATTERN = re.compile(r".+@[0-9a-f]{40}$")
REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"


def _workflow_action_references() -> list[tuple[Path, int, str]]:
    references: list[tuple[Path, int, str]] = []
    for workflow_path in sorted(WORKFLOW_DIR.glob("*.yml")):
        lines = workflow_path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, start=1):
            if match := ACTION_USE_PATTERN.match(line):
                references.append((workflow_path, line_number, match["reference"]))
    return references


def _is_remote_action_reference(reference: str) -> bool:
    return (
        "/" in reference
        and not reference.startswith(("./", "../"))
        and not reference.startswith("docker://")
    )


def test_github_actions_are_pinned_to_full_commit_shas() -> None:
    mutable_references = [
        f"{path.relative_to(REPO_ROOT)}:{line_number}: {reference}"
        for path, line_number, reference in _workflow_action_references()
        if _is_remote_action_reference(reference)
        and not FULL_SHA_REFERENCE_PATTERN.fullmatch(reference)
    ]

    assert mutable_references == []
