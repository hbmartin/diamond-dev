"""Tests for workflow context assembly."""

from __future__ import annotations

from pathlib import Path

from diamond_dev.config import DiamondDevConfig
from diamond_dev.workflow import build_run_context


def test_build_run_context_uses_effective_wiki_url_for_directory(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "My Plan.md"
    config = DiamondDevConfig(
        config_path=tmp_path / ".diamond-dev.toml",
        repository_url="git@github.com:owner/repo.git",
        wiki_repository_url="git@github.com:owner/custom-notes.wiki.git",
    )

    context = build_run_context(
        cwd=tmp_path,
        plan_path=plan_path,
        config=config,
    )

    assert context.wiki.url == "git@github.com:owner/custom-notes.wiki.git"
    assert context.wiki.directory == tmp_path / "custom-notes.wiki"
    assert context.wiki.comparison_file == tmp_path / "custom-notes.wiki" / (
        "my-plan-comparison.md"
    )
    assert context.wiki.review_file == tmp_path / "custom-notes.wiki" / (
        "my-plan-review.md"
    )
