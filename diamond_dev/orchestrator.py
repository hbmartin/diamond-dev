"""Diamond Dev workflow orchestration."""

# pylint: disable=too-many-lines

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
    acceptance_wait_delays,
    ensure_acceptance_checkbox,
    parse_acceptance,
)
from diamond_dev.agents import AgentAdapter, AgentCapability, resolve_adapter
from diamond_dev.commands import (
    ComparisonBranchContext,
    ComparisonPromptContext,
    comparison_implementation_prompt,
    gemini_comparison_prompt,
    initial_implementation_prompt,
    review_fix_prompt,
    review_judgment_prompt,
)
from diamond_dev.comparison_bundle import write_comparison_bundle
from diamond_dev.config import (
    load_config,
    read_comparison_judgment_prompt,
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
from diamond_dev.notify import notify_url
from diamond_dev.orchestrator_repositories import RepositoryPreparationMixin
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
from diamond_dev.review_judgments import (
    ReviewJudgmentStatus,
    canonical_review_judgments_json,
    canonical_review_judgments_payload,
    read_review_judgments_status,
    summarize_review_judgments,
    upsert_structured_judgments_section,
)

if TYPE_CHECKING:
    from diamond_dev.workflow import (
        ImplementationBranch,
        RunContext,
        SelectedImplementation,
    )

T = TypeVar("T")


class DiamondDevOrchestrator(RepositoryPreparationMixin):
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

    def run(self, plan_path: Path) -> int:  # pylint: disable=too-many-locals
        """Run the Diamond Dev workflow for a markdown plan."""
        started_at = datetime.now(UTC)
        started_monotonic = time.perf_counter()
        phase_timings: list[PhaseTiming] = []
        context: RunContext | None = None
        selected: SelectedImplementation | None = None
        preflight_summary: PreflightSummary | None = None
        phase_warnings: list[PhaseWarning] = []
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
                lambda: run_preflight(
                    runner=self.runner,
                    cwd=self.cwd,
                    required_cli_names=active_context.config.required_cli_names(),
                ),
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
            active_context = self._timed_phase(
                phase_timings,
                "prepare comparison",
                lambda: self._run_comparison_judgment(active_context),
            )
            context = active_context
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
                lambda: self._run_comparison_fixer(
                    active_context,
                    selected_implementation,
                    phase_warnings,
                ),
            )
            context = active_context
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
            context = active_context
            status = "succeeded_with_warnings" if phase_warnings else "succeeded"
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
                    phase_warnings=phase_warnings,
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
                        _initial_agent_command(
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

    def _run_comparison_judgment(self, context: RunContext) -> RunContext:
        self.workflow_provider.sync_wiki(context.wiki.directory)
        if context.wiki.comparison_file.is_file():
            shutil.copy2(context.wiki.comparison_file, context.comparison_file)
            if context.wiki.comparison_bundle_file.is_file():
                shutil.copy2(
                    context.wiki.comparison_bundle_file,
                    context.comparison_bundle_file,
                )
            logger.info(
                "Using existing wiki comparison: {}",
                context.wiki.comparison_file,
            )
            return context

        if context.comparison_file.is_file():
            self._promote_local_comparison(context)
            notify_url(context.config.notifications.comparison_url, label="comparison")
            return context

        active_context = self._prepare_comparison_bundle(context)
        configured_prompt = read_comparison_judgment_prompt(context.config)
        prompt = gemini_comparison_prompt(
            configured_prompt,
            ComparisonPromptContext(
                base_branch=active_context.implementation.base_branch,
                comparison_bundle_file_name=active_context.plan.comparison_bundle_file_name,
                branches=tuple(
                    ComparisonBranchContext(
                        agent_name=branch.agent_name,
                        branch=branch.branch,
                        repo_dir=branch.repo_dir,
                    )
                    for branch in active_context.implementation.branches
                ),
            ),
        )
        comparison_judge = active_context.config.workflow.comparison_judge
        log_name = f"{comparison_judge}-comparison"
        self.runner.run(
            _prompt_agent_command(
                active_context,
                comparison_judge,
                active_context.cwd,
                prompt,
                capability="comparison_judge",
            ),
            cwd=active_context.cwd,
            log_name=log_name,
        )
        if not active_context.comparison_file.is_file():
            raise DiamondDevError(
                f"Comparison judge {comparison_judge} did not write comparison.md",
            )

        self._promote_local_comparison(active_context)
        notify_url(
            active_context.config.notifications.comparison_url,
            label="comparison",
        )
        return active_context

    def _run_gemini_comparison(self, context: RunContext) -> RunContext:
        """Run the configured comparison judgment phase."""
        return self._run_comparison_judgment(context)

    def _prepare_comparison_bundle(self, context: RunContext) -> RunContext:
        active_context = write_comparison_bundle(
            context=context,
            runner=self.runner,
            git=self.git,
        )
        shutil.copy2(
            active_context.comparison_bundle_file,
            active_context.wiki.comparison_bundle_file,
        )
        self.git.commit_if_changes(
            active_context.wiki.directory,
            message=f"Add {active_context.plan.slug} comparison bundle",
            log_prefix="wiki-comparison-bundle",
            paths=(active_context.wiki.comparison_bundle_file.name,),
        )
        self.workflow_provider.push_wiki(
            active_context.wiki.directory,
            log_name="wiki-comparison-bundle-push",
        )
        return active_context

    def _promote_local_comparison(self, context: RunContext) -> None:
        comparison_markdown = context.comparison_file.read_text(encoding="utf-8")
        context.comparison_file.write_text(
            ensure_acceptance_checkbox(
                comparison_markdown,
                context.implementation.implementer_names,
            ),
            encoding="utf-8",
        )
        shutil.copy2(context.comparison_file, context.wiki.comparison_file)
        paths = [context.wiki.comparison_file.name]
        if context.comparison_bundle_file.is_file():
            shutil.copy2(
                context.comparison_bundle_file,
                context.wiki.comparison_bundle_file,
            )
            paths.append(context.wiki.comparison_bundle_file.name)
        self.git.commit_if_changes(
            context.wiki.directory,
            message=f"Add {context.plan.slug} comparison",
            log_prefix="wiki-comparison",
            paths=tuple(paths),
        )
        self.workflow_provider.push_wiki(
            context.wiki.directory,
            log_name="wiki-comparison-push",
        )

    def _poll_acceptance(self, context: RunContext) -> str:
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

    def _check_acceptance_once(self, context: RunContext) -> str | None:
        self.workflow_provider.sync_wiki(context.wiki.directory)
        if not context.wiki.comparison_file.is_file():
            logger.warning(
                "Comparison file {} not found in wiki repository",
                context.wiki.comparison_file,
            )
            return None

        comparison_markdown = context.wiki.comparison_file.read_text(encoding="utf-8")
        if accepted_agent := parse_acceptance(
            comparison_markdown,
            context.implementation.implementer_names,
        ):
            logger.info("Accepted implementation: {}", accepted_agent)
            return accepted_agent
        logger.info("No accepted implementation found yet")
        return None

    def _run_comparison_fixer(
        self,
        context: RunContext,
        selected: SelectedImplementation,
        phase_warnings: list[PhaseWarning],
    ) -> RunContext:
        self.workflow_provider.sync_wiki(context.wiki.directory)
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

        configured_prompt = read_prompt_file(
            context.config,
            context.config.prompts.comparison_implementation_file,
            label="Comparison implementation prompt",
        )
        prompt = comparison_implementation_prompt(
            context.plan.comparison_file_name,
            configured_prompt,
        )
        log_name = f"{selected.comparison_fixer}-comparison-agent"
        try:
            self._run_agent(
                context=context,
                agent=selected.comparison_fixer,
                repo_dir=selected.repo_dir,
                prompt=prompt,
                log_name=log_name,
                capability="comparison_fixer",
            )
        except (CommandFailureError,) as error:
            logger.opt(exception=error).warning(
                "Comparison fixer failed; continuing where possible: {}",
                error,
            )
            phase_warnings.append(
                PhaseWarning(
                    phase="comparison fixer implementation",
                    status="failed",
                    message=(
                        f"{selected.comparison_fixer} failed while applying "
                        "comparison follow-up."
                    ),
                    error=str(error),
                    log_name=log_name,
                ),
            )

        context = self.git.push_agent_branch(
            context,
            label=f"{selected.comparison_fixer} comparison",
            repo_dir=selected.repo_dir,
            branch=selected.branch,
            log_prefix=f"{selected.comparison_fixer}-comparison",
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

    def _run_opposite_agent(
        self,
        context: RunContext,
        selected: SelectedImplementation,
        phase_warnings: list[PhaseWarning],
    ) -> RunContext:
        """Run the configured comparison fixer phase."""
        return self._run_comparison_fixer(context, selected, phase_warnings)

    def _run_review_phases(
        self,
        context: RunContext,
        selected: SelectedImplementation,
        phase_warnings: list[PhaseWarning],
    ) -> None:
        self.workflow_provider.sync_wiki(context.wiki.directory)
        review_file = selected.repo_dir / context.plan.review_file_name
        if context.wiki.review_file.is_file():
            self._restore_or_validate_review_file(context, review_file)
            self._run_review_fixes(context, selected, phase_warnings)
            return

        if review_file.is_file():
            self._promote_review_file(context, review_file)
            self._run_review_fixes(context, selected, phase_warnings)
            return

        review_provider = context.config.workflow.review_provider
        review_provider_label = _agent_label(review_provider)
        review_judge_label = _agent_label(context.config.workflow.review_judge)
        review_fixer_label = _agent_label(context.config.workflow.review_fixer)
        review_provider_log_name = f"{review_provider}-review"
        try:
            self.review_provider.run_review(
                _review_provider_command(context),
                repo_dir=selected.repo_dir,
                log_name=review_provider_log_name,
                output_path=review_file,
            )
        except (CommandFailureError,) as error:
            logger.opt(exception=error).warning(
                "Review provider failed; continuing where possible: {}",
                error,
            )
            phase_warnings.extend(
                (
                    PhaseWarning(
                        phase=f"{review_provider_label} review",
                        status="failed",
                        message=(
                            f"{review_provider_label} review failed; no review file "
                            "was produced."
                        ),
                        error=str(error),
                        log_name=review_provider_log_name,
                    ),
                    PhaseWarning(
                        phase=f"{review_judge_label} review judgment",
                        status="skipped",
                        message=(
                            f"Skipped because {review_provider_label} review failed."
                        ),
                        error=None,
                        log_name=None,
                    ),
                    PhaseWarning(
                        phase=f"{review_fixer_label} review fixes",
                        status="skipped",
                        message=(
                            f"Skipped because {review_provider_label} review failed."
                        ),
                        error=None,
                        log_name=None,
                    ),
                ),
            )
            return

        configured_judgment_prompt = read_prompt_file(
            context.config,
            context.config.prompts.review_judgment_file,
            label="Review judgment prompt",
        )
        review_judge = context.config.workflow.review_judge
        review_judge_label = _agent_label(review_judge)
        review_fixer_label = _agent_label(context.config.workflow.review_fixer)
        judgment_log_name = f"{review_judge}-review-judgment"
        try:
            self._run_agent(
                context=context,
                agent=review_judge,
                repo_dir=selected.repo_dir,
                prompt=review_judgment_prompt(
                    context.plan.review_file_name,
                    context.plan.review_judgments_file_name,
                    context.config.workflow.review_provider,
                    context.config.workflow.review_judge,
                    configured_judgment_prompt,
                ),
                log_name=judgment_log_name,
                capability="review_judge",
            )
        except (CommandFailureError,) as error:
            logger.opt(exception=error).warning(
                "Review judgment failed; continuing: {}",
                error,
            )
            phase_warnings.extend(
                (
                    PhaseWarning(
                        phase=f"{review_judge_label} review judgment",
                        status="failed",
                        message=(
                            f"{review_judge_label} failed while judging review "
                            "findings."
                        ),
                        error=str(error),
                        log_name=judgment_log_name,
                    ),
                    PhaseWarning(
                        phase=f"{review_fixer_label} review fixes",
                        status="skipped",
                        message=(
                            f"Skipped because {review_judge_label} review judgment "
                            "failed."
                        ),
                        error=None,
                        log_name=None,
                    ),
                ),
            )
            return

        self._promote_review_file(context, review_file)
        self._run_review_fixes(context, selected, phase_warnings)

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
            self._restore_or_validate_review_judgments(
                context,
                review_file.parent / context.plan.review_judgments_file_name,
            )
            return
        shutil.copy2(context.wiki.review_file, review_file)
        self._restore_or_validate_review_judgments(
            context,
            review_file.parent / context.plan.review_judgments_file_name,
        )

    def _promote_review_file(self, context: RunContext, review_file: Path) -> None:
        review_markdown = review_file.read_text(encoding="utf-8")
        review_judgments_file = (
            review_file.parent / context.plan.review_judgments_file_name
        )
        judgment_status = read_review_judgments_status(review_judgments_file)
        needs_input_from_sidecar = False
        paths = [context.wiki.review_file.name]
        if judgment_status.status == "valid" and judgment_status.judgments is not None:
            review_markdown = upsert_structured_judgments_section(
                review_markdown,
                judgment_status.judgments,
            )
            review_file.write_text(review_markdown, encoding="utf-8")
            review_judgments_file.write_text(
                canonical_review_judgments_json(judgment_status.judgments),
                encoding="utf-8",
            )
            shutil.copy2(review_judgments_file, context.wiki.review_judgments_file)
            paths.append(context.wiki.review_judgments_file.name)
            needs_input_from_sidecar = (
                summarize_review_judgments(judgment_status.judgments).needs_input > 0
            )
        elif judgment_status.status == "invalid":
            logger.warning(
                "Ignoring invalid structured review judgment sidecar {}: {}",
                judgment_status.path,
                judgment_status.error,
            )
        else:
            logger.warning(
                "Structured review judgment sidecar missing: {}",
                judgment_status.path,
            )

        if "(C)" in review_markdown or needs_input_from_sidecar:
            notify_url(
                context.config.notifications.review_input_needed_url,
                label="review input needed",
            )

        shutil.copy2(review_file, context.wiki.review_file)
        self.git.commit_if_changes(
            context.wiki.directory,
            message=f"Add {context.plan.slug} review",
            log_prefix="wiki-review",
            paths=tuple(paths),
        )
        self.workflow_provider.push_wiki(
            context.wiki.directory,
            log_name="wiki-review-push",
        )

    def _restore_or_validate_review_judgments(
        self,
        context: RunContext,
        local_review_judgments_file: Path,
    ) -> None:
        wiki_status = read_review_judgments_status(context.wiki.review_judgments_file)
        local_status = read_review_judgments_status(local_review_judgments_file)
        if wiki_status.status == "valid" and wiki_status.judgments is not None:
            if _valid_review_judgments_differ(local_status, wiki_status):
                raise DiamondDevError(
                    "Local review judgment sidecar differs from wiki review "
                    f"judgment sidecar: {local_review_judgments_file}",
                )
            shutil.copy2(
                context.wiki.review_judgments_file,
                local_review_judgments_file,
            )
            return
        if wiki_status.status == "invalid":
            logger.warning(
                "Ignoring invalid wiki review judgment sidecar {}: {}",
                wiki_status.path,
                wiki_status.error,
            )
        if local_status.status == "invalid":
            logger.warning(
                "Ignoring invalid local review judgment sidecar {}: {}",
                local_status.path,
                local_status.error,
            )

    def _run_review_fixes(
        self,
        context: RunContext,
        selected: SelectedImplementation,
        phase_warnings: list[PhaseWarning],
    ) -> None:
        configured_prompt = read_prompt_file(
            context.config,
            context.config.prompts.review_fix_file,
            label="Review fix prompt",
        )
        review_fixer = context.config.workflow.review_fixer
        review_fixer_label = _agent_label(review_fixer)
        log_name = f"{review_fixer}-review-fixes"
        try:
            self._run_agent(
                context=context,
                agent=review_fixer,
                repo_dir=selected.repo_dir,
                prompt=review_fix_prompt(
                    context.plan.review_file_name,
                    context.plan.review_judgments_file_name,
                    configured_prompt,
                ),
                log_name=log_name,
                capability="review_fixer",
            )
        except (CommandFailureError,) as error:
            logger.opt(exception=error).warning(
                "Review fixes failed; continuing: {}",
                error,
            )
            phase_warnings.append(
                PhaseWarning(
                    phase=f"{review_fixer_label} review fixes",
                    status="failed",
                    message=(
                        f"{review_fixer_label} failed while applying accepted review "
                        "fixes."
                    ),
                    error=str(error),
                    log_name=log_name,
                ),
            )

    def _finalize_pr(
        self,
        context: RunContext,
        selected: SelectedImplementation,
        phase_warnings: list[PhaseWarning],
    ) -> RunContext:
        self._ensure_no_existing_pr(selected)
        for artifact_name in (
            context.plan.file_name,
            context.plan.comparison_file_name,
            context.plan.comparison_bundle_file_name,
            context.plan.review_file_name,
            context.plan.review_judgments_file_name,
        ):
            (selected.repo_dir / artifact_name).unlink(missing_ok=True)

        self.git.commit_if_changes(
            selected.repo_dir,
            message="post work cleanup",
            log_prefix="post-work-cleanup",
            paths=(
                context.plan.file_name,
                context.plan.comparison_file_name,
                context.plan.comparison_bundle_file_name,
                context.plan.review_file_name,
                context.plan.review_judgments_file_name,
            ),
        )
        context = self.git.record_dirty_files(
            context,
            "final selected branch",
            selected.repo_dir,
            selected.branch,
            log_prefix="final-selected-branch",
        )
        self.git.push_branch(
            selected.repo_dir,
            selected.branch,
            log_name="final-push",
        )

        pr_title = f"Implement {context.plan.path.stem}"
        pr_body = pr.build_pr_body(context, selected, warnings=phase_warnings)
        created_pr = self.workflow_provider.create_pull_request(
            selected,
            base_branch=context.implementation.base_branch,
            title=pr_title,
            body=pr_body,
        )
        pr_url = created_pr.url
        pr_number = created_pr.number
        notify_url(context.config.notifications.open_pr_url, label="open pr")
        context = context.with_pr_url(pr_url)

        final_reviewer = context.config.workflow.final_reviewer
        final_reviewer_label = _agent_label(final_reviewer)
        final_review_log_name = f"{final_reviewer}-final-review"
        try:
            self.runner.run_interactive(
                _final_review_command(
                    context,
                    pr_number,
                ),
                cwd=selected.repo_dir,
                log_name=final_review_log_name,
            )
        except (CommandFailureError,) as error:
            logger.opt(exception=error).warning(
                "Final interactive review failed: {}",
                error,
            )
            phase_warnings.append(
                PhaseWarning(
                    phase=f"final interactive {final_reviewer_label} review",
                    status="failed",
                    message=(
                        f"Final interactive {final_reviewer_label} review failed "
                        "after PR creation."
                    ),
                    error=str(error),
                    log_name=final_review_log_name,
                ),
            )
            try:
                self.workflow_provider.edit_pull_request_body(
                    selected.repo_dir,
                    pr_url=pr_url,
                    body=pr.build_pr_body(
                        context,
                        selected,
                        warnings=phase_warnings,
                    ),
                )
            except (CommandFailureError,) as edit_error:
                logger.opt(exception=edit_error).warning(
                    "Failed to edit PR body with workflow warnings: {}",
                    edit_error,
                )
        return context

    def _ensure_no_existing_pr(self, selected: SelectedImplementation) -> None:
        if existing_pr := self.workflow_provider.existing_pull_request(selected):
            raise DiamondDevError(
                "Pull request already exists for selected branch "
                f"{selected.branch}: #{existing_pr.number} {existing_pr.state} "
                f"{existing_pr.url}",
            )

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
            _prompt_agent_command(
                context=context,
                agent_name=agent,
                repo_dir=repo_dir,
                prompt=prompt,
                capability=capability,
            ),
            cwd=repo_dir,
            log_name=log_name,
        )


def _initial_agent_command(
    context: RunContext,
    agent_branch: ImplementationBranch,
    prompt: str,
) -> tuple[str, ...]:
    return _prompt_agent_command(
        context=context,
        agent_name=agent_branch.agent_name,
        repo_dir=agent_branch.repo_dir,
        prompt=prompt,
        capability="implementation",
    )


def _prompt_agent_command(
    context: RunContext,
    agent_name: str,
    repo_dir: Path,
    prompt: str,
    *,
    capability: AgentCapability,
) -> tuple[str, ...]:
    adapter = _agent_adapter(context, agent_name)
    return adapter.prompt_command(
        repo_dir,
        prompt,
        model=_agent_model(context, agent_name),
        capability=capability,
    )


def _review_provider_command(context: RunContext) -> tuple[str, ...]:
    agent_name = context.config.workflow.review_provider
    adapter = _agent_adapter(context, agent_name)
    return adapter.review_command(
        context.implementation.base_branch,
        model=_agent_model(context, agent_name),
    )


def _final_review_command(context: RunContext, pr_number: str) -> tuple[str, ...]:
    agent_name = context.config.workflow.final_reviewer
    adapter = _agent_adapter(context, agent_name)
    return adapter.interactive_review_command(
        pr_number,
        model=_agent_model(context, agent_name),
    )


def _agent_adapter(context: RunContext, agent_name: str) -> AgentAdapter:
    adapter_name = context.config.agent_adapter_name(agent_name)
    return resolve_adapter(adapter_name)


def _agent_model(context: RunContext, agent_name: str) -> str | None:
    return context.config.agent_config(agent_name).model


def _agent_label(agent_name: str) -> str:
    return {
        "codex": "Codex",
        "claude": "Claude",
        "coderabbit": "CodeRabbit",
        "gemini": "Gemini",
    }.get(agent_name, agent_name)


def _valid_review_judgments_differ(
    local_status: ReviewJudgmentStatus,
    wiki_status: ReviewJudgmentStatus,
) -> bool:
    if (
        local_status.status != "valid"
        or local_status.judgments is None
        or wiki_status.status != "valid"
        or wiki_status.judgments is None
    ):
        return False
    return canonical_review_judgments_payload(
        local_status.judgments,
    ) != canonical_review_judgments_payload(wiki_status.judgments)


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
