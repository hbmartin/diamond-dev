"""Tests for workflow context assembly."""

from __future__ import annotations

from pathlib import Path

from diamond_dev.config import (
    AgentConfig,
    AgentConfigs,
    DiamondDevConfig,
    WorkflowConfig,
)
from diamond_dev.workflow import build_run_context, selected_implementation


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
    assert context.wiki.comparison_bundle_file == tmp_path / "custom-notes.wiki" / (
        "my-plan-comparison-bundle.md"
    )
    assert context.wiki.review_file == tmp_path / "custom-notes.wiki" / (
        "my-plan-review.md"
    )
    assert context.wiki.review_judgments_file == tmp_path / "custom-notes.wiki" / (
        "my-plan-review-judgments.json"
    )


def test_build_run_context_uses_configured_implementers(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "My Plan.md"
    config = DiamondDevConfig(
        config_path=tmp_path / ".diamond-dev.toml",
        repository_url="git@github.com:owner/repo.git",
        agents=AgentConfigs(
            by_name={
                "claude-fixer": AgentConfig(adapter="claude"),
            },
        ),
        workflow=WorkflowConfig(
            implementers=("codex", "claude", "claude-fixer"),
            comparison_fixer="claude-fixer",
        ),
    )

    context = build_run_context(
        cwd=tmp_path,
        plan_path=plan_path,
        config=config,
    )
    selected = selected_implementation(context, "codex")

    assert context.implementation.implementer_names == (
        "codex",
        "claude",
        "claude-fixer",
    )
    assert context.implementation.branch_for("claude-fixer").repo_dir == (
        tmp_path / "claude-fixer-my-plan"
    )
    assert context.implementation.branch_for("claude-fixer").branch == (
        "claude-fixer/my-plan"
    )
    assert selected.comparison_fixer == "claude-fixer"
