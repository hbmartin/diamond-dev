"""Diamond Dev workflow orchestration."""

# pylint: disable=too-many-lines

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from loguru import logger

from diamond_dev import pr, workflow
from diamond_dev.acceptance import (
    AgentChoice,
    acceptance_wait_delays,
    ensure_acceptance_checkbox,
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
    build_gh_pr_list_command,
    comparison_implementation_prompt,
    gemini_comparison_prompt,
    initial_implementation_prompt,
    review_fix_prompt,
    review_judgment_prompt,
)
from diamond_dev.config import load_config, read_gemini_prompt
from diamond_dev.errors import CommandFailureError, DiamondDevError
from diamond_dev.executor import (
    CommandLogRecord,
    CommandResult,
    CommandRunner,
    ManagedProcess,
)
from diamond_dev.git_ops import GitOperations
from diamond_dev.markdown import read_normalized_markdown
from diamond_dev.notify import notify_url
from diamond_dev.orchestrator_repositories import RepositoryPreparationMixin
from diamond_dev.preflight import PreflightSummary, run_preflight
from diamond_dev.report import (
    PhaseTiming,
    RunReport,
    RunReportTiming,
    RunReportWorkflow,
    RunStatus,
    write_run_report,
)

if TYPE_CHECKING:
    from diamond_dev.workflow import RunContext, SelectedImplementation

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class AgentBranch:
    """Resolved repository and branch details for one implementation agent."""

    agent: AgentChoice
    repo_dir: Path
    branch: str
    log_prefix: str


