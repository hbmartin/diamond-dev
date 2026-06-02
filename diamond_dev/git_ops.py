"""Git command helpers used by the Diamond Dev workflow."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from diamond_dev.errors import CommandFailureError, DiamondDevError
from diamond_dev.workflow import DirtyRecord

if TYPE_CHECKING:
    from diamond_dev.executor import CommandResult, CommandRunner
    from diamond_dev.workflow import RunContext


class GitOperations:
    """Run workflow git commands through the shared command runner."""

    def __init__(self, runner: CommandRunner) -> None:
        """Create git operations backed by a command runner."""
        self.runner = runner

    def run(
        self,
        repo_dir: Path,
        *args: str,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        """Run a git command in a repository."""
        return self.runner.run(
            ("git", *args),
            cwd=repo_dir,
            log_name=log_name,
            check=check,
        )

    def sync_notes(self, notes_dir: Path) -> None:
        """Fetch and fast-forward the notes repository."""
        self.run(notes_dir, "fetch", "--prune", log_name="notes-fetch")
        self.run(notes_dir, "pull", "--ff-only", log_name="notes-pull")

    def remote_default_branch(self, repo_dir: Path) -> str:
        """Return the default branch advertised by origin."""
        result = self.run(
            repo_dir,
            "symbolic-ref",
            "--quiet",
            "--short",
            "refs/remotes/origin/HEAD",
            log_name="remote-default-branch",
        )
        lines = result.output.strip().splitlines()
        if not lines:
            raise DiamondDevError("No output returned from symbolic-ref command")

        remote_ref = lines[-1]
        if not remote_ref.startswith("origin/"):
            raise DiamondDevError(f"Unexpected remote HEAD ref: {remote_ref}")
        return remote_ref.removeprefix("origin/")

    def ensure_remote_branch_absent(self, repo_dir: Path, branch: str) -> None:
        """Raise if a workflow branch already exists on origin."""
        result = self.run(
            repo_dir,
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            branch,
            log_name=f"branch-exists-{branch}",
            check=False,
        )
        if result.returncode == 0:
            raise DiamondDevError(f"Expected remote branch already exists: {branch}")
        if result.returncode != 2:
            raise _git_failure(result, repo_dir)

    def checkout_branch(
        self,
        repo_dir: Path,
        *,
        branch: str,
        base_branch: str,
        log_prefix: str,
    ) -> None:
        """Create a local workflow branch from the remote base branch."""
        self.run(
            repo_dir,
            "checkout",
            "-b",
            branch,
            f"origin/{base_branch}",
            log_name=f"{log_prefix}-checkout",
        )

    def commit_if_changes(
        self,
        repo_dir: Path,
        *,
        message: str,
        log_prefix: str,
        paths: tuple[str, ...],
    ) -> bool:
        """Commit selected paths when any committable change exists."""
        committable_paths = self._committable_paths(repo_dir, paths, log_prefix)
        if not committable_paths:
            logger.info("No committable paths for {}", log_prefix)
            return False

        self.run(
            repo_dir,
            "add",
            "--all",
            "--",
            *committable_paths,
            log_name=f"{log_prefix}-add",
        )
        staged_diff = self.run(
            repo_dir,
            "diff",
            "--cached",
            "--quiet",
            "--exit-code",
            "--",
            *committable_paths,
            log_name=f"{log_prefix}-staged-diff",
            check=False,
        )
        if staged_diff.returncode == 0:
            logger.info("No changes to commit for {}", log_prefix)
            return False
        if staged_diff.returncode != 1:
            raise _git_failure(staged_diff, repo_dir)

        self.run(
            repo_dir,
            "commit",
            "-m",
            message,
            log_name=f"{log_prefix}-commit",
        )
        return True

    def push_agent_branch(
        self,
        context: RunContext,
        *,
        label: str,
        repo_dir: Path,
        branch: str,
    ) -> RunContext:
        """Record dirty files, then push a workflow branch."""
        updated_context = self.record_dirty_files(context, label, repo_dir, branch)
        self.run(
            repo_dir,
            "push",
            "-u",
            "origin",
            branch,
            log_name=f"{label}-push",
        )
        return updated_context

    def record_dirty_files(
        self,
        context: RunContext,
        label: str,
        repo_dir: Path,
        branch: str,
    ) -> RunContext:
        """Append dirty files observed after an agent phase."""
        dirty_files = self.dirty_files(repo_dir, log_name=f"{label}-dirty-status")
        if not dirty_files:
            return context

        dirty_record = DirtyRecord(label=label, branch=branch, files=dirty_files)
        updated_context = context.with_dirty_record(dirty_record)
        logger.warning(
            "Dirty files remain after {} and will not be pushed: {}",
            label,
            ", ".join(dirty_files),
        )
        return updated_context

    def dirty_files(self, repo_dir: Path, *, log_name: str) -> tuple[str, ...]:
        """Return path names from git status porcelain output."""
        result = self.run(repo_dir, "status", "--porcelain", log_name=log_name)
        return tuple(
            status_line[3:] if len(status_line) > 3 else status_line
            for status_line in result.output.splitlines()
            if status_line
        )

    def _committable_paths(
        self,
        repo_dir: Path,
        paths: tuple[str, ...],
        log_prefix: str,
    ) -> tuple[str, ...]:
        committable_paths: list[str] = []
        for path in paths:
            if (repo_dir / path).exists() or self._is_tracked(
                repo_dir,
                path,
                log_name=f"{log_prefix}-tracked-{path}",
            ):
                committable_paths.append(path)
                continue

            logger.info(
                "Skipping missing untracked path for {}: {}",
                log_prefix,
                path,
            )
        return tuple(committable_paths)

    def _is_tracked(self, repo_dir: Path, path: str, *, log_name: str) -> bool:
        result = self.run(
            repo_dir,
            "ls-files",
            "--error-unmatch",
            "--",
            path,
            log_name=log_name,
            check=False,
        )
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise _git_failure(result, repo_dir)


def _git_failure(result: CommandResult, repo_dir: Path) -> CommandFailureError:
    return CommandFailureError(
        command=shlex.join(result.command),
        cwd=str(repo_dir),
        returncode=result.returncode,
        log_path=str(result.log_path),
    )
