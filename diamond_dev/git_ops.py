"""Git command helpers used by the Diamond Dev workflow."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from diamond_dev.errors import CommandFailureError, DiamondDevError
from diamond_dev.workflow import DirtyRecord

if TYPE_CHECKING:
    from diamond_dev.executor import CommandResult, CommandRunner
    from diamond_dev.workflow import RunContext


@dataclass(frozen=True, slots=True)
class BranchAheadBehind:
    """Ahead/behind counts for a branch compared with the current base branch."""

    ahead: int
    behind: int


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

    def sync_wiki(self, wiki_dir: Path) -> None:
        """Fetch and fast-forward the wiki repository."""
        self.run(wiki_dir, "fetch", "--prune", log_name="wiki-fetch")
        self.run(wiki_dir, "pull", "--ff-only", log_name="wiki-pull")

    def is_git_repo(self, repo_dir: Path, *, log_name: str) -> bool:
        """Return whether a directory is a Git repository."""
        result = self.run(
            repo_dir,
            "rev-parse",
            "--git-dir",
            log_name=log_name,
            check=False,
        )
        if result.returncode == 0:
            return True
        if result.returncode in {1, 128}:
            return False
        raise _git_failure(result, repo_dir)

    def fetch(self, repo_dir: Path, *, log_name: str) -> None:
        """Fetch and prune a repository's origin."""
        self.run(repo_dir, "fetch", "--prune", "origin", log_name=log_name)

    def origin_url(self, repo_dir: Path, *, log_name: str) -> str:
        """Return the configured origin URL."""
        result = self.run(repo_dir, "remote", "get-url", "origin", log_name=log_name)
        return result.output.strip()

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
        if self.remote_branch_exists(
            repo_dir,
            branch,
            log_name=f"branch-exists-{branch}",
        ):
            raise DiamondDevError(f"Expected remote branch already exists: {branch}")

    def remote_url_branch_exists(
        self,
        cwd: Path,
        *,
        remote_url: str,
        branch: str,
        log_name: str,
    ) -> bool:
        """Return whether a branch exists on a remote URL without cloning it."""
        result = self.runner.run(
            ("git", "ls-remote", "--exit-code", "--heads", remote_url, branch),
            cwd=cwd,
            log_name=log_name,
            check=False,
        )
        return _branch_exists_from_result(result, cwd)

    def remote_branch_exists(
        self,
        repo_dir: Path,
        branch: str,
        *,
        log_name: str,
    ) -> bool:
        """Return whether a branch exists on the repository origin."""
        result = self.run(
            repo_dir,
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            branch,
            log_name=log_name,
            check=False,
        )
        return _branch_exists_from_result(result, repo_dir)

    def local_branch_exists(
        self,
        repo_dir: Path,
        branch: str,
        *,
        log_name: str,
    ) -> bool:
        """Return whether a local branch exists."""
        result = self.run(
            repo_dir,
            "rev-parse",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
            log_name=log_name,
            check=False,
        )
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
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

    def checkout_existing_branch(
        self,
        repo_dir: Path,
        *,
        branch: str,
        log_prefix: str,
    ) -> None:
        """Checkout an existing local workflow branch."""
        self.run(
            repo_dir,
            "checkout",
            branch,
            log_name=f"{log_prefix}-checkout-existing",
        )

    def branch_ahead_behind(
        self,
        repo_dir: Path,
        *,
        branch: str,
        base_branch: str,
        log_name: str,
    ) -> BranchAheadBehind:
        """Return ahead and behind counts for a branch against origin/base."""
        result = self.run(
            repo_dir,
            "rev-list",
            "--left-right",
            "--count",
            f"origin/{base_branch}...{branch}",
            log_name=log_name,
        )
        lines = result.output.strip().splitlines()
        if not lines:
            raise DiamondDevError(
                f"No output returned for ahead/behind of {branch}",
            )
        counts = lines[-1].split()
        if len(counts) != 2:
            raise DiamondDevError(
                f"Unexpected ahead/behind output for {branch}: {result.output}",
            )
        behind, ahead = (int(counts[0]), int(counts[1]))
        return BranchAheadBehind(ahead=ahead, behind=behind)

    def branches_match_remote(
        self,
        repo_dir: Path,
        branch: str,
        *,
        log_prefix: str,
    ) -> bool:
        """Return whether local and origin point at the same commit."""
        local_revision = self.revision(
            repo_dir,
            branch,
            log_name=f"{log_prefix}-local-revision",
        )
        remote_revision = self.revision(
            repo_dir,
            f"origin/{branch}",
            log_name=f"{log_prefix}-remote-revision",
        )
        return local_revision == remote_revision

    def revision(self, repo_dir: Path, ref: str, *, log_name: str) -> str:
        """Return the commit revision for a ref."""
        result = self.run(repo_dir, "rev-parse", ref, log_name=log_name)
        revision = (
            result.output.strip().splitlines()[-1] if result.output.strip() else ""
        )
        if not revision:
            raise DiamondDevError(f"Could not resolve git revision for {ref}")
        return revision

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
        self.push_branch(repo_dir, branch, log_name=f"{label}-push")
        return updated_context

    def push_branch(self, repo_dir: Path, branch: str, *, log_name: str) -> None:
        """Push a branch to origin and set upstream."""
        self.run(repo_dir, "push", "-u", "origin", branch, log_name=log_name)

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


def _branch_exists_from_result(result: CommandResult, repo_dir: Path) -> bool:
    if result.returncode == 0:
        return True
    if result.returncode == 2:
        return False
    raise _git_failure(result, repo_dir)


def _git_failure(result: CommandResult, repo_dir: Path) -> CommandFailureError:
    return CommandFailureError(
        command=shlex.join(result.command),
        cwd=str(repo_dir),
        returncode=result.returncode,
        log_path=str(result.log_path),
    )
