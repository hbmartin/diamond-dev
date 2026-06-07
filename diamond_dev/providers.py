"""Thin workflow provider boundaries for GitHub and review operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from diamond_dev import pr
from diamond_dev.commands import (
    build_gh_pr_create_command,
    build_gh_pr_edit_body_command,
    build_gh_pr_list_command,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from diamond_dev.executor import CommandExecutor, CommandResult, FileCommandExecutor
    from diamond_dev.git_ops import GitHubGitOperations
    from diamond_dev.pr import ExistingPullRequest
    from diamond_dev.workflow import SelectedImplementation


@dataclass(frozen=True, slots=True)
class CreatedPullRequest:
    """Metadata for a newly created pull request."""

    url: str
    number: str


class GitHubWorkflowProvider:
    """Current GitHub workflow operations backed by git and gh commands."""

    def __init__(
        self,
        *,
        runner: CommandExecutor,
        git: GitHubGitOperations,
    ) -> None:
        """Create a provider backed by existing command helpers."""
        self.runner = runner
        self.git = git

    def sync_wiki(self, wiki_dir: Path) -> None:
        """Fetch and fast-forward the wiki repository."""
        self.git.sync_wiki(wiki_dir)

    def push_wiki(self, wiki_dir: Path, *, log_name: str) -> None:
        """Push the wiki repository."""
        self.git.run(wiki_dir, "push", log_name=log_name)

    def remote_workflow_branch_exists(
        self,
        cwd: Path,
        *,
        remote_url: str,
        branch: str,
        log_name: str,
    ) -> bool:
        """Return whether a workflow branch already exists on the remote."""
        return self.git.remote_url_branch_exists(
            cwd,
            remote_url=remote_url,
            branch=branch,
            log_name=log_name,
        )

    def existing_pull_request(
        self,
        selected: SelectedImplementation,
    ) -> ExistingPullRequest | None:
        """Return an existing pull request for the selected branch, if any."""
        result = self.runner.run(
            build_gh_pr_list_command(selected.branch),
            cwd=selected.repo_dir,
            log_name="gh-pr-list-existing",
        )
        return pr.parse_existing_pull_request(result.output)

    def create_pull_request(
        self,
        selected: SelectedImplementation,
        *,
        base_branch: str,
        title: str,
        body: str,
    ) -> CreatedPullRequest:
        """Create a pull request for the selected branch."""
        result = self.runner.run(
            build_gh_pr_create_command(
                base_branch=base_branch,
                head_branch=selected.branch,
                title=title,
                body=body,
            ),
            cwd=selected.repo_dir,
            log_name="gh-pr-create",
        )
        return CreatedPullRequest(
            url=pr.parse_pr_url(result.output),
            number=pr.parse_pr_number(result.output),
        )

    def edit_pull_request_body(self, repo_dir: Path, *, pr_url: str, body: str) -> None:
        """Edit an existing pull request body."""
        self.runner.run(
            build_gh_pr_edit_body_command(pr_url=pr_url, body=body),
            cwd=repo_dir,
            log_name="gh-pr-edit-final-review-warning",
        )


class ReviewProvider:
    """Current raw review provider operation backed by an external command."""

    def __init__(self, *, runner: FileCommandExecutor) -> None:
        """Create a review provider backed by the shared runner."""
        self.runner = runner

    def run_review(
        self,
        command: Sequence[str],
        *,
        repo_dir: Path,
        log_name: str,
        output_path: Path,
    ) -> CommandResult:
        """Run a configured review command into a markdown review file."""
        return self.runner.run_to_file(
            command,
            cwd=repo_dir,
            log_name=log_name,
            output_path=output_path,
        )
