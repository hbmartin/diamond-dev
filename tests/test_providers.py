"""Tests for thin workflow providers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from diamond_dev.executor import CommandResult
from diamond_dev.git_ops import GitOperations
from diamond_dev.providers import GitHubWorkflowProvider, ReviewProvider
from diamond_dev.workflow import SelectedImplementation


class _FakeRunner:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.calls: list[tuple[tuple[str, ...], Path, str, bool]] = []
        self.file_calls: list[tuple[tuple[str, ...], Path, str, Path, bool]] = []
        self.next_output = ""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        command_tuple = tuple(command)
        self.calls.append((command_tuple, cwd, log_name, check))
        return CommandResult(
            command=command_tuple,
            cwd=cwd,
            returncode=0,
            log_path=self.tmp_path / f"{log_name}.log",
            output=self.next_output,
        )

    def run_to_file(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        output_path: Path,
        check: bool = True,
    ) -> CommandResult:
        command_tuple = tuple(command)
        self.file_calls.append((command_tuple, cwd, log_name, output_path, check))
        output_path.write_text(self.next_output, encoding="utf-8")
        return CommandResult(
            command=command_tuple,
            cwd=cwd,
            returncode=0,
            log_path=self.tmp_path / f"{log_name}.log",
            output="",
        )


class _FakeGit:
    def __init__(self) -> None:
        self.synced: list[Path] = []
        self.run_calls: list[tuple[Path, tuple[str, ...], str, bool]] = []
        self.remote_checks: list[tuple[Path, str, str, str]] = []

    def sync_wiki(self, wiki_dir: Path) -> None:
        self.synced.append(wiki_dir)

    def run(
        self,
        repo_dir: Path,
        *args: str,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        self.run_calls.append((repo_dir, args, log_name, check))
        return CommandResult(
            command=("git", *args),
            cwd=repo_dir,
            returncode=0,
            log_path=repo_dir / f"{log_name}.log",
            output="",
        )

    def remote_url_branch_exists(
        self,
        cwd: Path,
        *,
        remote_url: str,
        branch: str,
        log_name: str,
    ) -> bool:
        self.remote_checks.append((cwd, remote_url, branch, log_name))
        return True


def test_github_workflow_provider_delegates_wiki_operations(tmp_path: Path) -> None:
    runner = _FakeRunner(tmp_path)
    git = _FakeGit()
    provider = GitHubWorkflowProvider(runner=runner, git=git)  # type: ignore[arg-type]

    provider.sync_wiki(tmp_path / "repo.wiki")
    provider.push_wiki(tmp_path / "repo.wiki", log_name="wiki-push")

    assert git.synced == [tmp_path / "repo.wiki"]
    assert git.run_calls == [
        (tmp_path / "repo.wiki", ("push",), "wiki-push", True),
    ]


def test_github_workflow_provider_delegates_branch_lookup(tmp_path: Path) -> None:
    runner = _FakeRunner(tmp_path)
    git = _FakeGit()
    provider = GitHubWorkflowProvider(runner=runner, git=git)  # type: ignore[arg-type]

    exists = provider.remote_workflow_branch_exists(
        tmp_path,
        remote_url="git@github.com:owner/repo.git",
        branch="codex/my-plan",
        log_name="branch-check",
    )

    assert exists
    assert git.remote_checks == [
        (
            tmp_path,
            "git@github.com:owner/repo.git",
            "codex/my-plan",
            "branch-check",
        ),
    ]


def test_github_workflow_provider_uses_existing_gh_pr_commands(
    tmp_path: Path,
) -> None:
    runner = _FakeRunner(tmp_path)
    git = GitOperations(runner)  # type: ignore[arg-type]
    provider = GitHubWorkflowProvider(runner=runner, git=git)  # type: ignore[arg-type]
    selected = SelectedImplementation(
        accepted_agent="codex",
        comparison_fixer="claude",
        branch="codex/my-plan",
        repo_dir=tmp_path / "repo",
    )

    runner.next_output = (
        '[{"number":123,"state":"OPEN",'
        '"url":"https://github.com/owner/repo/pull/123"}]'
    )
    existing = provider.existing_pull_request(selected)
    runner.next_output = "https://github.com/owner/repo/pull/124\n"
    created = provider.create_pull_request(
        selected,
        base_branch="main",
        title="Implement plan",
        body="body",
    )
    provider.edit_pull_request_body(
        selected.repo_dir,
        pr_url=created.url,
        body="updated body",
    )

    assert existing is not None
    assert existing.number == 123
    assert created.number == "124"
    assert runner.calls == [
        (
            (
                "gh",
                "pr",
                "list",
                "--head",
                "codex/my-plan",
                "--state",
                "all",
                "--json",
                "number,state,url",
                "--limit",
                "1",
            ),
            selected.repo_dir,
            "gh-pr-list-existing",
            True,
        ),
        (
            (
                "gh",
                "pr",
                "create",
                "--base",
                "main",
                "--head",
                "codex/my-plan",
                "--title",
                "Implement plan",
                "--body",
                "body",
            ),
            selected.repo_dir,
            "gh-pr-create",
            True,
        ),
        (
            (
                "gh",
                "pr",
                "edit",
                "https://github.com/owner/repo/pull/124",
                "--body",
                "updated body",
            ),
            selected.repo_dir,
            "gh-pr-edit-final-review-warning",
            True,
        ),
    ]


def test_review_provider_writes_raw_review_markdown(tmp_path: Path) -> None:
    runner = _FakeRunner(tmp_path)
    runner.next_output = "# Review\n"
    provider = ReviewProvider(runner=runner)  # type: ignore[arg-type]
    output_path = tmp_path / "review.md"

    result = provider.run_review(
        ("coderabbit", "review", "--plain", "--base", "main"),
        repo_dir=tmp_path / "repo",
        log_name="coderabbit-review",
        output_path=output_path,
    )

    assert result.output == ""
    assert output_path.read_text(encoding="utf-8") == "# Review\n"
    assert runner.file_calls == [
        (
            ("coderabbit", "review", "--plain", "--base", "main"),
            tmp_path / "repo",
            "coderabbit-review",
            output_path,
            True,
        ),
    ]
