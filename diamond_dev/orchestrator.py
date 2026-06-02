"""Diamond Dev workflow orchestration."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from loguru import logger

from diamond_dev import pr, workflow
from diamond_dev.acceptance import (
    AgentChoice,
    acceptance_wait_delays,
    append_acceptance_checkbox,
    parse_acceptance,
)
from diamond_dev.commands import (
    ComparisonPromptContext,
    build_claude_interactive_review_command,
    build_claude_print_command,
    build_coderabbit_review_command,
    build_codex_command,
    build_gemini_command,
    build_gh_pr_create_command,
    build_pnpm_install_command,
    build_uv_sync_command,
    comparison_implementation_prompt,
    gemini_comparison_prompt,
    initial_implementation_prompt,
    review_fix_prompt,
    review_judgment_prompt,
)
from diamond_dev.config import load_config, read_gemini_prompt
from diamond_dev.errors import CommandFailureError, DiamondDevError
from diamond_dev.executor import CommandLogRecord, CommandResult, CommandRunner
from diamond_dev.git_ops import GitOperations
from diamond_dev.notify import notify_url
from diamond_dev.preflight import PreflightSummary, run_preflight
from diamond_dev.report import PhaseTiming, RunReport, RunStatus, write_run_report

if TYPE_CHECKING:
    from diamond_dev.workflow import RunContext, SelectedImplementation

T = TypeVar("T")


class DiamondDevOrchestrator:
    """Coordinate the full Diamond Dev multi-agent workflow."""

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        config_path: Path | None = None,
        report_path: Path | None = None,
        runner: CommandRunner | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create an orchestrator."""
        self.cwd = cwd or Path.cwd()
        self.config_path = config_path
        self.runner = runner or CommandRunner(self.cwd / "logs")
        self.git = GitOperations(self.runner)
        self.report_path = report_path or _default_report_path(self.runner, self.cwd)
        self.sleep = sleep

    def run(self, plan_path: Path) -> int:
        """Run the Diamond Dev workflow for a markdown plan."""
        started_at = datetime.now(UTC)
        started_monotonic = time.perf_counter()
        phase_timings: list[PhaseTiming] = []
        context: RunContext | None = None
        selected: SelectedImplementation | None = None
        preflight_summary: PreflightSummary | None = None
        status: RunStatus = "failed"
        error: str | None = None

        try:
            resolved_plan_path = self._timed_phase(
                phase_timings,
                "resolve plan",
                lambda: workflow.resolve_plan_path(
                    cwd=self.cwd,
                    plan_path=plan_path,
                ),
            )
            config = self._timed_phase(
                phase_timings,
                "load config",
                lambda: load_config(self.cwd, self.config_path),
            )
            active_context = self._timed_phase(
                phase_timings,
                "build context",
                lambda: workflow.build_run_context(
                    cwd=self.cwd,
                    plan_path=resolved_plan_path,
                    config=config,
                ),
            )
            context = active_context
            self._timed_phase(
                phase_timings,
                "validate initial state",
                lambda: self._validate_initial_state(active_context),
            )
            preflight_summary = self._timed_phase(
                phase_timings,
                "preflight",
                lambda: run_preflight(runner=self.runner, cwd=self.cwd),
            )
            self._timed_phase(
                phase_timings,
                "prepare notes",
                lambda: self._prepare_notes_with_plan(active_context),
            )
            active_context = self._timed_phase(
                phase_timings,
                "prepare implementation clones",
                lambda: self._prepare_implementation_clones(active_context),
            )
            context = active_context
            active_context = self._timed_phase(
                phase_timings,
                "run initial agents",
                lambda: self._run_initial_agents(active_context),
            )
            context = active_context
            self._timed_phase(
                phase_timings,
                "run gemini comparison",
                lambda: self._run_gemini_comparison(active_context),
            )
            accepted_agent = self._timed_phase(
                phase_timings,
                "poll acceptance",
                lambda: self._poll_acceptance(active_context),
            )
            selected_implementation = workflow.selected_implementation(
                active_context,
                accepted_agent,
            )
            selected = selected_implementation
            active_context = self._timed_phase(
                phase_timings,
                "run comparison implementation",
                lambda: self._run_opposite_agent(
                    active_context,
                    selected_implementation,
                ),
            )
            context = active_context
            self._timed_phase(
                phase_timings,
                "run review phases",
                lambda: self._run_review_phases(
                    active_context,
                    selected_implementation,
                ),
            )
            active_context = self._timed_phase(
                phase_timings,
                "finalize pull request",
                lambda: self._finalize_pr(active_context, selected_implementation),
            )
            context = active_context
            status = "succeeded"
        except (Exception,) as run_error:
            error = str(run_error)
            raise
        else:
            return 0
        finally:
            self._write_report(
                RunReport(
                    path=self.report_path,
                    status=status,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    duration_seconds=time.perf_counter() - started_monotonic,
                    phase_timings=phase_timings,
                    context=context,
                    selected=selected,
                    preflight_summary=preflight_summary,
                    command_logs=_command_log_records(self.runner),
                    error=error,
                ),
            )

    def _validate_initial_state(self, context: RunContext) -> None:
        for clone_dir in (
            context.implementation.codex_dir,
            context.implementation.claude_dir,
        ):
            if clone_dir.exists():
                raise DiamondDevError(
                    f"Implementation clone already exists: {clone_dir}",
                )

    def _timed_phase(
        self,
        phase_timings: list[PhaseTiming],
        name: str,
        action: Callable[[], T],
    ) -> T:
        phase_started = time.perf_counter()
        try:
            return action()
        finally:
            phase_timings.append(
                PhaseTiming(
                    name=name,
                    duration_seconds=time.perf_counter() - phase_started,
                ),
            )

    def _write_report(self, report: RunReport) -> None:
        try:
            write_run_report(report)
        except (OSError, TypeError, ValueError) as report_error:
            logger.warning(
                "Could not write run report {}: {}",
                self.report_path,
                report_error,
            )

    def _prepare_notes_with_plan(self, context: RunContext) -> None:
        self._ensure_notes_repo(context)
        shutil.copy2(
            context.plan.path,
            context.notes.directory / context.plan.file_name,
        )
        self.git.commit_if_changes(
            context.notes.directory,
            message=f"Add {context.plan.file_name} plan",
            log_prefix="notes-plan",
            paths=(context.plan.file_name,),
        )
        self.git.run(context.notes.directory, "push", log_name="notes-plan-push")

    def _ensure_notes_repo(self, context: RunContext) -> None:
        if context.notes.directory.exists():
            if not (context.notes.directory / ".git").is_dir():
                raise DiamondDevError(
                    f"Existing notes path is not a Git repo: {context.notes.directory}",
                )
            self.git.sync_notes(context.notes.directory)
            return

        self.runner.run(
            ("git", "clone", context.notes.url, str(context.notes.directory)),
            cwd=context.cwd,
            log_name="notes-clone",
        )

    def _prepare_implementation_clones(self, context: RunContext) -> RunContext:
        implementation = context.implementation
        self.runner.run(
            (
                "git",
                "clone",
                context.config.repository_url,
                str(implementation.codex_dir),
            ),
            cwd=context.cwd,
            log_name="codex-clone",
        )
        implementation = implementation.with_base_branch(
            self.git.remote_default_branch(implementation.codex_dir),
        )
        self.git.ensure_remote_branch_absent(
            implementation.codex_dir,
            implementation.codex_branch,
        )
        self.git.ensure_remote_branch_absent(
            implementation.codex_dir,
            implementation.claude_branch,
        )

        self.runner.run(
            (
                "git",
                "clone",
                context.config.repository_url,
                str(implementation.claude_dir),
            ),
            cwd=context.cwd,
            log_name="claude-clone",
        )
        self.git.checkout_branch(
            implementation.codex_dir,
            branch=implementation.codex_branch,
            base_branch=implementation.base_branch,
            log_prefix="codex",
        )
        self.git.checkout_branch(
            implementation.claude_dir,
            branch=implementation.claude_branch,
            base_branch=implementation.base_branch,
            log_prefix="claude",
        )
        self._install_packages(implementation.codex_dir, log_prefix="codex")
        self._install_packages(implementation.claude_dir, log_prefix="claude")

        for repo_dir in (
            implementation.codex_dir,
            implementation.claude_dir,
        ):
            shutil.copy2(context.plan.path, repo_dir / context.plan.file_name)
        return context.with_implementation(implementation)

    def _install_packages(self, repo_dir: Path, *, log_prefix: str) -> None:
        supported_lockfile_found = False
        if (repo_dir / "uv.lock").is_file():
            supported_lockfile_found = True
            self.runner.run(
                build_uv_sync_command(),
                cwd=repo_dir,
                log_name=f"{log_prefix}-uv-sync",
            )
        if (repo_dir / "pnpm-lock.yaml").is_file():
            supported_lockfile_found = True
            self.runner.run(
                build_pnpm_install_command(),
                cwd=repo_dir,
                log_name=f"{log_prefix}-pnpm-install",
            )
        if not supported_lockfile_found:
            logger.info(
                "No supported package lockfile found in {}; skipping install",
                repo_dir,
            )

    def _run_initial_agents(self, context: RunContext) -> RunContext:
        prompt = initial_implementation_prompt(context.plan.file_name)
        codex_process = self.runner.start(
            build_codex_command(context.implementation.codex_dir, prompt),
            cwd=context.implementation.codex_dir,
            log_name="codex-initial-agent",
        )
        claude_process = self.runner.start(
            build_claude_print_command(prompt),
            cwd=context.implementation.claude_dir,
            log_name="claude-initial-agent",
        )
        notify_url(
            context.config.notifications.initial_implementation_url,
            label="initial implementation",
        )

        failures: list[CommandFailureError] = []
        for managed_process in (codex_process, claude_process):
            try:
                managed_process.wait()
            except (CommandFailureError,) as error:
                failures.append(error)

        if failures:
            failure_summary = "; ".join(str(failure) for failure in failures)
            raise DiamondDevError(
                f"Initial agent implementation failed: {failure_summary}",
            )

        context = self.git.push_agent_branch(
            context,
            label="codex initial",
            repo_dir=context.implementation.codex_dir,
            branch=context.implementation.codex_branch,
        )
        return self.git.push_agent_branch(
            context,
            label="claude initial",
            repo_dir=context.implementation.claude_dir,
            branch=context.implementation.claude_branch,
        )

    def _run_gemini_comparison(self, context: RunContext) -> None:
        context.comparison_file.unlink(missing_ok=True)
        configured_prompt = read_gemini_prompt(context.config)
        prompt = gemini_comparison_prompt(
            configured_prompt,
            ComparisonPromptContext(
                base_branch=context.implementation.base_branch,
                codex_branch=context.implementation.codex_branch,
                claude_branch=context.implementation.claude_branch,
                codex_dir=context.implementation.codex_dir,
                claude_dir=context.implementation.claude_dir,
            ),
        )
        self.runner.run(
            build_gemini_command(prompt),
            cwd=context.cwd,
            log_name="gemini-comparison",
        )
        if not context.comparison_file.is_file():
            raise DiamondDevError("Gemini did not write comparison.md")

        comparison_markdown = context.comparison_file.read_text(encoding="utf-8")
        context.comparison_file.write_text(
            append_acceptance_checkbox(comparison_markdown),
            encoding="utf-8",
        )
        shutil.copy2(context.comparison_file, context.notes.comparison_file)
        self.git.commit_if_changes(
            context.notes.directory,
            message=f"Add {context.plan.slug} comparison",
            log_prefix="notes-comparison",
            paths=(context.notes.comparison_file.name,),
        )
        self.git.run(
            context.notes.directory,
            "push",
            log_name="notes-comparison-push",
        )
        notify_url(context.config.notifications.comparison_url, label="comparison")

    def _poll_acceptance(self, context: RunContext) -> AgentChoice:
        for attempt_number, delay_seconds in enumerate(
            acceptance_wait_delays(),
            start=1,
        ):
            logger.info(
                "Waiting {} seconds before acceptance check {}",
                delay_seconds,
                attempt_number,
            )
            self.sleep(delay_seconds)
            self.git.sync_notes(context.notes.directory)
            if not context.notes.comparison_file.is_file():
                logger.warning(
                    "Comparison file {} not found in notes repository",
                    context.notes.comparison_file,
                )
                continue

            comparison_markdown = context.notes.comparison_file.read_text(
                encoding="utf-8",
            )
            if accepted_agent := parse_acceptance(comparison_markdown):
                logger.info("Accepted implementation: {}", accepted_agent)
                return accepted_agent
            logger.info("No accepted implementation found yet")

        raise DiamondDevError("No valid acceptance found after polling window")

    def _run_opposite_agent(
        self,
        context: RunContext,
        selected: SelectedImplementation,
    ) -> RunContext:
        plan_file = selected.repo_dir / context.plan.file_name
        plan_file.unlink(missing_ok=True)
        self.git.commit_if_changes(
            selected.repo_dir,
            message="Remove original plan artifact",
            log_prefix="selected-plan-cleanup",
            paths=(context.plan.file_name,),
        )

        comparison_file = selected.repo_dir / context.plan.comparison_file_name
        shutil.copy2(context.notes.comparison_file, comparison_file)
        self.git.run(selected.repo_dir, "fetch", log_name="selected-fetch")

        prompt = comparison_implementation_prompt(context.plan.comparison_file_name)
        try:
            self._run_agent(
                agent=selected.opposite_agent,
                repo_dir=selected.repo_dir,
                prompt=prompt,
                log_name=f"{selected.opposite_agent}-comparison-agent",
            )
        except (CommandFailureError,) as error:
            logger.opt(exception=error).warning(
                "Opposite agent failed; continuing where possible: {}",
                error,
            )

        context = self.git.push_agent_branch(
            context,
            label=f"{selected.opposite_agent} comparison",
            repo_dir=selected.repo_dir,
            branch=selected.branch,
        )

        comparison_file.unlink(missing_ok=True)
        self.git.commit_if_changes(
            selected.repo_dir,
            message="Remove comparison artifact",
            log_prefix="selected-comparison-cleanup",
            paths=(context.plan.comparison_file_name,),
        )
        self.git.run(
            selected.repo_dir,
            "push",
            "-u",
            "origin",
            selected.branch,
            log_name="selected-comparison-cleanup-push",
        )
        notify_url(
            context.config.notifications.comparison_implementation_url,
            label="comparison implementation",
        )
        return context

    def _run_review_phases(
        self,
        context: RunContext,
        selected: SelectedImplementation,
    ) -> None:
        review_file = selected.repo_dir / context.plan.review_file_name
        try:
            self.runner.run_to_file(
                build_coderabbit_review_command(context.implementation.base_branch),
                cwd=selected.repo_dir,
                log_name="coderabbit-review",
                output_path=review_file,
            )
        except (CommandFailureError,) as error:
            logger.opt(exception=error).warning(
                "CodeRabbit review failed; continuing where possible: {}",
                error,
            )
            return

        try:
            self.runner.run(
                build_codex_command(
                    selected.repo_dir,
                    review_judgment_prompt(context.plan.review_file_name),
                ),
                cwd=selected.repo_dir,
                log_name="codex-review-judgment",
            )
        except (CommandFailureError,) as error:
            logger.opt(exception=error).warning(
                "Codex review judgment failed; continuing: {}",
                error,
            )

        review_markdown = review_file.read_text(encoding="utf-8")
        if "(C)" in review_markdown:
            notify_url(
                context.config.notifications.review_input_needed_url,
                label="review input needed",
            )

        shutil.copy2(review_file, context.notes.review_file)
        self.git.commit_if_changes(
            context.notes.directory,
            message=f"Add {context.plan.slug} review",
            log_prefix="notes-review",
            paths=(context.notes.review_file.name,),
        )
        self.git.run(context.notes.directory, "push", log_name="notes-review-push")

        try:
            self.runner.run(
                build_codex_command(
                    selected.repo_dir,
                    review_fix_prompt(context.plan.review_file_name),
                ),
                cwd=selected.repo_dir,
                log_name="codex-review-fixes",
            )
        except (CommandFailureError,) as error:
            logger.opt(exception=error).warning(
                "Codex review fixes failed; continuing: {}",
                error,
            )

    def _finalize_pr(
        self,
        context: RunContext,
        selected: SelectedImplementation,
    ) -> RunContext:
        for artifact_name in (
            context.plan.file_name,
            context.plan.comparison_file_name,
            context.plan.review_file_name,
        ):
            (selected.repo_dir / artifact_name).unlink(missing_ok=True)

        self.git.commit_if_changes(
            selected.repo_dir,
            message="post work cleanup",
            log_prefix="post-work-cleanup",
            paths=(
                context.plan.file_name,
                context.plan.comparison_file_name,
                context.plan.review_file_name,
            ),
        )
        context = self.git.record_dirty_files(
            context,
            "final selected branch",
            selected.repo_dir,
            selected.branch,
        )
        self.git.run(
            selected.repo_dir,
            "push",
            "-u",
            "origin",
            selected.branch,
            log_name="final-push",
        )

        pr_title = f"Implement {context.plan.path.stem}"
        pr_body = pr.build_pr_body(context, selected)
        pr_result = self.runner.run(
            build_gh_pr_create_command(
                base_branch=context.implementation.base_branch,
                head_branch=selected.branch,
                title=pr_title,
                body=pr_body,
            ),
            cwd=selected.repo_dir,
            log_name="gh-pr-create",
        )
        pr_url = pr.parse_pr_url(pr_result.output)
        pr_number = pr.parse_pr_number(pr_result.output)
        notify_url(context.config.notifications.open_pr_url, label="open pr")

        try:
            self.runner.run_interactive(
                build_claude_interactive_review_command(pr_number),
                cwd=selected.repo_dir,
                log_name="claude-final-review",
            )
        except (CommandFailureError,) as error:
            logger.opt(exception=error).warning(
                "Final interactive Claude review failed: {}",
                error,
            )
        return context.with_pr_url(pr_url)

    def _run_agent(
        self,
        *,
        agent: AgentChoice,
        repo_dir: Path,
        prompt: str,
        log_name: str,
    ) -> CommandResult:
        match agent:
            case "codex":
                command = build_codex_command(repo_dir, prompt)
            case "claude":
                command = build_claude_print_command(prompt)
        return self.runner.run(command, cwd=repo_dir, log_name=log_name)


def _default_report_path(runner: object, cwd: Path) -> Path:
    log_dir = getattr(runner, "log_dir", cwd / "logs")
    if isinstance(log_dir, Path):
        return log_dir / "run-report.json"
    return cwd / "logs" / "run-report.json"


def _command_log_records(runner: object) -> tuple[CommandLogRecord, ...]:
    command_logs = getattr(runner, "command_logs", ())
    if not isinstance(command_logs, list | tuple):
        return ()
    return tuple(
        command_log
        for command_log in command_logs
        if isinstance(command_log, CommandLogRecord)
    )
