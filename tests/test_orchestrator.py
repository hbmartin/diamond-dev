"""Tests for orchestrator helper behavior."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from diamond_dev.config import DiamondDevConfig
from diamond_dev.executor import CommandResult, CommandRunner
from diamond_dev.orchestrator import (
    DiamondDevOrchestrator,
    DirtyRecord,
    RunContext,
    SelectedImplementation,
    build_pr_body,
)


class _RecordingRunner:
    """Minimal command runner fake for orchestrator push tests."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.interactive_commands: list[tuple[str, ...]] = []

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        assert isinstance(check, bool)
        command_tuple = tuple(command)
        self.commands.append(command_tuple)
        output = ""
        if command_tuple[:3] == ("gh", "pr", "create"):
            output = "https://github.com/owner/repo/pull/123"
        return CommandResult(
            command=command_tuple,
            cwd=cwd,
            returncode=0,
            log_path=cwd / f"{log_name}.log",
            output=output,
        )

    def run_interactive(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        assert isinstance(check, bool)
        command_tuple = tuple(command)
        self.interactive_commands.append(command_tuple)
        return CommandResult(
            command=command_tuple,
            cwd=cwd,
            returncode=0,
            log_path=cwd / f"{log_name}.log",
            output="",
        )


def build_context(tmp_path: Path) -> RunContext:
    config = DiamondDevConfig(
        config_path=tmp_path / ".diamond-dev.toml",
        repository_url="git@github.com:owner/repo.git",
    )
    return RunContext(
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


def test_build_pr_body_includes_dirty_records(tmp_path: Path) -> None:
    context = build_context(tmp_path)
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


def test_commit_if_changes_skips_missing_untracked_paths(tmp_path: Path) -> None:
    runner = CommandRunner(tmp_path / "logs")
    runner.run(("git", "init"), cwd=tmp_path, log_name="git-init")
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)

    committed = orchestrator._commit_if_changes(  # noqa: SLF001
        tmp_path,
        message="cleanup",
        log_prefix="cleanup",
        paths=("missing.md",),
    )

    assert not committed


def test_finalize_pr_records_dirty_files_and_still_pushes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = build_context(tmp_path)
    selected_repo = tmp_path / "codex-my-plan"
    selected_repo.mkdir()
    selected = SelectedImplementation(
        accepted_agent="codex",
        opposite_agent="claude",
        repo_dir=selected_repo,
        branch="codex/my-plan",
    )
    runner = _RecordingRunner()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)

    def no_commit(
        _repo_dir: Path,
        *,
        message: str,
        log_prefix: str,
        paths: tuple[str, ...],
    ) -> bool:
        assert message
        assert log_prefix
        assert paths
        return False

    def dirty_files(_repo_dir: Path, *, log_name: str) -> tuple[str, ...]:
        assert log_name == "final selected branch-dirty-status"
        return ("src/dirty.py",)

    monkeypatch.setattr(orchestrator, "_commit_if_changes", no_commit)
    monkeypatch.setattr(orchestrator, "_dirty_files", dirty_files)

    orchestrator._finalize_pr(context, selected)  # noqa: SLF001

    assert context.dirty_records[0].files == ("src/dirty.py",)
    assert ("git", "push", "-u", "origin", "codex/my-plan") in runner.commands
    assert any(command[:3] == ("gh", "pr", "create") for command in runner.commands)
