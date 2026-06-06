"""Diamond Dev workflow orchestration."""

# pylint: disable=too-many-lines

from __future__ import annotations

import shutil
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from loguru import logger

from diamond_dev import workflow
from diamond_dev.commands import (
    initial_implementation_prompt,
)
from diamond_dev.commit_pair import (
    build_commit_pair_entries,
    choose_commit_pair_slug,
    infer_commit_labels,
    resolve_commit_pair_inputs,
)
from diamond_dev.config import (
    load_config,
    read_prompt_file,
)
from diamond_dev.errors import CommandFailureError, DiamondDevError
from diamond_dev.executor import (
    CommandLogRecord,
    CommandResult,
    CommandRunner,
    ManagedProcess,
)
from diamond_dev.git_ops import GitOperations
from diamond_dev.markdown import read_normalized_markdown
from diamond_dev.naming import derive_wiki_repository_url, wiki_directory_name
from diamond_dev.notify import notify_url
from diamond_dev.orchestrator_acceptance import AcceptancePollingMixin
from diamond_dev.orchestrator_agents import (
    initial_agent_command,
    prompt_agent_command,
)
from diamond_dev.orchestrator_comparison import ComparisonPhasesMixin
from diamond_dev.orchestrator_pull_request import PullRequestFinalizationMixin
from diamond_dev.orchestrator_repositories import RepositoryPreparationMixin
from diamond_dev.orchestrator_review import ReviewPhasesMixin
from diamond_dev.preflight import PreflightSummary, run_preflight
from diamond_dev.providers import GitHubWorkflowProvider, ReviewProvider
from diamond_dev.report import (
    PhaseTiming,
    PhaseWarning,
    RunReport,
    RunReportTiming,
    RunReportWorkflow,
    RunStatus,
    write_run_report,
)

if TYPE_CHECKING:
    from diamond_dev.agents import AgentCapability
    from diamond_dev.config import DiamondDevConfig
    from diamond_dev.workflow import (
        ImplementationBranch,
        RunContext,
        SelectedImplementation,
    )

T = TypeVar("T")


@dataclass(slots=True)
class _RunState:
    """Mutable state captured while writing a run report."""

    # pylint: disable=too-many-instance-attributes

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_monotonic: float = field(default_factory=time.perf_counter)
    phase_timings: list[PhaseTiming] = field(default_factory=list)
    context: RunContext | None = None
    selected: SelectedImplementation | None = None
    preflight_summary: PreflightSummary | None = None
    phase_warnings: list[PhaseWarning] = field(default_factory=list)
    status: RunStatus = "failed"
    error: str | None = None


