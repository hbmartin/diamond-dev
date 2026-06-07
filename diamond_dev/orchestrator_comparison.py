"""Comparison judgment and fixer phases for Diamond Dev orchestration."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from loguru import logger

from diamond_dev.acceptance import ensure_acceptance_checkbox
from diamond_dev.commands import (
    ComparisonBranchContext,
    ComparisonPromptContext,
    comparison_implementation_prompt,
    gemini_comparison_prompt,
)
from diamond_dev.commit_pair import (
    comparison_has_matching_commit_pair_marker,
    ensure_commit_pair_marker,
    upsert_commit_pair_index,
)
from diamond_dev.comparison_bundle import write_comparison_bundle
from diamond_dev.config import read_comparison_judgment_prompt, read_prompt_file
from diamond_dev.errors import CommandFailureError, DiamondDevError
from diamond_dev.notify import notify_url
from diamond_dev.report import PhaseWarning
from diamond_dev.workflow import (
    LOCAL_COMPARISON_FILE_NAME,
    copy_generated_child_file,
    read_generated_child_text,
    safe_child_path,
    safe_generated_child_path,
    write_generated_child_text,
)

if TYPE_CHECKING:
    from pathlib import Path

    from diamond_dev.agents import AgentCapability
    from diamond_dev.executor import CommandResult, CommandRunnerLike
    from diamond_dev.git_ops import GitOperations
    from diamond_dev.providers import GitHubWorkflowProvider
    from diamond_dev.workflow import RunContext, SelectedImplementation


class ComparisonPhasesMixin:
    """Run comparison judgment and selected-branch follow-up phases."""

    cwd: Path
    git: GitOperations
    runner: CommandRunnerLike
    workflow_provider: GitHubWorkflowProvider

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
        raise NotImplementedError

    def _run_comparison_judgment(self, context: RunContext) -> RunContext:
        self.workflow_provider.sync_wiki(context.wiki.directory)
        if context.wiki.comparison_file.is_file():
            wiki_comparison_markdown = context.wiki.comparison_file.read_text(
                encoding="utf-8",
            )
            if not comparison_has_matching_commit_pair_marker(
                wiki_comparison_markdown,
                context,
            ):
                logger.warning(
                    "Ignoring wiki comparison without matching commit-pair marker: {}",
                    context.wiki.comparison_file,
                )
            else:
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
            local_comparison_markdown = context.comparison_file.read_text(
                encoding="utf-8",
            )
            if not comparison_has_matching_commit_pair_marker(
                local_comparison_markdown,
                context,
            ):
                logger.warning(
                    "Ignoring local comparison without matching commit-pair marker: {}",
                    context.comparison_file,
                )
                context.comparison_file.unlink()
            else:
                self._promote_local_comparison(context)
                notify_url(
                    context.config.notifications.comparison_url,
                    label="comparison",
                )
                return context

        active_context = self._prepare_comparison_bundle(context)
        configured_prompt = read_comparison_judgment_prompt(context.config)
        prompt = gemini_comparison_prompt(
            configured_prompt,
            ComparisonPromptContext(
                base_branch=active_context.implementation.base_branch,
                comparison_bundle_file_name=(
                    active_context.plan.comparison_bundle_file_name
                ),
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
        self._run_agent(
            context=active_context,
            agent=comparison_judge,
            repo_dir=active_context.cwd,
            prompt=prompt,
            log_name=log_name,
            capability="comparison_judge",
        )
        if not active_context.comparison_file.is_file():
            raise DiamondDevError(
                f"Comparison judge {comparison_judge} did not write "
                f"{LOCAL_COMPARISON_FILE_NAME}",
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
        comparison_markdown = read_generated_child_text(
            context.cwd,
            LOCAL_COMPARISON_FILE_NAME,
        )
        comparison_markdown = ensure_commit_pair_marker(comparison_markdown, context)
        write_generated_child_text(
            context.cwd,
            LOCAL_COMPARISON_FILE_NAME,
            ensure_acceptance_checkbox(
                comparison_markdown,
                context.implementation.implementer_names,
            ),
        )
        copy_generated_child_file(
            source_dir=context.cwd,
            source_name=LOCAL_COMPARISON_FILE_NAME,
            destination_dir=context.wiki.directory,
            destination_name=context.wiki.comparison_file.name,
        )
        paths = [context.wiki.comparison_file.name]
        comparison_bundle_file = context.comparison_bundle_file
        if comparison_bundle_file.is_file():
            copy_generated_child_file(
                source_dir=context.cwd,
                source_name=context.plan.comparison_bundle_file_name,
                destination_dir=context.wiki.directory,
                destination_name=context.wiki.comparison_bundle_file.name,
            )
            paths.append(context.wiki.comparison_bundle_file.name)
        if context.commit_pair is not None and upsert_commit_pair_index(
            context.wiki.directory,
            context.commit_pair,
        ):
            paths.append("diamond-dev-commit-comparisons.md")
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

        plan_file = safe_child_path(selected.repo_dir, context.plan.file_name)
        plan_file.unlink(missing_ok=True)
        self.git.commit_if_changes(
            selected.repo_dir,
            message="Remove original plan artifact",
            log_prefix="selected-plan-cleanup",
            paths=(context.plan.file_name,),
        )

        comparison_file = safe_generated_child_path(
            selected.repo_dir,
            context.plan.comparison_file_name,
        )
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
