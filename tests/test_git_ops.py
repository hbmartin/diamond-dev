"""Tests for git operation helpers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from diamond_dev.config import DiamondDevConfig
from diamond_dev.errors import CommandFailureError, DiamondDevError
from diamond_dev.executor import CommandResult, CommandRunner
from diamond_dev.git_ops import GitOperations
from diamond_dev.workflow import (
    ImplementationBranch,
    ImplementationContext,
    PlanContext,
    RunContext,
    WikiContext,
)


def test_remote_default_branch_rejects_empty_symbolic_ref_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CommandRunner(tmp_path / "logs")
    git = GitOperations(runner)

    def empty_symbolic_ref(
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        assert check
        return CommandResult(
            command=tuple(command),
            cwd=cwd,
            returncode=0,
            log_path=tmp_path / f"{log_name}.log",
            output=" \n",
        )

    monkeypatch.setattr(runner, "run", empty_symbolic_ref)

    with pytest.raises(DiamondDevError, match="No output returned"):
        git.remote_default_branch(tmp_path)


def test_local_branch_exists_checks_only_local_heads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CommandRunner(tmp_path / "logs")
    git = GitOperations(runner)
    commands: list[tuple[str, ...]] = []

    def branch_missing(
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        assert cwd == tmp_path
        assert log_name == "local-branch"
        assert not check
        command_tuple = tuple(command)
        commands.append(command_tuple)
        return CommandResult(
            command=command_tuple,
            cwd=cwd,
            returncode=1,
            log_path=tmp_path / f"{log_name}.log",
            output="",
        )

    monkeypatch.setattr(runner, "run", branch_missing)

    assert not git.local_branch_exists(tmp_path, "release", log_name="local-branch")
    assert commands == [
        ("git", "rev-parse", "--verify", "--quiet", "refs/heads/release"),
    ]


def test_branch_ahead_behind_parses_counts_from_last_output_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CommandRunner(tmp_path / "logs")
    git = GitOperations(runner)

    def ahead_behind_output(
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        assert check
        return CommandResult(
            command=tuple(command),
            cwd=cwd,
            returncode=0,
            log_path=tmp_path / f"{log_name}.log",
            output="warning: ignored diagnostic\n2\t3\n",
        )

    monkeypatch.setattr(runner, "run", ahead_behind_output)

    counts = git.branch_ahead_behind(
        tmp_path,
        branch="feature",
        base_branch="main",
        log_name="ahead-behind",
    )

    assert counts.behind == 2
    assert counts.ahead == 3


def test_branch_ahead_behind_rejects_empty_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CommandRunner(tmp_path / "logs")
    git = GitOperations(runner)

    def empty_ahead_behind_output(
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        assert check
        return CommandResult(
            command=tuple(command),
            cwd=cwd,
            returncode=0,
            log_path=tmp_path / f"{log_name}.log",
            output=" \n",
        )

    monkeypatch.setattr(runner, "run", empty_ahead_behind_output)

    with pytest.raises(DiamondDevError, match="No output returned"):
        git.branch_ahead_behind(
            tmp_path,
            branch="feature",
            base_branch="main",
            log_name="ahead-behind",
        )


def test_is_git_repo_returns_false_for_expected_codes_and_raises_on_failure(
    tmp_path: Path,
) -> None:
    runner = _ScriptedRunner(
        tmp_path,
        results=[
            _ScriptedResult(returncode=1),
            _ScriptedResult(returncode=128),
            _ScriptedResult(returncode=2),
        ],
    )
    git = GitOperations(runner)

    assert not git.is_git_repo(tmp_path, log_name="not-repo")
    assert not git.is_git_repo(tmp_path, log_name="not-repo-128")
    with pytest.raises(CommandFailureError):
        git.is_git_repo(tmp_path, log_name="broken-repo")


def test_fetch_origin_url_and_checkout_existing_branch_delegate_to_git(
    tmp_path: Path,
) -> None:
    runner = _ScriptedRunner(
        tmp_path,
        results=[
            _ScriptedResult(),
            _ScriptedResult(output="git@github.com:owner/repo.git\n"),
            _ScriptedResult(),
        ],
    )
    git = GitOperations(runner)

    git.fetch(tmp_path, log_name="fetch-origin")
    origin_url = git.origin_url(tmp_path, log_name="origin-url")
    git.checkout_existing_branch(tmp_path, branch="feature", log_prefix="resume")

    assert origin_url == "git@github.com:owner/repo.git"
    assert [call.command for call in runner.calls] == [
        ("git", "fetch", "--prune", "origin"),
        ("git", "remote", "get-url", "origin"),
        ("git", "checkout", "feature"),
    ]
    assert [call.log_name for call in runner.calls] == [
        "fetch-origin",
        "origin-url",
        "resume-checkout-existing",
    ]


def test_remote_default_branch_rejects_unexpected_ref(tmp_path: Path) -> None:
    runner = _ScriptedRunner(
        tmp_path,
        results=[_ScriptedResult(output="upstream/main\n")],
    )
    git = GitOperations(runner)

    with pytest.raises(DiamondDevError, match="Unexpected remote HEAD ref"):
        git.remote_default_branch(tmp_path)


def test_ensure_remote_branch_absent_raises_when_remote_branch_exists(
    tmp_path: Path,
) -> None:
    runner = _ScriptedRunner(tmp_path, results=[_ScriptedResult(returncode=0)])
    git = GitOperations(runner)

    with pytest.raises(DiamondDevError, match="already exists"):
        git.ensure_remote_branch_absent(tmp_path, "feature")


def test_remote_branch_helpers_parse_missing_and_unexpected_codes(
    tmp_path: Path,
) -> None:
    runner = _ScriptedRunner(
        tmp_path,
        results=[
            _ScriptedResult(returncode=2),
            _ScriptedResult(returncode=1),
        ],
    )
    git = GitOperations(runner)

    assert not git.remote_url_branch_exists(
        tmp_path,
        remote_url="git@github.com:owner/repo.git",
        branch="missing",
        log_name="remote-url-missing",
    )
    with pytest.raises(CommandFailureError):
        git.remote_branch_exists(
            tmp_path,
            "broken",
            log_name="remote-branch-broken",
        )


def test_local_branch_exists_returns_true_and_raises_on_unexpected_code(
    tmp_path: Path,
) -> None:
    runner = _ScriptedRunner(
        tmp_path,
        results=[
            _ScriptedResult(returncode=0),
            _ScriptedResult(returncode=2),
        ],
    )
    git = GitOperations(runner)

    assert git.local_branch_exists(tmp_path, "feature", log_name="local-feature")
    with pytest.raises(CommandFailureError):
        git.local_branch_exists(tmp_path, "broken", log_name="local-broken")


def test_branch_ahead_behind_rejects_malformed_output(tmp_path: Path) -> None:
    runner = _ScriptedRunner(
        tmp_path,
        results=[_ScriptedResult(output="1 2 3\n")],
    )
    git = GitOperations(runner)

    with pytest.raises(DiamondDevError, match="Unexpected ahead/behind output"):
        git.branch_ahead_behind(
            tmp_path,
            branch="feature",
            base_branch="main",
            log_name="ahead-behind",
        )


def test_branches_match_remote_compares_revision_outputs(tmp_path: Path) -> None:
    runner = _ScriptedRunner(
        tmp_path,
        results=[
            _ScriptedResult(output="abc123\n"),
            _ScriptedResult(output="abc123\n"),
        ],
    )
    git = GitOperations(runner)

    assert git.branches_match_remote(tmp_path, "feature", log_prefix="compare")
    assert [call.command for call in runner.calls] == [
        ("git", "rev-parse", "feature"),
        ("git", "rev-parse", "origin/feature"),
    ]


def test_revision_rejects_empty_output(tmp_path: Path) -> None:
    runner = _ScriptedRunner(tmp_path, results=[_ScriptedResult(output=" \n")])
    git = GitOperations(runner)

    with pytest.raises(DiamondDevError, match="Could not resolve git revision"):
        git.revision(tmp_path, "HEAD", log_name="revision")


def test_commit_if_changes_raises_on_staged_diff_failure(tmp_path: Path) -> None:
    (tmp_path / "tracked.md").write_text("content\n", encoding="utf-8")
    runner = _ScriptedRunner(
        tmp_path,
        results=[
            _ScriptedResult(),
            _ScriptedResult(returncode=2),
        ],
    )
    git = GitOperations(runner)

    with pytest.raises(CommandFailureError):
        git.commit_if_changes(
            tmp_path,
            message="Update tracked file",
            log_prefix="tracked",
            paths=("tracked.md",),
        )


def test_commit_if_changes_raises_when_tracking_check_fails(tmp_path: Path) -> None:
    runner = _ScriptedRunner(tmp_path, results=[_ScriptedResult(returncode=2)])
    git = GitOperations(runner)

    with pytest.raises(CommandFailureError):
        git.commit_if_changes(
            tmp_path,
            message="Update missing file",
            log_prefix="missing",
            paths=("missing.md",),
        )


def test_push_agent_branch_records_dirty_files_and_pushes(tmp_path: Path) -> None:
    runner = _ScriptedRunner(
        tmp_path,
        results=[
            _ScriptedResult(output=" M src/app.py\n?? README.md\nA\n"),
            _ScriptedResult(),
        ],
    )
    git = GitOperations(runner)
    context: RunContext = _run_context(tmp_path)

    updated_context: RunContext = git.push_agent_branch(
        context,
        label="codex initial",
        repo_dir=tmp_path,
        branch="codex/my-plan",
        log_prefix="codex-initial",
    )

    assert len(updated_context.dirty_records) == 1
    dirty_record = updated_context.dirty_records[0]
    assert dirty_record.label == "codex initial"
    assert dirty_record.branch == "codex/my-plan"
    assert dirty_record.files == ("src/app.py", "README.md", "A")
    assert [call.command for call in runner.calls] == [
        ("git", "status", "--porcelain"),
        ("git", "push", "-u", "origin", "codex/my-plan"),
    ]
    assert [call.log_name for call in runner.calls] == [
        "codex-initial-dirty-status",
        "codex-initial-push",
    ]


class _ScriptedResult:
    def __init__(self, *, returncode: int = 0, output: str = "") -> None:
        self.returncode = returncode
        self.output = output


class _ScriptedCall:
    def __init__(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        log_name: str,
        check: bool,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.log_name = log_name
        self.check = check


class _ScriptedRunner:
    def __init__(self, tmp_path: Path, *, results: list[_ScriptedResult]) -> None:
        self.tmp_path = tmp_path
        self.results = results
        self.calls: list[_ScriptedCall] = []

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        command_tuple = tuple(command)
        self.calls.append(
            _ScriptedCall(
                command=command_tuple,
                cwd=cwd,
                log_name=log_name,
                check=check,
            ),
        )
        result = self.results.pop(0)
        return CommandResult(
            command=command_tuple,
            cwd=cwd,
            returncode=result.returncode,
            log_path=self.tmp_path / f"{log_name}.log",
            output=result.output,
        )


def _run_context(tmp_path: Path) -> RunContext:
    wiki_dir = tmp_path / "repo.wiki"
    return RunContext(
        cwd=tmp_path,
        config=DiamondDevConfig(
            config_path=tmp_path / ".diamond-dev.toml",
            repository_url="git@github.com:owner/repo.git",
        ),
        plan=PlanContext(
            path=tmp_path / "My Plan.md",
            slug="my-plan",
        ),
        wiki=WikiContext(
            url="git@github.com:owner/repo.wiki.git",
            directory=wiki_dir,
            comparison_file=wiki_dir / "my-plan-comparison.md",
            comparison_bundle_file=wiki_dir / "my-plan-comparison-bundle.md",
            review_file=wiki_dir / "my-plan-review.md",
            review_judgments_file=wiki_dir / "my-plan-review-judgments.json",
        ),
        implementation=ImplementationContext(
            branches=(
                ImplementationBranch(
                    agent_name="codex",
                    repo_dir=tmp_path,
                    branch="codex/my-plan",
                    log_prefix="codex",
                ),
            ),
            base_branch="main",
        ),
    )