class DiamondDevOrchestrator(
    RepositoryPreparationMixin,
    ComparisonPhasesMixin,
    AcceptancePollingMixin,
    ReviewPhasesMixin,
    PullRequestFinalizationMixin,
):
    """Coordinate the full Diamond Dev multi-agent workflow."""

    # pylint: disable=too-many-instance-attributes

    def __init__(  # noqa: PLR0913
        self,
        *,
        cwd: Path | None = None,
        config_path: Path | None = None,
        report_path: Path | None = None,
        runner: CommandRunner | None = None,
        workflow_provider: GitHubWorkflowProvider | None = None,
        review_provider: ReviewProvider | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create an orchestrator."""
        self.cwd = cwd or Path.cwd()
        self.config_path = config_path
        self.runner = runner or CommandRunner(self.cwd / "logs")
        self.git = GitOperations(self.runner)
        self.workflow_provider = workflow_provider or GitHubWorkflowProvider(
            runner=self.runner,
            git=self.git,
        )
        self.review_provider = review_provider or ReviewProvider(runner=self.runner)
        self.report_path = report_path or _default_report_path(self.runner, self.cwd)
        self.sleep = sleep

    def run(self, plan_path: Path) -> int:
        """Run the Diamond Dev workflow for a markdown plan."""
        with self._reported_run() as run_state:
            resolved_plan_path = self._timed_phase(
                run_state.phase_timings,
                "resolve plan",
                lambda: workflow.resolve_plan_path(
                    cwd=self.cwd,
                    plan_path=plan_path,
                ),
            )
            config = self._timed_phase(
                run_state.phase_timings,
                "load config",
                lambda: load_config(self.cwd, self.config_path),
            )
            active_context = self._timed_phase(
                run_state.phase_timings,
                "build context",
                lambda: workflow.build_run_context(
                    cwd=self.cwd,
                    plan_path=resolved_plan_path,
                    config=config,
                ),
            )
            run_state.context = active_context
            run_state.preflight_summary = self._timed_phase(
                run_state.phase_timings,
                "preflight",
                lambda: run_preflight(
                    runner=self.runner,
                    cwd=self.cwd,
                    required_cli_names=active_context.config.required_cli_names(),
                ),
            )
            self._timed_phase(
                run_state.phase_timings,
                "prepare wiki",
                lambda: self._prepare_wiki_with_plan(active_context),
            )
            active_context = self._timed_phase(
                run_state.phase_timings,
                "prepare or resume implementation clones",
                lambda: self._prepare_implementation_clones(active_context),
            )
            run_state.context = active_context
            active_context = self._timed_phase(
                run_state.phase_timings,
                "complete initial agents",
                lambda: self._run_initial_agents(active_context),
            )
            run_state.context = active_context
            active_context, selected_implementation = self._run_pipeline(
                active_context,
                run_state.phase_timings,
                run_state.phase_warnings,
            )
            run_state.context = active_context
            run_state.selected = selected_implementation
        return 0

    def run_commits(
        self,
        commit_args: tuple[str, str],
    ) -> int:
        """Run the Diamond Dev workflow for two existing commits."""
        with self._reported_run() as run_state:
            config = self._timed_phase(
                run_state.phase_timings,
                "load config",
                lambda: load_config(self.cwd, self.config_path),
            )
            run_state.preflight_summary = self._timed_phase(
                run_state.phase_timings,
                "preflight",
                lambda: run_preflight(
                    runner=self.runner,
                    cwd=self.cwd,
                    required_cli_names=_commit_pair_required_cli_names(config),
                ),
            )
            resolved_inputs = self._timed_phase(
                run_state.phase_timings,
                "resolve commits",
                lambda: resolve_commit_pair_inputs(
                    cwd=self.cwd,
                    repository_url=config.repository_url,
                    runner=self.runner,
                    commit_args=commit_args,
                ),
            )
            wiki_url = config.wiki_repository_url or derive_wiki_repository_url(
                config.repository_url,
            )
            wiki_dir = self.cwd / wiki_directory_name(wiki_url)
            self._timed_phase(
                run_state.phase_timings,
                "prepare wiki",
                lambda: self._ensure_wiki_repo_at(
                    cwd=self.cwd,
                    wiki_url=wiki_url,
                    wiki_dir=wiki_dir,
                ),
            )
            slug = self._timed_phase(
                run_state.phase_timings,
                "resolve commit slug",
                lambda: choose_commit_pair_slug(
                    cwd=self.cwd,
                    wiki_dir=wiki_dir,
                    runner=self.runner,
                    resolved=resolved_inputs,
                ),
            )
            labels = infer_commit_labels(*resolved_inputs)
            entries = build_commit_pair_entries(
                resolved=resolved_inputs,
                labels=labels,
                slug=slug,
            )
            active_context = self._timed_phase(
                run_state.phase_timings,
                "build context",
                lambda: workflow.build_commit_pair_run_context(
                    cwd=self.cwd,
                    slug=slug,
                    entries=entries,
                    config=config,
                ),
            )
            run_state.context = active_context
            active_context = self._timed_phase(
                run_state.phase_timings,
                "prepare or resume implementation clones",
                lambda: self._prepare_commit_pair_clones(active_context),
            )
            run_state.context = active_context
            active_context, selected_implementation = self._run_pipeline(
                active_context,
                run_state.phase_timings,
                run_state.phase_warnings,
            )
            run_state.context = active_context
            run_state.selected = selected_implementation
        return 0

    def _run_pipeline(
        self,
        context: RunContext,
        phase_timings: list[PhaseTiming],
        phase_warnings: list[PhaseWarning],
    ) -> tuple[RunContext, SelectedImplementation]:
        """Run the shared post-setup workflow pipeline."""
        active_context = self._timed_phase(
            phase_timings,
            "prepare comparison",
            lambda: self._run_comparison_judgment(context),
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
        active_context = self._timed_phase(
            phase_timings,
            "run comparison implementation",
            lambda: self._run_comparison_fixer(
                active_context,
                selected_implementation,
                phase_warnings,
            ),
        )
        self._timed_phase(
            phase_timings,
            "run review phases",
            lambda: self._run_review_phases(
                active_context,
                selected_implementation,
                phase_warnings,
            ),
        )
        active_context = self._timed_phase(
            phase_timings,
            "finalize pull request",
            lambda: self._finalize_pr(
                active_context,
                selected_implementation,
                phase_warnings,
            ),
        )
        return active_context, selected_implementation

    @contextmanager
    def _reported_run(self) -> Generator[_RunState]:
        run_state = _RunState()
        try:
            yield run_state
        except (Exception, KeyboardInterrupt) as run_error:
            run_state.error = _phase_error_message(run_error)
            raise
        else:
            run_state.status = (
                "succeeded_with_warnings"
                if run_state.phase_warnings
                else "succeeded"
            )
        finally:
            self._write_report(
                RunReport(
                    path=self.report_path,
                    status=run_state.status,
                    timing=RunReportTiming(
                        started_at=run_state.started_at,
                        finished_at=datetime.now(UTC),
                        duration_seconds=(
                            time.perf_counter() - run_state.started_monotonic
                        ),
                        phase_timings=run_state.phase_timings,
                    ),
                    workflow=RunReportWorkflow(
                        context=run_state.context,
                        selected=run_state.selected,
                        preflight_summary=run_state.preflight_summary,
                    ),
                    command_logs=_command_log_records(self.runner),
                    phase_warnings=run_state.phase_warnings,
                    error=run_state.error,
                ),
            )

    def _timed_phase(
        self,
        phase_timings: list[PhaseTiming],
        name: str,
        action: Callable[[], T],
    ) -> T:
        phase_started = time.perf_counter()
        logger.bind(phase=name, phase_status="started").info(
            "Phase started: {}",
            name,
        )
        try:
            result = action()
        except (Exception, KeyboardInterrupt) as error:
            duration_seconds = time.perf_counter() - phase_started
            phase_timings.append(
                PhaseTiming(
                    name=name,
                    duration_seconds=duration_seconds,
                    status="failed",
                    error=_phase_error_message(error),
                    log_path=_phase_error_log_path(error),
                ),
            )
            logger.bind(
                phase=name,
                phase_status="failed",
                duration_seconds=duration_seconds,
            ).opt(exception=error).warning("Phase failed: {}", name)
            raise
        duration_seconds = time.perf_counter() - phase_started
        phase_timings.append(
            PhaseTiming(
                name=name,
                duration_seconds=duration_seconds,
                status="succeeded",
            ),
        )
        logger.bind(
            phase=name,
            phase_status="succeeded",
            duration_seconds=duration_seconds,
        ).info("Phase succeeded: {}", name)
        return result

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
        missing_agents: list[ImplementationBranch] = []
        completed_in_current_process = False
        active_context = context
        for agent_branch in context.implementation.branches:
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

        configured_prompt = read_prompt_file(
            context.config,
            context.config.prompts.initial_implementation_file,
            label="Initial implementation prompt",
        )
        prompt = initial_implementation_prompt(
            context.plan.file_name,
            configured_prompt,
        )
        processes: list[tuple[ImplementationBranch, ManagedProcess]] = []
        for agent_branch in missing_agents:
            self._ensure_agent_plan_copy(context, agent_branch.repo_dir)
            processes.append(
                (
                    agent_branch,
                    self.runner.start(
                        initial_agent_command(
                            context,
                            agent_branch,
                            prompt,
                        ),
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
                label=f"{agent_branch.agent_name} initial",
                repo_dir=agent_branch.repo_dir,
                branch=agent_branch.branch,
                log_prefix=f"{agent_branch.log_prefix}-initial",
            )
        notify_url(
            context.config.notifications.initial_implementation_url,
            label="initial implementation",
        )
        return active_context

    def _prepare_initial_branch(
        self,
        context: RunContext,
        agent_branch: ImplementationBranch,
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
                f"{agent_branch.agent_name} initial",
                agent_branch.repo_dir,
                agent_branch.branch,
                log_prefix=f"{agent_branch.log_prefix}-initial",
            )
            self.git.push_branch(
                agent_branch.repo_dir,
                agent_branch.branch,
                log_name=f"{agent_branch.log_prefix}-initial-push",
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

    def _run_agent(  # noqa: PLR0913
        self,
        *,
        context: RunContext,
        agent: str,
        repo_dir: Path,
        prompt: str,
        log_name: str,
        capability: AgentCapability,
    ) -> CommandResult:
        return self.runner.run(
            prompt_agent_command(
                context=context,
                agent_name=agent,
                repo_dir=repo_dir,
                prompt=prompt,
                capability=capability,
            ),
            cwd=repo_dir,
            log_name=log_name,
        )


def _default_report_path(runner: object, cwd: Path) -> Path:
    log_dir = getattr(runner, "log_dir", cwd / "logs")
    if isinstance(log_dir, Path):
        return log_dir / "run-report.json"
    return cwd / "logs" / "run-report.json"


def _phase_error_message(error: BaseException) -> str:
    message = str(error)
    if message:
        return message
    if isinstance(error, KeyboardInterrupt):
        return "Interrupted by user"
    return type(error).__name__


def _phase_error_log_path(error: BaseException) -> str | None:
    current_error: BaseException | None = error
    seen: set[int] = set()
    while current_error is not None:
        current_error_id = id(current_error)
        if current_error_id in seen:
            break
        seen.add(current_error_id)
        if isinstance(current_error, CommandFailureError):
            return current_error.log_path
        if current_error.__cause__ is not None:
            current_error = current_error.__cause__
        elif current_error.__suppress_context__:
            current_error = None
        else:
            current_error = current_error.__context__
    return None


def _command_log_records(runner: object) -> tuple[CommandLogRecord, ...]:
    command_logs = getattr(runner, "command_logs", ())
    if not isinstance(command_logs, list | tuple):
        return ()
    return tuple(
        command_log
        for command_log in command_logs
        if isinstance(command_log, CommandLogRecord)
    )


def _commit_pair_required_cli_names(config: DiamondDevConfig) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*config.required_cli_names(), "codex")))
