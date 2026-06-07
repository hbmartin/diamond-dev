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
    copy_child_file,
    read_child_text,
    safe_child_path,
    selected_implementation,
    write_child_text,
)


def test_safe_child_path_returns_child_under_parent(tmp_path: Path) -> None:
    assert safe_child_path(tmp_path, "artifact.md") == tmp_path / "artifact.md"


@pytest.mark.parametrize(
    "child_name",
    [
        "",
        "../artifact.md",
        "artifact?.md",
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


def test_safe_child_path_rejects_symlink_escape(tmp_path: Path) -> None:
    parent_dir = tmp_path / "parent"
    outside_dir = tmp_path / "outside"
    parent_dir.mkdir()
    outside_dir.mkdir()
    (parent_dir / "escape").symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(DiamondDevError, match="escapes parent directory"):
        safe_child_path(parent_dir, "escape")


def test_child_text_helpers_round_trip_validated_child(tmp_path: Path) -> None:
    written_path = write_child_text(tmp_path, "artifact.md", "content\n")

    assert written_path == tmp_path / "artifact.md"
    assert read_child_text(tmp_path, "artifact.md") == "content\n"


@pytest.mark.parametrize(
    "child_name",
    [
        "",
        "../artifact.md",
        "nested/artifact.md",
    ],
)
def test_write_child_text_rejects_unsafe_names(
    tmp_path: Path,
    child_name: str,
) -> None:
    with pytest.raises(DiamondDevError, match="Unsafe child path"):
        write_child_text(tmp_path, child_name, "content\n")


def test_write_child_text_rejects_absolute_names(tmp_path: Path) -> None:
    with pytest.raises(DiamondDevError, match="Unsafe child path"):
        write_child_text(tmp_path, str(tmp_path / "artifact.md"), "content\n")


def test_write_child_text_rejects_symlink_escape(tmp_path: Path) -> None:
    parent_dir = tmp_path / "parent"
    outside_dir = tmp_path / "outside"
    parent_dir.mkdir()
    outside_dir.mkdir()
    (parent_dir / "escape").symlink_to(outside_dir / "artifact.md")

    with pytest.raises(DiamondDevError, match="escapes parent directory"):
        write_child_text(parent_dir, "escape", "content\n")


def test_copy_child_file_validates_source_and_destination(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    source_dir.mkdir()
    destination_dir.mkdir()
    write_child_text(source_dir, "artifact.md", "content\n")

    copied_path = copy_child_file(
        source_dir=source_dir,
        source_name="artifact.md",
        destination_dir=destination_dir,
        destination_name="artifact.md",
    )

    assert copied_path == destination_dir / "artifact.md"
    assert read_child_text(destination_dir, "artifact.md") == "content\n"


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
