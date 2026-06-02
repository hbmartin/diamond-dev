"""Tests for orchestrator helper behavior."""

from __future__ import annotations

from pathlib import Path

from diamond_dev.config import DiamondDevConfig
from diamond_dev.orchestrator import (
    DirtyRecord,
    RunContext,
    SelectedImplementation,
    build_pr_body,
)


def test_build_pr_body_includes_dirty_records(tmp_path: Path) -> None:
    config = DiamondDevConfig(
        config_path=tmp_path / ".diamond-dev.toml",
        repository_url="git@github.com:owner/repo.git",
    )
    context = RunContext(
        cwd=tmp_path,
        config=config,
        plan_path=tmp_path / "My Plan.md",
        plan_slug="my-plan",
        notes_url="git@github.com:owner/repo.wiki.git",
        notes_dir=tmp_path / "repo.wiki",
        codex_dir=tmp_path / "codex-my-plan",
        claude_dir=tmp_path / "claude-my-plan",
        codex_branch="codex/my-plan",
        claude_branch="claude/my-plan",
        comparison_file=tmp_path / "comparison.md",
        notes_comparison_file=tmp_path / "repo.wiki" / "my-plan-comparison.md",
        notes_review_file=tmp_path / "repo.wiki" / "my-plan-review.md",
        base_branch="main",
    )
    context.dirty_records.append(
        DirtyRecord(
            label="codex initial",
            branch="codex/my-plan",
            files=("src/app.py", "README.md"),
        ),
    )
    selected = SelectedImplementation(
        accepted_agent="codex",
        opposite_agent="claude",
        repo_dir=tmp_path / "codex-my-plan",
        branch="codex/my-plan",
    )

    body = build_pr_body(context, selected)

    assert "Accepted implementation: codex" in body
    assert "Selected branch: codex/my-plan" in body
    assert "src/app.py, README.md" in body
