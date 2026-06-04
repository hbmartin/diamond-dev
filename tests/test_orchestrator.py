"""Tests for orchestrator helper behavior."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from diamond_dev.acceptance import acceptance_wait_delays
from diamond_dev.config import DiamondDevConfig
from diamond_dev.errors import CommandFailureError, DiamondDevError
from diamond_dev.executor import (
    CommandLogRecord,
    CommandResult,
    CommandRunner,
    re_slug,
)
from diamond_dev.git_ops import BranchAheadBehind
from diamond_dev.orchestrator import DiamondDevOrchestrator
from diamond_dev.pr import build_pr_body
from diamond_dev.report import PhaseWarning
from diamond_dev.workflow import (
    DirtyRecord,
    ImplementationBranch,
    ImplementationContext,
    PlanContext,
    RunContext,
    SelectedImplementation,
    WikiContext,
)

if TYPE_CHECKING:
    from diamond_dev.orchestrator_repositories import ResumeAgentBranch


class _RecordingRunner:
    """Minimal command runner fake for orchestrator push tests."""

    def __init__(
        self,
        *,
        clone_lockfiles_by_dir: dict[Path, tuple[str, ...]] | None = None,
    ) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.command_calls: list[tuple[tuple[str, ...], Path, str]] = []
        self.interactive_commands: list[tuple[str, ...]] = []
        self.clone_lockfiles_by_dir = clone_lockfiles_by_dir or {}

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
        self.command_calls.append((command_tuple, cwd, log_name))
        output = ""
        returncode = 0
        if command_tuple[:2] == ("git", "clone"):
            clone_dir = Path(command_tuple[-1])
            clone_dir.mkdir(parents=True, exist_ok=True)
            (clone_dir / ".git").mkdir(exist_ok=True)
            for lockfile_name in self.clone_lockfiles_by_dir.get(clone_dir, ()):
                (clone_dir / lockfile_name).write_text("", encoding="utf-8")
        if command_tuple[:3] == ("git", "ls-remote", "--exit-code"):
            returncode = 2
        if command_tuple[:3] == ("gh", "pr", "list"):
            output = "[]"
        if command_tuple[:3] == ("gh", "pr", "create"):
            output = "https://github.com/owner/repo/pull/123"
        return CommandResult(
            command=command_tuple,
            cwd=cwd,
            returncode=returncode,
            log_path=cwd / f"{log_name}.log",
            output=output,
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
        output_path.write_text("", encoding="utf-8")
        return self.run(command, cwd=cwd, log_name=log_name, check=check)

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

    def start(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
    ) -> _CompletedProcess:
        command_tuple = tuple(command)
        self.commands.append(command_tuple)
        return _CompletedProcess(
            CommandResult(
                command=command_tuple,
                cwd=cwd,
                returncode=0,
                log_path=cwd / f"{log_name}.log",
                output="",
            ),
        )


class _CompletedProcess:
    """Minimal completed process for orchestrator start tests."""

    def __init__(self, result: CommandResult) -> None:
        self.result = result

    def wait(self) -> CommandResult:
        return self.result


class _ScriptedRunner(CommandRunner):
    """Scripted runner for a full orchestrator walkthrough."""

    def __init__(self, log_dir: Path) -> None:
        super().__init__(log_dir)
        self.commands: list[tuple[str, ...]] = []
        self.interactive_commands: list[tuple[str, ...]] = []

    def run(  # noqa: C901
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        command_tuple = tuple(command)
        self.commands.append(command_tuple)
        output = ""
        returncode = 0

        match command_tuple:
            case ("git", "clone", *_rest, target_dir):
                target_path = Path(target_dir)
                target_path.mkdir(parents=True, exist_ok=True)
                (target_path / ".git").mkdir(exist_ok=True)
            case ("git", "symbolic-ref", *_):
                output = "origin/main\n"
            case ("git", "rev-parse", *_):
                output = "abc123\n"
            case ("git", "ls-remote", *_):
                returncode = 2
            case ("git", "rev-list", "--left-right", "--count", *_):
                output = "0\t0\n"
            case ("git", "diff", "--name-status", *_):
                output = "M\tsrc/app.py\n"
            case ("git", "diff", "--no-color", *_):
                output = (
                    "diff --git a/src/app.py b/src/app.py\n"
                    "--- a/src/app.py\n"
                    "+++ b/src/app.py\n"
                    "@@ -1 +1 @@\n"
                    "-old\n"
                    "+new\n"
                )
            case ("git", "diff", "--cached", *_):
                returncode = 1
            case ("git", "status", "--porcelain"):
                output = ""
            case ("git", "pull", *_):
                self._accept_comparison(cwd)
            case ("gh", "pr", "list", *_):
                output = "[]"
            case ("gemini", *_):
                (cwd / "comparison.md").write_text("Comparison\n", encoding="utf-8")
            case ("gh", "pr", "create", *_):
                output = "https://github.com/owner/repo/pull/123\n"

        result = self._result(
            command=command_tuple,
            cwd=cwd,
            log_name=log_name,
            returncode=returncode,
            output=output,
        )
        assert not check or returncode == 0
        return result

    def run_to_file(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        output_path: Path,
        check: bool = True,
    ) -> CommandResult:
        output_path.write_text("Review\n(A) Fix accepted item.\n", encoding="utf-8")
        return self.run(command, cwd=cwd, log_name=log_name, check=check)

    def start(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
    ) -> _CompletedProcess:
        command_tuple = tuple(command)
        self.commands.append(command_tuple)
        return _CompletedProcess(
            self._result(
                command=command_tuple,
                cwd=cwd,
                log_name=log_name,
                returncode=0,
                output="",
            ),
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
        return self._result(
            command=command_tuple,
            cwd=cwd,
            log_name=log_name,
            returncode=0,
            output="",
        )

    def _result(
        self,
        *,
        command: tuple[str, ...],
        cwd: Path,
        log_name: str,
        returncode: int,
        output: str,
    ) -> CommandResult:
        log_path = self.log_dir / f"{re_slug(log_name)}.log"
        log_path.write_text(output, encoding="utf-8")
        self.command_logs.append(
            CommandLogRecord(
                label=log_name,
                command=command,
                cwd=cwd,
                log_path=log_path,
            ),
        )
        return CommandResult(
            command=command,
            cwd=cwd,
            returncode=returncode,
            log_path=log_path,
            output=output,
        )

    def _accept_comparison(self, cwd: Path) -> None:
        for comparison_path in cwd.glob("*-comparison.md"):
            comparison_markdown = comparison_path.read_text(encoding="utf-8")
            comparison_path.write_text(
                comparison_markdown.replace(
                    "- [ ] Accept: (codex/claude)",
                    "- [x] Accept: codex",
                ),
                encoding="utf-8",
            )


class _CodeRabbitFailureRunner(_ScriptedRunner):
    """Scripted runner that fails the CodeRabbit review phase."""

    def run_to_file(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        output_path: Path,
        check: bool = True,
    ) -> CommandResult:
        assert isinstance(check, bool)
        assert output_path.name.endswith("-review.md")
        command_tuple = tuple(command)
        self.commands.append(command_tuple)
        log_path = self.log_dir / f"{re_slug(log_name)}.log"
        log_path.write_text("CodeRabbit crashed\n", encoding="utf-8")
        self.command_logs.append(
            CommandLogRecord(
                label=log_name,
                command=command_tuple,
                cwd=cwd,
                log_path=log_path,
            ),
        )
        raise CommandFailureError(
            command=" ".join(command_tuple),
            cwd=str(cwd),
            returncode=1,
            log_path=str(log_path),
        )


class _FinalReviewFailureRunner(_RecordingRunner):
    """Recording runner that fails the final interactive review."""

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
        raise CommandFailureError(
            command=" ".join(command_tuple),
            cwd=str(cwd),
            returncode=1,
            log_path=str(cwd / f"{log_name}.log"),
        )


class _FinalReviewAndBodyEditFailureRunner(_FinalReviewFailureRunner):
    """Recording runner that also fails the warning PR body edit."""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        result = super().run(command, cwd=cwd, log_name=log_name, check=check)
        if tuple(command)[:3] == ("gh", "pr", "edit"):
            raise CommandFailureError(
                command=" ".join(command),
                cwd=str(cwd),
                returncode=1,
                log_path=str(result.log_path),
            )
        return result


class _ReviewJudgmentFailureRunner(_RecordingRunner):
    """Recording runner that produces a review then fails judgment."""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        if log_name == "codex-review-judgment":
            command_tuple = tuple(command)
            self.commands.append(command_tuple)
            self.command_calls.append((command_tuple, cwd, log_name))
            raise CommandFailureError(
                command=" ".join(command_tuple),
                cwd=str(cwd),
                returncode=1,
                log_path=str(cwd / f"{log_name}.log"),
            )
        return super().run(command, cwd=cwd, log_name=log_name, check=check)

    def run_to_file(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        output_path: Path,
        check: bool = True,
    ) -> CommandResult:
        output_path.write_text("Raw review\n", encoding="utf-8")
        return self.run(command, cwd=cwd, log_name=log_name, check=check)


def build_context(tmp_path: Path) -> RunContext:
    config = DiamondDevConfig(
        config_path=tmp_path / ".diamond-dev.toml",
        repository_url="git@github.com:owner/repo.git",
    )
    return RunContext(
        cwd=tmp_path,
        config=config,
        plan=PlanContext(
            path=tmp_path / "My Plan.md",
            slug="my-plan",
        ),
        wiki=WikiContext(
            url="git@github.com:owner/repo.wiki.git",
            directory=tmp_path / "repo.wiki",
            comparison_file=tmp_path / "repo.wiki" / "my-plan-comparison.md",
            comparison_bundle_file=(
                tmp_path / "repo.wiki" / "my-plan-comparison-bundle.md"
            ),
            review_file=tmp_path / "repo.wiki" / "my-plan-review.md",
            review_judgments_file=(
                tmp_path / "repo.wiki" / "my-plan-review-judgments.json"
            ),
        ),
        implementation=ImplementationContext(
            branches=(
                ImplementationBranch(
                    agent_name="codex",
                    repo_dir=tmp_path / "codex-my-plan",
                    branch="codex/my-plan",
                    log_prefix="codex",
                ),
                ImplementationBranch(
                    agent_name="claude",
                    repo_dir=tmp_path / "claude-my-plan",
                    branch="claude/my-plan",
                    log_prefix="claude",
                ),
            ),
            base_branch="main",
        ),
    )


def _prepare_clones_for_lockfiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    codex_lockfiles: tuple[str, ...],
    claude_lockfiles: tuple[str, ...],
) -> tuple[RunContext, _RecordingRunner]:
    context = build_context(tmp_path)
    context = context.with_implementation(context.implementation.with_base_branch(""))
    context.plan.path.write_text("# My Plan\n", encoding="utf-8")
    runner = _RecordingRunner(
        clone_lockfiles_by_dir={
            context.implementation.codex_dir: codex_lockfiles,
            context.implementation.claude_dir: claude_lockfiles,
        },
    )
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)

    def remote_default_branch(repo_dir: Path) -> str:
        assert repo_dir == context.implementation.codex_dir
        return "trunk"

    def remote_url_branch_exists(
        cwd: Path,
        *,
        remote_url: str,
        branch: str,
        log_name: str,
    ) -> bool:
        assert cwd == context.cwd
        assert remote_url == context.config.repository_url
        assert branch in {
            context.implementation.codex_branch,
            context.implementation.claude_branch,
        }
        assert log_name
        return False

    def checkout_branch(
        repo_dir: Path,
        *,
        branch: str,
        base_branch: str,
        log_prefix: str,
    ) -> None:
        assert repo_dir in {
            context.implementation.codex_dir,
            context.implementation.claude_dir,
        }
        assert branch in {
            context.implementation.codex_branch,
            context.implementation.claude_branch,
        }
        assert base_branch == "trunk"
        assert log_prefix in {"codex", "claude"}

    monkeypatch.setattr(orchestrator.git, "remote_default_branch", remote_default_branch)
    monkeypatch.setattr(
        orchestrator.git,
        "remote_url_branch_exists",
        remote_url_branch_exists,
    )
    monkeypatch.setattr(orchestrator.git, "checkout_branch", checkout_branch)

    updated_context = orchestrator._prepare_implementation_clones(context)  # noqa: SLF001
    return updated_context, runner


def _install_calls(
    runner: _RecordingRunner,
) -> list[tuple[tuple[str, ...], Path, str]]:
    return [
        command_call
        for command_call in runner.command_calls
        if command_call[0][0] in {"pnpm", "uv"}
    ]


def test_run_happy_path_walks_full_phase_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_path = tmp_path / "My Plan.md"
    plan_path.write_text("# My Plan\n", encoding="utf-8")
    (tmp_path / ".diamond-dev.toml").write_text(
        'repository_url = "git@github.com:owner/repo.git"\n'
        'wiki_repository_url = "git@github.com:owner/repo.wiki.git"\n',
        encoding="utf-8",
    )
    runner = _ScriptedRunner(tmp_path / "logs")
    monkeypatch.setattr(
        "diamond_dev.preflight.shutil.which",
        lambda cli_name: f"/usr/bin/{cli_name}",
    )
    monkeypatch.setattr("diamond_dev.orchestrator.acceptance_wait_delays", lambda: (0,))
    orchestrator = DiamondDevOrchestrator(
        cwd=tmp_path,
        runner=runner,
        sleep=lambda _seconds: None,
    )

    exit_code = orchestrator.run(plan_path)

    assert exit_code == 0
    log_labels = [command_log.label for command_log in runner.command_logs]
    assert log_labels.index("preflight-gh-auth") < log_labels.index("wiki-clone")
    assert log_labels.index("wiki-clone") < log_labels.index("codex-clone")
    assert log_labels.index("codex-clone") < log_labels.index("gh-pr-create")
    assert "claude-clone" not in log_labels
    assert any(command[:3] == ("gh", "pr", "create") for command in runner.commands)
    assert runner.interactive_commands == [
        (
            "claude",
            "--permission-mode",
            "bypassPermissions",
            "--dangerously-skip-permissions",
            "/review 123",
        ),
    ]

    report = json.loads((tmp_path / "logs" / "run-report.json").read_text())
    assert report["status"] == "succeeded"
    assert report["phase_warnings"] == []
    assert report["context"]["artifacts"]["pr_url"] == (
        "https://github.com/owner/repo/pull/123"
    )
    assert report["selected_implementation"]["accepted_agent"] == "codex"
    assert report["context"]["branches"]["base"] == "main"
    assert report["preflight"]["cli_checks"][0] == {
        "name": "git",
        "path": "/usr/bin/git",
    }
    assert {phase["name"] for phase in report["phase_timings"]} >= {
        "preflight",
        "prepare or resume implementation clones",
        "finalize pull request",
    }
    assert any(
        command_log["label"] == "gh-pr-create"
        for command_log in report["command_logs"]
    )


def test_run_reports_warning_status_when_coderabbit_review_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_path = tmp_path / "My Plan.md"
    plan_path.write_text("# My Plan\n", encoding="utf-8")
    (tmp_path / ".diamond-dev.toml").write_text(
        'repository_url = "git@github.com:owner/repo.git"\n'
        'wiki_repository_url = "git@github.com:owner/repo.wiki.git"\n',
        encoding="utf-8",
    )
    runner = _CodeRabbitFailureRunner(tmp_path / "logs")
    monkeypatch.setattr(
        "diamond_dev.preflight.shutil.which",
        lambda cli_name: f"/usr/bin/{cli_name}",
    )
    monkeypatch.setattr("diamond_dev.orchestrator.acceptance_wait_delays", lambda: (0,))
    orchestrator = DiamondDevOrchestrator(
        cwd=tmp_path,
        runner=runner,
        sleep=lambda _seconds: None,
    )

    exit_code = orchestrator.run(plan_path)

    assert exit_code == 0
    report = json.loads((tmp_path / "logs" / "run-report.json").read_text())
    assert report["status"] == "succeeded_with_warnings"
    assert [
        (warning["phase"], warning["status"])
        for warning in report["phase_warnings"]
    ] == [
        ("CodeRabbit review", "failed"),
        ("Codex review judgment", "skipped"),
        ("Codex review fixes", "skipped"),
    ]
    pr_create_command = next(
        command
        for command in runner.commands
        if command[:3] == ("gh", "pr", "create")
    )
    pr_body = pr_create_command[pr_create_command.index("--body") + 1]
    assert "Workflow warnings:" in pr_body
    assert "CodeRabbit review (failed)" in pr_body
    assert "Codex review fixes (skipped)" in pr_body


def test_run_uses_configured_review_fixer_and_final_reviewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_path = tmp_path / "My Plan.md"
    plan_path.write_text("# My Plan\n", encoding="utf-8")
    (tmp_path / ".diamond-dev.toml").write_text(
        'repository_url = "git@github.com:owner/repo.git"\n'
        'wiki_repository_url = "git@github.com:owner/repo.wiki.git"\n'
        "[workflow]\n"
        'review_fixer = "claude-fixer"\n'
        'final_reviewer = "claude-reviewer"\n'
        "[agents.claude-fixer]\n"
        'adapter = "claude"\n'
        'model = "opus"\n'
        "[agents.claude-reviewer]\n"
        'adapter = "claude"\n'
        'model = "sonnet"\n',
        encoding="utf-8",
    )
    runner = _ScriptedRunner(tmp_path / "logs")
    monkeypatch.setattr(
        "diamond_dev.preflight.shutil.which",
        lambda cli_name: f"/usr/bin/{cli_name}",
    )
    monkeypatch.setattr("diamond_dev.orchestrator.acceptance_wait_delays", lambda: (0,))
    orchestrator = DiamondDevOrchestrator(
        cwd=tmp_path,
        runner=runner,
        sleep=lambda _seconds: None,
    )

    exit_code = orchestrator.run(plan_path)

    assert exit_code == 0
    assert any(
        command[:3] == ("claude", "--model", "opus")
        for command in runner.commands
    )
    assert runner.interactive_commands[-1] == (
        "claude",
        "--model",
        "sonnet",
        "--permission-mode",
        "bypassPermissions",
        "--dangerously-skip-permissions",
        "/review 123",
    )
    report = json.loads((tmp_path / "logs" / "run-report.json").read_text())
    assert report["context"]["workflow_roles"]["review_fixer"] == "claude-fixer"
    assert report["context"]["workflow_roles"]["final_reviewer"] == "claude-reviewer"


def test_build_pr_body_includes_dirty_records(tmp_path: Path) -> None:
    context = build_context(tmp_path).with_dirty_record(
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


def test_build_pr_body_includes_phase_warnings(tmp_path: Path) -> None:
    context = build_context(tmp_path)
    selected = SelectedImplementation(
        accepted_agent="codex",
        opposite_agent="claude",
        repo_dir=tmp_path / "codex-my-plan",
        branch="codex/my-plan",
    )
    warning = PhaseWarning(
        phase="CodeRabbit review",
        status="failed",
        message="CodeRabbit review failed; no review file was produced.",
        error="exit 1",
        log_name="coderabbit-review",
    )

    body = build_pr_body(context, selected, warnings=(warning,))

    assert "Workflow warnings:" in body
    assert "CodeRabbit review (failed)" in body
    assert "coderabbit-review" in body


def test_prepare_implementation_clones_returns_context_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    context = context.with_implementation(context.implementation.with_base_branch(""))
    context.plan.path.write_text("# My Plan\n", encoding="utf-8")
    runner = _RecordingRunner()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)
    checked_branches: list[tuple[Path, str]] = []
    checkout_calls: list[tuple[Path, str, str, str]] = []

    def remote_default_branch(repo_dir: Path) -> str:
        assert repo_dir == context.implementation.codex_dir
        return "trunk"

    def remote_url_branch_exists(
        cwd: Path,
        *,
        remote_url: str,
        branch: str,
        log_name: str,
    ) -> bool:
        assert cwd == context.cwd
        assert remote_url == context.config.repository_url
        assert log_name
        checked_branches.append((context.implementation.codex_dir, branch))
        return False

    def checkout_branch(
        repo_dir: Path,
        *,
        branch: str,
        base_branch: str,
        log_prefix: str,
    ) -> None:
        assert context.implementation.claude_dir.is_dir()
        assert (context.implementation.claude_dir / ".git").is_dir()
        checkout_calls.append((repo_dir, branch, base_branch, log_prefix))

    monkeypatch.setattr(orchestrator.git, "remote_default_branch", remote_default_branch)
    monkeypatch.setattr(
        orchestrator.git,
        "remote_url_branch_exists",
        remote_url_branch_exists,
    )
    monkeypatch.setattr(orchestrator.git, "checkout_branch", checkout_branch)

    updated_context = orchestrator._prepare_implementation_clones(context)  # noqa: SLF001

    assert context.implementation.base_branch == ""
    assert updated_context.implementation.base_branch == "trunk"
    assert checked_branches == [
        (context.implementation.codex_dir, "codex/my-plan"),
        (context.implementation.codex_dir, "claude/my-plan"),
    ]
    assert checkout_calls == [
        (context.implementation.codex_dir, "codex/my-plan", "trunk", "codex"),
        (context.implementation.claude_dir, "claude/my-plan", "trunk", "claude"),
    ]
    assert [
        command_call
        for command_call in runner.command_calls
        if command_call[0][:2] == ("git", "clone")
    ] == [
        (
            (
                "git",
                "clone",
                context.config.repository_url,
                str(context.implementation.codex_dir),
            ),
            context.cwd,
            "codex-clone",
        ),
    ]
    assert _install_calls(runner) == []
    assert (context.implementation.codex_dir / "My Plan.md").is_file()
    assert (context.implementation.claude_dir / "My Plan.md").is_file()


def test_prepare_implementation_clones_copies_preserving_before_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    context = context.with_implementation(context.implementation.with_base_branch(""))
    context.plan.path.write_text("# My Plan\n", encoding="utf-8")
    runner = _RecordingRunner()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)

    def remote_default_branch(repo_dir: Path) -> str:
        assert repo_dir == context.implementation.codex_dir
        symlink_target = repo_dir / "source.txt"
        symlink_target.write_text("source\n", encoding="utf-8")
        (repo_dir / "linked.txt").symlink_to("source.txt")
        return "trunk"

    def checkout_branch(
        repo_dir: Path,
        *,
        branch: str,
        base_branch: str,
        log_prefix: str,
    ) -> None:
        assert repo_dir in {
            context.implementation.codex_dir,
            context.implementation.claude_dir,
        }
        assert branch in {
            context.implementation.codex_branch,
            context.implementation.claude_branch,
        }
        assert base_branch == "trunk"
        assert log_prefix in {"codex", "claude"}
        copied_link = context.implementation.claude_dir / "linked.txt"
        assert copied_link.is_symlink()
        assert copied_link.readlink() == Path("source.txt")
        assert (context.implementation.claude_dir / ".git").is_dir()

    monkeypatch.setattr(orchestrator.git, "remote_default_branch", remote_default_branch)
    monkeypatch.setattr(
        orchestrator.git,
        "remote_url_branch_exists",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(orchestrator.git, "checkout_branch", checkout_branch)

    orchestrator._prepare_implementation_clones(context)  # noqa: SLF001

    assert not any(
        log_name == "claude-clone"
        for _command, _cwd, log_name in runner.command_calls
    )


def test_prepare_implementation_clones_installs_uv_lockfiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, runner = _prepare_clones_for_lockfiles(
        tmp_path,
        monkeypatch,
        codex_lockfiles=("uv.lock",),
        claude_lockfiles=("uv.lock",),
    )

    assert _install_calls(runner) == [
        (("uv", "sync", "--locked"), context.implementation.codex_dir, "codex-uv-sync"),
        (
            ("uv", "sync", "--locked"),
            context.implementation.claude_dir,
            "claude-uv-sync",
        ),
    ]


def test_prepare_implementation_clones_installs_pnpm_lockfiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, runner = _prepare_clones_for_lockfiles(
        tmp_path,
        monkeypatch,
        codex_lockfiles=("pnpm-lock.yaml",),
        claude_lockfiles=("pnpm-lock.yaml",),
    )

    assert _install_calls(runner) == [
        (
            ("pnpm", "install", "--frozen-lockfile"),
            context.implementation.codex_dir,
            "codex-pnpm-install",
        ),
        (
            ("pnpm", "install", "--frozen-lockfile"),
            context.implementation.claude_dir,
            "claude-pnpm-install",
        ),
    ]


def test_prepare_implementation_clones_installs_both_lockfiles_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, runner = _prepare_clones_for_lockfiles(
        tmp_path,
        monkeypatch,
        codex_lockfiles=("uv.lock", "pnpm-lock.yaml"),
        claude_lockfiles=("uv.lock", "pnpm-lock.yaml"),
    )

    assert _install_calls(runner) == [
        (("uv", "sync", "--locked"), context.implementation.codex_dir, "codex-uv-sync"),
        (
            ("pnpm", "install", "--frozen-lockfile"),
            context.implementation.codex_dir,
            "codex-pnpm-install",
        ),
        (
            ("uv", "sync", "--locked"),
            context.implementation.claude_dir,
            "claude-uv-sync",
        ),
        (
            ("pnpm", "install", "--frozen-lockfile"),
            context.implementation.claude_dir,
            "claude-pnpm-install",
        ),
    ]


def test_prepare_implementation_clones_resumes_with_shared_install_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    context = context.with_implementation(context.implementation.with_base_branch(""))
    context.implementation.codex_dir.mkdir()
    context.implementation.claude_dir.mkdir()
    (context.implementation.codex_dir / "uv.lock").write_text("", encoding="utf-8")
    (context.implementation.claude_dir / "pnpm-lock.yaml").write_text(
        "",
        encoding="utf-8",
    )
    runner = _RecordingRunner()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)
    validated_branches: list[tuple[Path, str, str]] = []

    def validate_resume_clone(
        _context: RunContext,
        agent_branch: ResumeAgentBranch,
    ) -> None:
        validated_branches.append(
            (
                agent_branch.repo_dir,
                agent_branch.branch,
                agent_branch.log_prefix,
            ),
        )

    def remote_default_branch(repo_dir: Path) -> str:
        assert repo_dir == context.implementation.codex_dir
        return "trunk"

    monkeypatch.setattr(orchestrator, "_validate_resume_clone", validate_resume_clone)
    monkeypatch.setattr(orchestrator.git, "remote_default_branch", remote_default_branch)

    updated_context = orchestrator._prepare_implementation_clones(context)  # noqa: SLF001

    assert updated_context.implementation.base_branch == "trunk"
    assert validated_branches == [
        (context.implementation.codex_dir, "codex/my-plan", "codex"),
        (context.implementation.claude_dir, "claude/my-plan", "claude"),
    ]
    assert _install_calls(runner) == [
        (("uv", "sync", "--locked"), context.implementation.codex_dir, "codex-uv-sync"),
        (
            ("pnpm", "install", "--frozen-lockfile"),
            context.implementation.claude_dir,
            "claude-pnpm-install",
        ),
    ]


def test_prepare_implementation_clones_fails_when_one_clone_missing(
    tmp_path: Path,
) -> None:
    context = build_context(tmp_path)
    context.implementation.codex_dir.mkdir()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=_RecordingRunner())

    with pytest.raises(DiamondDevError, match="missing implementation clone"):
        orchestrator._prepare_implementation_clones(context)  # noqa: SLF001


def test_prepare_implementation_clones_fails_when_remote_branch_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=_RecordingRunner())

    def remote_url_branch_exists(
        _cwd: Path,
        *,
        remote_url: str,
        branch: str,
        log_name: str,
    ) -> bool:
        assert remote_url == context.config.repository_url
        assert branch == context.implementation.codex_branch
        assert log_name
        return True

    monkeypatch.setattr(
        orchestrator.git,
        "remote_url_branch_exists",
        remote_url_branch_exists,
    )

    with pytest.raises(DiamondDevError, match="local implementation clones are missing"):
        orchestrator._prepare_implementation_clones(context)  # noqa: SLF001


def test_run_initial_agents_skips_matching_remote_zero_commit_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    context.plan.path.write_text("# My Plan\n", encoding="utf-8")
    context.implementation.codex_dir.mkdir()
    context.implementation.claude_dir.mkdir()
    runner = _RecordingRunner()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)

    monkeypatch.setattr(orchestrator.git, "remote_branch_exists", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(orchestrator.git, "branches_match_remote", lambda *_args, **_kwargs: True)

    updated_context = orchestrator._run_initial_agents(context)  # noqa: SLF001

    assert updated_context == context
    assert not any(command[0] in {"codex", "claude"} for command in runner.commands)


def test_run_initial_agents_pushes_local_commits_without_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    context.plan.path.write_text("# My Plan\n", encoding="utf-8")
    context.implementation.codex_dir.mkdir()
    context.implementation.claude_dir.mkdir()
    runner = _RecordingRunner()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)

    monkeypatch.setattr(orchestrator.git, "remote_branch_exists", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        orchestrator.git,
        "branch_ahead_behind",
        lambda *_args, **_kwargs: BranchAheadBehind(ahead=1, behind=0),
    )
    monkeypatch.setattr(orchestrator.git, "dirty_files", lambda *_args, **_kwargs: ())

    orchestrator._run_initial_agents(context)  # noqa: SLF001

    assert ("git", "push", "-u", "origin", "codex/my-plan") in runner.commands
    assert ("git", "push", "-u", "origin", "claude/my-plan") in runner.commands
    assert [
        log_name
        for command, _cwd, log_name in runner.command_calls
        if command[:2] == ("git", "push")
    ] == ["codex-initial-push", "claude-initial-push"]
    assert not any(command[0] in {"codex", "claude"} for command in runner.commands)


def test_run_initial_agents_reruns_only_missing_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    context.plan.path.write_text("# My Plan\n", encoding="utf-8")
    context.implementation.codex_dir.mkdir()
    context.implementation.claude_dir.mkdir()
    runner = _RecordingRunner()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)

    def remote_branch_exists(_repo_dir: Path, branch: str, *, log_name: str) -> bool:
        assert log_name
        return branch == context.implementation.codex_branch

    monkeypatch.setattr(orchestrator.git, "remote_branch_exists", remote_branch_exists)
    monkeypatch.setattr(orchestrator.git, "branches_match_remote", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        orchestrator.git,
        "branch_ahead_behind",
        lambda *_args, **_kwargs: BranchAheadBehind(ahead=0, behind=0),
    )
    monkeypatch.setattr(orchestrator.git, "dirty_files", lambda *_args, **_kwargs: ())

    orchestrator._run_initial_agents(context)  # noqa: SLF001

    assert any(command[0] == "claude" for command in runner.commands)
    assert not any(command[:2] == ("codex", "exec") for command in runner.commands)


def test_run_initial_agents_fails_on_branch_divergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    context.implementation.codex_dir.mkdir()
    context.implementation.claude_dir.mkdir()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=_RecordingRunner())

    monkeypatch.setattr(orchestrator.git, "remote_branch_exists", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(orchestrator.git, "branches_match_remote", lambda *_args, **_kwargs: False)

    with pytest.raises(DiamondDevError, match="divergent workflow branch"):
        orchestrator._run_initial_agents(context)  # noqa: SLF001


def test_run_gemini_comparison_overwrites_local_from_wiki(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    context.wiki.directory.mkdir()
    context.wiki.comparison_file.write_text("Wiki comparison\n", encoding="utf-8")
    context.comparison_file.write_text("Local comparison\n", encoding="utf-8")
    runner = _RecordingRunner()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)
    monkeypatch.setattr(orchestrator.git, "sync_wiki", lambda _wiki_dir: None)

    orchestrator._run_gemini_comparison(context)  # noqa: SLF001

    assert context.comparison_file.read_text(encoding="utf-8") == "Wiki comparison\n"
    assert not any(command[0] == "gemini" for command in runner.commands)


def test_run_gemini_comparison_promotes_local_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    context.wiki.directory.mkdir()
    context.comparison_file.write_text("Local comparison\n", encoding="utf-8")
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=_RecordingRunner())
    monkeypatch.setattr(orchestrator.git, "sync_wiki", lambda _wiki_dir: None)
    monkeypatch.setattr(orchestrator.git, "commit_if_changes", lambda *_args, **_kwargs: True)

    orchestrator._run_gemini_comparison(context)  # noqa: SLF001

    assert "- [ ] Accept: (codex/claude)" in context.wiki.comparison_file.read_text(
        encoding="utf-8",
    )


def test_prepare_wiki_with_plan_fails_on_plan_drift(tmp_path: Path) -> None:
    context = build_context(tmp_path)
    context.plan.path.write_text("# Source Plan\n", encoding="utf-8")
    context.wiki.directory.mkdir()
    (context.wiki.directory / ".git").mkdir()
    (context.wiki.directory / context.plan.file_name).write_text(
        "# Old Plan\n",
        encoding="utf-8",
    )
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=_RecordingRunner())

    with pytest.raises(DiamondDevError, match="Plan drift"):
        orchestrator._prepare_wiki_with_plan(context)  # noqa: SLF001


def test_prepare_wiki_with_plan_accepts_line_ending_differences(
    tmp_path: Path,
) -> None:
    context = build_context(tmp_path)
    context.plan.path.write_text("# Source Plan\n", encoding="utf-8")
    context.wiki.directory.mkdir()
    (context.wiki.directory / ".git").mkdir()
    wiki_plan = context.wiki.directory / context.plan.file_name
    wiki_plan.write_text(
        "# Source Plan\r\n",
        encoding="utf-8",
    )
    source_plan_bytes = context.plan.path.read_bytes()
    wiki_plan_bytes = wiki_plan.read_bytes()
    runner = _RecordingRunner()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)

    orchestrator._prepare_wiki_with_plan(context)  # noqa: SLF001

    assert context.plan.path.read_bytes() == source_plan_bytes
    assert wiki_plan.read_bytes() == wiki_plan_bytes
    assert [
        log_name
        for _command, _cwd, log_name in runner.command_calls
        if log_name.startswith("wiki-plan")
    ] == []


def test_ensure_agent_plan_copy_accepts_line_ending_differences(
    tmp_path: Path,
) -> None:
    context = build_context(tmp_path)
    context.plan.path.write_text("# Source Plan\n", encoding="utf-8")
    repo_dir = context.implementation.codex_dir
    repo_dir.mkdir()
    agent_plan = repo_dir / context.plan.file_name
    agent_plan.write_text(
        "# Source Plan\r\n",
        encoding="utf-8",
    )
    source_plan_bytes = context.plan.path.read_bytes()
    agent_plan_bytes = agent_plan.read_bytes()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=_RecordingRunner())

    orchestrator._ensure_agent_plan_copy(context, repo_dir)  # noqa: SLF001

    assert context.plan.path.read_bytes() == source_plan_bytes
    assert agent_plan.read_bytes() == agent_plan_bytes


def test_run_review_phases_promotes_local_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    selected_repo = context.implementation.codex_dir
    selected_repo.mkdir()
    context.wiki.directory.mkdir()
    selected = SelectedImplementation(
        accepted_agent="codex",
        opposite_agent="claude",
        repo_dir=selected_repo,
        branch=context.implementation.codex_branch,
    )
    review_file = selected_repo / context.plan.review_file_name
    review_file.write_text("Review\n", encoding="utf-8")
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=_RecordingRunner())
    fix_calls: list[Path] = []
    monkeypatch.setattr(orchestrator.git, "sync_wiki", lambda _wiki_dir: None)
    monkeypatch.setattr(orchestrator.git, "commit_if_changes", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        orchestrator,
        "_run_review_fixes",
        lambda _context, selected_implementation, _warnings: fix_calls.append(
            selected_implementation.repo_dir,
        ),
    )

    orchestrator._run_review_phases(context, selected, [])  # noqa: SLF001

    assert context.wiki.review_file.read_text(encoding="utf-8") == "Review\n"
    assert fix_calls == [selected_repo]


def test_run_review_phases_restores_wiki_review_and_runs_fixes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    selected_repo = context.implementation.codex_dir
    selected_repo.mkdir()
    context.wiki.directory.mkdir()
    context.wiki.review_file.write_text("Wiki Review\n", encoding="utf-8")
    selected = SelectedImplementation(
        accepted_agent="codex",
        opposite_agent="claude",
        repo_dir=selected_repo,
        branch=context.implementation.codex_branch,
    )
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=_RecordingRunner())
    fix_calls: list[Path] = []
    monkeypatch.setattr(orchestrator.git, "sync_wiki", lambda _wiki_dir: None)
    monkeypatch.setattr(
        orchestrator,
        "_run_review_fixes",
        lambda _context, selected_implementation, _warnings: fix_calls.append(
            selected_implementation.repo_dir,
        ),
    )

    orchestrator._run_review_phases(context, selected, [])  # noqa: SLF001

    assert (selected_repo / context.plan.review_file_name).read_text(
        encoding="utf-8",
    ) == "Wiki Review\n"
    assert fix_calls == [selected_repo]


def test_run_review_phases_skips_fixes_when_judgment_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    selected_repo = context.implementation.codex_dir
    selected_repo.mkdir()
    context.wiki.directory.mkdir()
    selected = SelectedImplementation(
        accepted_agent="codex",
        opposite_agent="claude",
        repo_dir=selected_repo,
        branch=context.implementation.codex_branch,
    )
    runner = _ReviewJudgmentFailureRunner()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)
    phase_warnings: list[PhaseWarning] = []
    promoted_reviews: list[Path] = []
    fix_calls: list[Path] = []
    monkeypatch.setattr(orchestrator.git, "sync_wiki", lambda _wiki_dir: None)
    monkeypatch.setattr(
        orchestrator,
        "_promote_review_file",
        lambda _context, review_file: promoted_reviews.append(review_file),
    )
    monkeypatch.setattr(
        orchestrator,
        "_run_review_fixes",
        lambda _context, selected_implementation, _warnings: fix_calls.append(
            selected_implementation.repo_dir,
        ),
    )

    orchestrator._run_review_phases(context, selected, phase_warnings)  # noqa: SLF001

    assert (selected_repo / context.plan.review_file_name).read_text(
        encoding="utf-8",
    ) == "Raw review\n"
    assert promoted_reviews == []
    assert fix_calls == []
    assert [(warning.phase, warning.status) for warning in phase_warnings] == [
        ("Codex review judgment", "failed"),
        ("Codex review fixes", "skipped"),
    ]


def test_run_review_phases_fails_on_local_wiki_review_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    selected_repo = context.implementation.codex_dir
    selected_repo.mkdir()
    context.wiki.directory.mkdir()
    context.wiki.review_file.write_text("Wiki Review\n", encoding="utf-8")
    (selected_repo / context.plan.review_file_name).write_text(
        "Local Review\n",
        encoding="utf-8",
    )
    selected = SelectedImplementation(
        accepted_agent="codex",
        opposite_agent="claude",
        repo_dir=selected_repo,
        branch=context.implementation.codex_branch,
    )
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=_RecordingRunner())
    monkeypatch.setattr(orchestrator.git, "sync_wiki", lambda _wiki_dir: None)

    with pytest.raises(DiamondDevError, match="differs from wiki review"):
        orchestrator._run_review_phases(context, selected, [])  # noqa: SLF001


def test_restore_or_validate_review_file_accepts_line_ending_differences(
    tmp_path: Path,
) -> None:
    context = build_context(tmp_path)
    selected_repo = context.implementation.codex_dir
    selected_repo.mkdir()
    context.wiki.directory.mkdir()
    context.wiki.review_file.write_text("Review\n", encoding="utf-8")
    review_file = selected_repo / context.plan.review_file_name
    review_file.write_text("Review\r\n", encoding="utf-8")
    wiki_review_bytes = context.wiki.review_file.read_bytes()
    local_review_bytes = review_file.read_bytes()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=_RecordingRunner())

    orchestrator._restore_or_validate_review_file(context, review_file)  # noqa: SLF001

    assert context.wiki.review_file.read_bytes() == wiki_review_bytes
    assert review_file.read_bytes() == local_review_bytes


def test_restore_or_validate_review_file_restores_wiki_sidecar(
    tmp_path: Path,
) -> None:
    context = build_context(tmp_path)
    selected_repo = context.implementation.codex_dir
    selected_repo.mkdir()
    context.wiki.directory.mkdir()
    context.wiki.review_file.write_text("Review\n", encoding="utf-8")
    context.wiki.review_judgments_file.write_text(
        _review_judgments_text(decision="fix"),
        encoding="utf-8",
    )
    review_file = selected_repo / context.plan.review_file_name
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=_RecordingRunner())

    orchestrator._restore_or_validate_review_file(context, review_file)  # noqa: SLF001

    assert review_file.read_text(encoding="utf-8") == "Review\n"
    assert (
        selected_repo / context.plan.review_judgments_file_name
    ).read_text(encoding="utf-8") == _review_judgments_text(decision="fix")


def test_restore_or_validate_review_file_fails_on_sidecar_conflict(
    tmp_path: Path,
) -> None:
    context = build_context(tmp_path)
    selected_repo = context.implementation.codex_dir
    selected_repo.mkdir()
    context.wiki.directory.mkdir()
    context.wiki.review_file.write_text("Review\n", encoding="utf-8")
    context.wiki.review_judgments_file.write_text(
        _review_judgments_text(decision="fix"),
        encoding="utf-8",
    )
    review_file = selected_repo / context.plan.review_file_name
    review_file.write_text("Review\n", encoding="utf-8")
    (selected_repo / context.plan.review_judgments_file_name).write_text(
        _review_judgments_text(decision="decline"),
        encoding="utf-8",
    )
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=_RecordingRunner())

    with pytest.raises(DiamondDevError, match="judgment sidecar"):
        orchestrator._restore_or_validate_review_file(context, review_file)  # noqa: SLF001


def test_promote_review_file_warns_only_when_sidecar_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    selected_repo = context.implementation.codex_dir
    selected_repo.mkdir()
    context.wiki.directory.mkdir()
    review_file = selected_repo / context.plan.review_file_name
    review_file.write_text("Review\n", encoding="utf-8")
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=_RecordingRunner())
    monkeypatch.setattr(orchestrator.git, "commit_if_changes", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(orchestrator.workflow_provider, "push_wiki", lambda *_args, **_kwargs: None)

    orchestrator._promote_review_file(context, review_file)  # noqa: SLF001

    assert context.wiki.review_file.read_text(encoding="utf-8") == "Review\n"
    assert not context.wiki.review_judgments_file.exists()


def test_promote_review_file_renders_and_copies_valid_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    selected_repo = context.implementation.codex_dir
    selected_repo.mkdir()
    context.wiki.directory.mkdir()
    review_file = selected_repo / context.plan.review_file_name
    review_file.write_text("Review\n", encoding="utf-8")
    (selected_repo / context.plan.review_judgments_file_name).write_text(
        _review_judgments_text(decision="fix"),
        encoding="utf-8",
    )
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=_RecordingRunner())
    monkeypatch.setattr(orchestrator.git, "commit_if_changes", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(orchestrator.workflow_provider, "push_wiki", lambda *_args, **_kwargs: None)

    orchestrator._promote_review_file(context, review_file)  # noqa: SLF001

    wiki_review = context.wiki.review_file.read_text(encoding="utf-8")
    assert "## Structured review judgments" in wiki_review
    assert "| CR-1 | fix | 0.80 | Valid finding. |" in wiki_review
    assert context.wiki.review_judgments_file.read_text(
        encoding="utf-8",
    ) == _canonical_review_judgments_text(decision="fix")


@pytest.mark.parametrize("pr_state", ("OPEN", "CLOSED", "MERGED"))
def test_finalize_pr_fails_when_existing_pr_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pr_state: str,
) -> None:
    context = build_context(tmp_path)
    selected_repo = context.implementation.codex_dir
    selected_repo.mkdir()
    selected = SelectedImplementation(
        accepted_agent="codex",
        opposite_agent="claude",
        repo_dir=selected_repo,
        branch=context.implementation.codex_branch,
    )
    runner = _RecordingRunner()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)

    def run_existing_pr(
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        assert isinstance(check, bool)
        assert command[:3] == ("gh", "pr", "list")
        return CommandResult(
            command=tuple(command),
            cwd=cwd,
            returncode=0,
            log_path=cwd / f"{log_name}.log",
            output=(
                f'[{{"number": 7, "state": "{pr_state}", '
                '"url": "https://github.com/owner/repo/pull/7"}]'
            ),
        )

    monkeypatch.setattr(runner, "run", run_existing_pr)

    with pytest.raises(DiamondDevError, match="Pull request already exists"):
        orchestrator._finalize_pr(context, selected, [])  # noqa: SLF001


def test_finalize_pr_edits_body_when_final_review_fails(tmp_path: Path) -> None:
    context = build_context(tmp_path)
    selected_repo = context.implementation.codex_dir
    selected_repo.mkdir()
    selected = SelectedImplementation(
        accepted_agent="codex",
        opposite_agent="claude",
        repo_dir=selected_repo,
        branch=context.implementation.codex_branch,
    )
    runner = _FinalReviewFailureRunner()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)
    phase_warnings: list[PhaseWarning] = []

    updated_context = orchestrator._finalize_pr(  # noqa: SLF001
        context,
        selected,
        phase_warnings,
    )

    assert updated_context.pr_url == "https://github.com/owner/repo/pull/123"
    assert [(warning.phase, warning.status) for warning in phase_warnings] == [
        ("final interactive Claude review", "failed"),
    ]
    pr_edit_command = next(
        command
        for command in runner.commands
        if command[:3] == ("gh", "pr", "edit")
    )
    pr_body = pr_edit_command[pr_edit_command.index("--body") + 1]
    assert "Workflow warnings:" in pr_body
    assert "final interactive Claude review (failed)" in pr_body


def test_finalize_pr_continues_when_warning_body_edit_fails(tmp_path: Path) -> None:
    context = build_context(tmp_path)
    selected_repo = context.implementation.codex_dir
    selected_repo.mkdir()
    selected = SelectedImplementation(
        accepted_agent="codex",
        opposite_agent="claude",
        repo_dir=selected_repo,
        branch=context.implementation.codex_branch,
    )
    runner = _FinalReviewAndBodyEditFailureRunner()
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)
    phase_warnings: list[PhaseWarning] = []

    updated_context = orchestrator._finalize_pr(  # noqa: SLF001
        context,
        selected,
        phase_warnings,
    )

    assert updated_context.pr_url == "https://github.com/owner/repo/pull/123"
    assert [(warning.phase, warning.status) for warning in phase_warnings] == [
        ("final interactive Claude review", "failed"),
    ]
    assert any(command[:3] == ("gh", "pr", "edit") for command in runner.commands)


def test_poll_acceptance_skips_missing_wiki_comparison_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_context(tmp_path)
    runner = _RecordingRunner()
    orchestrator = DiamondDevOrchestrator(
        cwd=tmp_path,
        runner=runner,
        sleep=lambda _seconds: None,
    )
    sync_calls: list[Path] = []

    def sync_wiki(wiki_dir: Path) -> None:
        sync_calls.append(wiki_dir)

    monkeypatch.setattr(orchestrator.git, "sync_wiki", sync_wiki)

    with pytest.raises(DiamondDevError, match="No valid acceptance"):
        orchestrator._poll_acceptance(context)  # noqa: SLF001

    assert sync_calls == [context.wiki.directory] * (
        len(acceptance_wait_delays()) + 1
    )


def test_commit_if_changes_skips_missing_untracked_paths(tmp_path: Path) -> None:
    runner = CommandRunner(tmp_path / "logs")
    runner.run(("git", "init"), cwd=tmp_path, log_name="git-init")
    orchestrator = DiamondDevOrchestrator(cwd=tmp_path, runner=runner)

    committed = orchestrator.git.commit_if_changes(
        tmp_path,
        message="cleanup",
        log_prefix="cleanup",
        paths=("missing.md",),
    )

    assert not committed


def test_finalize_pr_records_dirty_files_and_still_pushes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
        assert log_name == "final-selected-branch-dirty-status"
        return ("src/dirty.py",)

    monkeypatch.setattr(orchestrator.git, "commit_if_changes", no_commit)
    monkeypatch.setattr(orchestrator.git, "dirty_files", dirty_files)

    updated_context = orchestrator._finalize_pr(context, selected, [])  # noqa: SLF001

    assert not context.dirty_records
    assert updated_context.dirty_records[0].files == ("src/dirty.py",)
    assert updated_context.pr_url == "https://github.com/owner/repo/pull/123"
    assert ("git", "push", "-u", "origin", "codex/my-plan") in runner.commands
    assert any(command[:3] == ("gh", "pr", "create") for command in runner.commands)


def _review_judgments_text(*, decision: str) -> str:
    return _canonical_review_judgments_text(decision=decision)


def _canonical_review_judgments_text(*, decision: str) -> str:
    return f"{json.dumps(_review_judgments_payload(decision=decision), indent=2, sort_keys=True)}\n"


def _review_judgments_payload(*, decision: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "review_file": "my-plan-review.md",
        "review_provider": "coderabbit",
        "review_judge": "codex",
        "findings": [
            {
                "id": "CR-1",
                "decision": decision,
                "confidence": 0.8,
                "rationale": "Valid finding.",
            },
        ],
    }
