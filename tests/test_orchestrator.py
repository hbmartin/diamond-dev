"""Tests for orchestrator helper behavior."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from diamond_dev.acceptance import acceptance_wait_delays
from diamond_dev.config import DiamondDevConfig
from diamond_dev.errors import DiamondDevError
from diamond_dev.executor import (
    CommandLogRecord,
    CommandResult,
    CommandRunner,
    re_slug,
)
from diamond_dev.orchestrator import DiamondDevOrchestrator
from diamond_dev.pr import build_pr_body
from diamond_dev.workflow import (
    DirtyRecord,
    ImplementationContext,
    NotesContext,
    PlanContext,
    RunContext,
    SelectedImplementation,
)


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
        if command_tuple[:2] == ("git", "clone"):
            clone_dir = Path(command_tuple[-1])
            clone_dir.mkdir(parents=True, exist_ok=True)
            for lockfile_name in self.clone_lockfiles_by_dir.get(clone_dir, ()):
                (clone_dir / lockfile_name).write_text("", encoding="utf-8")
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

    def run(
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
            case ("git", "ls-remote", *_):
                returncode = 2
            case ("git", "diff", "--cached", *_):
                returncode = 1
            case ("git", "status", "--porcelain"):
                output = ""
            case ("git", "pull", *_):
                self._accept_comparison(cwd)
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
        notes=NotesContext(
            url="git@github.com:owner/repo.wiki.git",
            directory=tmp_path / "repo.wiki",
            comparison_file=tmp_path / "repo.wiki" / "my-plan-comparison.md",
            review_file=tmp_path / "repo.wiki" / "my-plan-review.md",
        ),
        implementation=ImplementationContext(
            codex_dir=tmp_path / "codex-my-plan",
            claude_dir=tmp_path / "claude-my-plan",
            codex_branch="codex/my-plan",
            claude_branch="claude/my-plan",
            base_branch="main",
        ),
        comparison_file=tmp_path / "comparison.md",
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

    def ensure_remote_branch_absent(repo_dir: Path, branch: str) -> None:
        assert repo_dir == context.implementation.codex_dir
        assert branch in {
            context.implementation.codex_branch,
            context.implementation.claude_branch,
        }

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
        "ensure_remote_branch_absent",
        ensure_remote_branch_absent,
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
        'notes_repository_url = "git@github.com:owner/repo.wiki.git"\n',
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
    assert log_labels.index("preflight-gh-auth") < log_labels.index("notes-clone")
    assert log_labels.index("notes-clone") < log_labels.index("codex-clone")
    assert log_labels.index("codex-clone") < log_labels.index("claude-clone")
    assert log_labels.index("claude-clone") < log_labels.index("gh-pr-create")
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
        "prepare implementation clones",
        "finalize pull request",
    }
    assert any(
        command_log["label"] == "gh-pr-create"
        for command_log in report["command_logs"]
    )


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

    def ensure_remote_branch_absent(repo_dir: Path, branch: str) -> None:
        checked_branches.append((repo_dir, branch))

    def checkout_branch(
        repo_dir: Path,
        *,
        branch: str,
        base_branch: str,
        log_prefix: str,
    ) -> None:
        checkout_calls.append((repo_dir, branch, base_branch, log_prefix))

    monkeypatch.setattr(orchestrator.git, "remote_default_branch", remote_default_branch)
    monkeypatch.setattr(
        orchestrator.git,
        "ensure_remote_branch_absent",
        ensure_remote_branch_absent,
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
    assert _install_calls(runner) == []
    assert (context.implementation.codex_dir / "My Plan.md").is_file()
    assert (context.implementation.claude_dir / "My Plan.md").is_file()


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


def test_poll_acceptance_skips_missing_notes_comparison_file(
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

    def sync_notes(notes_dir: Path) -> None:
        sync_calls.append(notes_dir)

    monkeypatch.setattr(orchestrator.git, "sync_notes", sync_notes)

    with pytest.raises(DiamondDevError, match="No valid acceptance"):
        orchestrator._poll_acceptance(context)  # noqa: SLF001

    assert sync_calls == [context.notes.directory] * len(acceptance_wait_delays())


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
        assert log_name == "final selected branch-dirty-status"
        return ("src/dirty.py",)

    monkeypatch.setattr(orchestrator.git, "commit_if_changes", no_commit)
    monkeypatch.setattr(orchestrator.git, "dirty_files", dirty_files)

    updated_context = orchestrator._finalize_pr(context, selected)  # noqa: SLF001

    assert not context.dirty_records
    assert updated_context.dirty_records[0].files == ("src/dirty.py",)
    assert updated_context.pr_url == "https://github.com/owner/repo/pull/123"
    assert ("git", "push", "-u", "origin", "codex/my-plan") in runner.commands
    assert any(command[:3] == ("gh", "pr", "create") for command in runner.commands)