class DiamondDevOrchestrator(RepositoryPreparationMixin):
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

    def run(self, plan_path: Path) -> int:  # pylint: disable=too-many-locals
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
            preflight_summary = self._timed_phase(
                phase_timings,
                "preflight",
                lambda: run_preflight(runner=self.runner, cwd=self.cwd),
            )
            self._timed_phase(
                phase_timings,
                "prepare wiki",
                lambda: self._prepare_wiki_with_plan(active_context),
            )
            active_context = self._timed_phase(
                phase_timings,
                "prepare or resume implementation clones",
                lambda: self._prepare_implementation_clones(active_context),
            )
            context = active_context
            active_context = self._timed_phase(
                phase_timings,
                "complete initial agents",
                lambda: self._run_initial_agents(active_context),
            )
            context = active_context
            self._timed_phase(
                phase_timings,
                "prepare comparison",
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
                    timing=RunReportTiming(
                        started_at=started_at,
                        finished_at=datetime.now(UTC),
                        duration_seconds=time.perf_counter() - started_monotonic,
                        phase_timings=phase_timings,
                    ),
                    workflow=RunReportWorkflow(
                        context=context,
                        selected=selected,
                        preflight_summary=preflight_summary,
                    ),
                    command_logs=_command_log_records(self.runner),
                    error=error,
                ),
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

    def _run_initial_agents(  # pylint: disable=too-many-locals
        self,
        context: RunContext,
    ) -> RunContext:
        missing_agents: list[AgentBranch] = []
        completed_in_current_process = False
        active_context = context
        for agent_branch in _agent_branches(context):
            needs_agent, active_context, completed = self._prepare_initial_branch(
                active_context,
                agent_branch,
            )
            completed_in_current_process = completed_in_current_process or completed
            if needs_agent:
                missing_agents.append(agent_branch)

        if not missing_agents:
            if completed_in_current_process:
                notify_url(
                    context.config.notifications.initial_implementation_url,
                    label="initial implementation",
                )
            return active_context

        prompt = initial_implementation_prompt(context.plan.file_name)
        processes: list[tuple[AgentBranch, ManagedProcess]] = []
        for agent_branch in missing_agents:
            self._ensure_agent_plan_copy(context, agent_branch.repo_dir)
            processes.append(
                (
                    agent_branch,
                    self.runner.start(
                        _initial_agent_command(agent_branch, prompt),
                        cwd=agent_branch.repo_dir,
                        log_name=f"{agent_branch.log_prefix}-initial-agent",
                    ),
                ),
            )

        failures: list[CommandFailureError] = []
        for _agent_branch, managed_process in processes:
            try:
                managed_process.wait()
            except (CommandFailureError,) as error:
                failures.append(error)

        if failures:
            failure_summary = "; ".join(str(failure) for failure in failures)
            raise DiamondDevError(
                f"Initial agent implementation failed: {failure_summary}",
            )

        for agent_branch, _managed_process in processes:
            active_context = self.git.push_agent_branch(
                active_context,
                label=f"{agent_branch.agent} initial",
                repo_dir=agent_branch.repo_dir,
                branch=agent_branch.branch,
            )
        notify_url(
            context.config.notifications.initial_implementation_url,
            label="initial implementation",
        )
        return active_context

    def _prepare_initial_branch(
        self,
        context: RunContext,
        agent_branch: AgentBranch,
    ) -> tuple[bool, RunContext, bool]:
        if self.git.remote_branch_exists(
            agent_branch.repo_dir,
            agent_branch.branch,
            log_name=f"{agent_branch.log_prefix}-initial-remote-branch-exists",
        ):
            if not self.git.branches_match_remote(
                agent_branch.repo_dir,
                agent_branch.branch,
                log_prefix=f"{agent_branch.log_prefix}-initial",
            ):
                raise DiamondDevError(
                    "Cannot auto-resume divergent workflow branch: "
                    f"{agent_branch.branch}",
                )
            logger.info("Initial branch already pushed: {}", agent_branch.branch)
            return False, context, False

        branch_counts = self.git.branch_ahead_behind(
            agent_branch.repo_dir,
            branch=agent_branch.branch,
            base_branch=context.implementation.base_branch,
            log_name=f"{agent_branch.log_prefix}-initial-ahead-behind",
        )
        if branch_counts.ahead > 0:
            updated_context = self.git.record_dirty_files(
                context,
                f"{agent_branch.agent} initial",
                agent_branch.repo_dir,
                agent_branch.branch,
            )
            self.git.push_branch(
                agent_branch.repo_dir,
                agent_branch.branch,
                log_name=f"{agent_branch.agent} initial-push",
            )
            return False, updated_context, True

        logger.info("Initial branch needs agent run: {}", agent_branch.branch)
        return True, context, False

    def _ensure_agent_plan_copy(self, context: RunContext, repo_dir: Path) -> None:
        repo_plan = repo_dir / context.plan.file_name
        source_plan_markdown = read_normalized_markdown(context.plan.path)
        if repo_plan.is_file():
            repo_plan_markdown = read_normalized_markdown(repo_plan)
            if repo_plan_markdown != source_plan_markdown:
                raise DiamondDevError(
                    f"Plan drift detected for {repo_plan}; "
                    "the implementation clone copy differs from the source plan",
                )
            return
        shutil.copy2(context.plan.path, repo_plan)

    def _run_gemini_comparison(self, context: RunContext) -> None:
        self.git.sync_wiki(context.wiki.directory)
        if context.wiki.comparison_file.is_file():
            shutil.copy2(context.wiki.comparison_file, context.comparison_file)
            logger.info(
                "Using existing wiki comparison: {}",
                context.wiki.comparison_file,
            )
            return

        if context.comparison_file.is_file():
            self._promote_local_comparison(context)
            notify_url(context.config.notifications.comparison_url, label="comparison")
            return

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

        self._promote_local_comparison(context)
        notify_url(context.config.notifications.comparison_url, label="comparison")

    def _promote_local_comparison(self, context: RunContext) -> None:
        comparison_markdown = context.comparison_file.read_text(encoding="utf-8")
        context.comparison_file.write_text(
            ensure_acceptance_checkbox(comparison_markdown),
            encoding="utf-8",
        )
        shutil.copy2(context.comparison_file, context.wiki.comparison_file)
        self.git.commit_if_changes(
            context.wiki.directory,
            message=f"Add {context.plan.slug} comparison",
            log_prefix="wiki-comparison",
            paths=(context.wiki.comparison_file.name,),
        )
        self.git.run(
            context.wiki.directory,
            "push",
            log_name="wiki-comparison-push",
        )

    def _poll_acceptance(self, context: RunContext) -> AgentChoice:
        if accepted_agent := self._check_acceptance_once(context):
            return accepted_agent

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
            if accepted_agent := self._check_acceptance_once(context):
                return accepted_agent

        raise DiamondDevError("No valid acceptance found after polling window")

    def _check_acceptance_once(self, context: RunContext) -> AgentChoice | None:
        self.git.sync_wiki(context.wiki.directory)
        if not context.wiki.comparison_file.is_file():
            logger.warning(
                "Comparison file {} not found in wiki repository",
                context.wiki.comparison_file,
            )
            return None

        comparison_markdown = context.wiki.comparison_file.read_text(encoding="utf-8")
        if accepted_agent := parse_acceptance(comparison_markdown):
            logger.info("Accepted implementation: {}", accepted_agent)
            return accepted_agent
        logger.info("No accepted implementation found yet")
        return None

    def _run_opposite_agent(
        self,
        context: RunContext,
        selected: SelectedImplementation,
    ) -> RunContext:
        self.git.sync_wiki(context.wiki.directory)
        if context.wiki.review_file.is_file():
            logger.info(
                "Skipping comparison implementation because wiki review exists: {}",
                context.wiki.review_file,
            )
            return context

        plan_file = selected.repo_dir / context.plan.file_name
        plan_file.unlink(missing_ok=True)
        self.git.commit_if_changes(
            selected.repo_dir,
            message="Remove original plan artifact",
            log_prefix="selected-plan-cleanup",
            paths=(context.plan.file_name,),
        )

        comparison_file = selected.repo_dir / context.plan.comparison_file_name
        shutil.copy2(context.wiki.comparison_file, comparison_file)
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
        self.git.push_branch(
            selected.repo_dir,
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
        self.git.sync_wiki(context.wiki.directory)
        review_file = selected.repo_dir / context.plan.review_file_name
        if context.wiki.review_file.is_file():
            self._restore_or_validate_review_file(context, review_file)
            self._run_review_fixes(context, selected)
            return

        if review_file.is_file():
            self._promote_review_file(context, review_file)
            self._run_review_fixes(context, selected)
            return

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

        self._promote_review_file(context, review_file)
        self._run_review_fixes(context, selected)

    def _restore_or_validate_review_file(
        self,
        context: RunContext,
        review_file: Path,
    ) -> None:
        wiki_review_markdown = read_normalized_markdown(context.wiki.review_file)
        if review_file.is_file():
            local_review_markdown = read_normalized_markdown(review_file)
            if local_review_markdown != wiki_review_markdown:
                raise DiamondDevError(
                    "Local review file differs from wiki review file: "
                    f"{review_file}",
                )
            return
        shutil.copy2(context.wiki.review_file, review_file)

    def _promote_review_file(self, context: RunContext, review_file: Path) -> None:
        review_markdown = review_file.read_text(encoding="utf-8")
        if "(C)" in review_markdown:
            notify_url(
                context.config.notifications.review_input_needed_url,
                label="review input needed",
            )

        shutil.copy2(review_file, context.wiki.review_file)
        self.git.commit_if_changes(
            context.wiki.directory,
            message=f"Add {context.plan.slug} review",
            log_prefix="wiki-review",
            paths=(context.wiki.review_file.name,),
        )
        self.git.run(context.wiki.directory, "push", log_name="wiki-review-push")

    def _run_review_fixes(
        self,
        context: RunContext,
        selected: SelectedImplementation,
    ) -> None:
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
        self._ensure_no_existing_pr(selected)
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
        self.git.push_branch(
            selected.repo_dir,
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

    def _ensure_no_existing_pr(self, selected: SelectedImplementation) -> None:
        result = self.runner.run(
            build_gh_pr_list_command(selected.branch),
            cwd=selected.repo_dir,
            log_name="gh-pr-list-existing",
        )
        if existing_pr := pr.parse_existing_pull_request(result.output):
            raise DiamondDevError(
                "Pull request already exists for selected branch "
                f"{selected.branch}: #{existing_pr.number} {existing_pr.state} "
                f"{existing_pr.url}",
            )

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
            case _:
                raise ValueError(f"Unknown agent: {agent}")
        return self.runner.run(command, cwd=repo_dir, log_name=log_name)


def _agent_branches(context: RunContext) -> tuple[AgentBranch, AgentBranch]:
    return (
        AgentBranch(
            agent="codex",
            repo_dir=context.implementation.codex_dir,
            branch=context.implementation.codex_branch,
            log_prefix="codex",
        ),
        AgentBranch(
            agent="claude",
            repo_dir=context.implementation.claude_dir,
            branch=context.implementation.claude_branch,
            log_prefix="claude",
        ),
    )


def _initial_agent_command(agent_branch: AgentBranch, prompt: str) -> tuple[str, ...]:
    match agent_branch.agent:
        case "codex":
            return build_codex_command(agent_branch.repo_dir, prompt)
        case "claude":
            return build_claude_print_command(prompt)
        case _:
            raise ValueError(f"Unknown agent: {agent_branch.agent}")


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
