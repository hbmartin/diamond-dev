"""Tests for workflow context assembly."""

from __future__ import annotations

from pathlib import Path

import pytest

from diamond_dev.config import (
    AgentConfig,
    AgentConfigs,
    DiamondDevConfig,
    WorkflowConfig,
)
from diamond_dev.errors import DiamondDevError
from diamond_dev.workflow import (
    build_run_context,
    safe_child_path,
    selected_implementation,
)


def test_safe_child_path_returns_child_under_parent(tmp_path: Path) -> None:
    assert safe_child_path(tmp_path, "artifact.md") == tmp_path / "artifact.md"


@pytest.mark.parametrize(
    "child_name",
    [
        "",
        "../artifact.md",
        "nested/artifact.md",
    ],
)
def test_safe_child_path_rejects_unsafe_names(
    tmp_path: Path,
    child_name: str,
) -> None:
    with pytest.raises(DiamondDevError, match="Unsafe child path"):
        safe_child_path(tmp_path, child_name)


def test_safe_child_path_rejects_absolute_names(tmp_path: Path) -> None:
    with pytest.raises(DiamondDevError, match="Unsafe child path"):
        safe_child_path(tmp_path, str(tmp_path / "artifact.md"))


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
