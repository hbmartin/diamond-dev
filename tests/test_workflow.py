"""Tests for workflow context assembly."""

from __future__ import annotations

from pathlib import Path

import pytest

from diamond_dev import path_safety, workflow
from diamond_dev.config import (
    AgentConfig,
    AgentConfigs,
    DiamondDevConfig,
    WorkflowConfig,
)
from diamond_dev.errors import DiamondDevError
from diamond_dev.workflow import (
    build_run_context,
    resolve_plan_path,
    selected_implementation,
)


def test_workflow_reexports_path_safety_helpers() -> None:
    assert workflow.safe_child_path is path_safety.safe_child_path
    assert workflow.safe_generated_child_path is path_safety.safe_generated_child_path


def test_resolve_plan_path_rejects_unsafe_plan_filename(tmp_path: Path) -> None:
    plan_path = tmp_path / "-draft.md"
    plan_path.write_text("plan\n", encoding="utf-8")

    with pytest.raises(DiamondDevError, match="Unsafe child path"):
        resolve_plan_path(cwd=tmp_path, plan_path=plan_path)


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
