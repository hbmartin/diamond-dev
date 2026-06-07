"""Review phases for Diamond Dev orchestration."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from diamond_dev.commands import review_fix_prompt, review_judgment_prompt
from diamond_dev.config import read_prompt_file
from diamond_dev.errors import CommandFailureError, DiamondDevError
from diamond_dev.markdown import read_normalized_markdown
from diamond_dev.notify import notify_url
from diamond_dev.orchestrator_agents import agent_label, review_provider_command
from diamond_dev.report import PhaseWarning
from diamond_dev.review_judgments import (
    ReviewJudgmentStatus,
    canonical_review_judgments_json,
    canonical_review_judgments_payload,
    read_review_judgments_status,
    summarize_review_judgments,
    upsert_structured_judgments_section,
)
from diamond_dev.workflow import (
    copy_generated_child_file,
    safe_generated_child_path,
    write_generated_child_text,
)

if TYPE_CHECKING:
    from diamond_dev.agents import AgentCapability
    from diamond_dev.executor import CommandResult
    from diamond_dev.git_ops import GitOperations
    from diamond_dev.providers import GitHubWorkflowProvider, ReviewProvider
    from diamond_dev.workflow import RunContext, SelectedImplementation


class ReviewPhasesMixin:
    """Run review provider, review judgment, and review fixes."""

    git: GitOperations
    review_provider: ReviewProvider
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

    def _run_review_phases(
        self,
        context: RunContext,
        selected: SelectedImplementation,
        phase_warnings: list[PhaseWarning],
    ) -> None:
        self.workflow_provider.sync_wiki(context.wiki.directory)
        review_file = safe_generated_child_path(
            selected.repo_dir,
            context.plan.review_file_name,
        )
        if context.wiki.review_file.is_file():
            self._restore_or_validate_review_file(context, review_file)
            self._run_review_fixes(context, selected, phase_warnings)
            return

        if review_file.is_file():
            self._promote_review_file(context, review_file)
            self._run_review_fixes(context, selected, phase_warnings)
            return

        review_provider = context.config.workflow.review_provider
        review_provider_label = agent_label(review_provider)
        review_judge_label = agent_label(context.config.workflow.review_judge)
        review_fixer_label = agent_label(context.config.workflow.review_fixer)
        review_provider_log_name = f"{review_provider}-review"
        try:
            self.review_provider.run_review(
                review_provider_command(context),
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
        review_judge_label = agent_label(review_judge)
        review_fixer_label = agent_label(context.config.workflow.review_fixer)
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
                safe_generated_child_path(
                    review_file.parent,
                    context.plan.review_judgments_file_name,
                ),
            )
            return
        shutil.copy2(context.wiki.review_file, review_file)
        self._restore_or_validate_review_judgments(
            context,
            safe_generated_child_path(
                review_file.parent,
                context.plan.review_judgments_file_name,
            ),
        )

    def _promote_review_file(self, context: RunContext, review_file: Path) -> None:
        if review_file.name != context.plan.review_file_name:
            raise DiamondDevError(f"Unexpected review file: {review_file}")
        review_file = safe_generated_child_path(
            review_file.parent,
            context.plan.review_file_name,
        )
        review_markdown = review_file.read_text(encoding="utf-8")
        review_judgments_file = safe_generated_child_path(
            review_file.parent,
            context.plan.review_judgments_file_name,
        )
        judgment_status = read_review_judgments_status(review_judgments_file)
        needs_input_from_sidecar = False
        paths = [context.wiki.review_file.name]
        if judgment_status.status == "valid" and judgment_status.judgments is not None:
            review_markdown = upsert_structured_judgments_section(
                review_markdown,
                judgment_status.judgments,
            )
            write_generated_child_text(
                review_file.parent,
                context.plan.review_file_name,
                review_markdown,
            )
            write_generated_child_text(
                review_file.parent,
                context.plan.review_judgments_file_name,
                canonical_review_judgments_json(judgment_status.judgments),
            )
            copy_generated_child_file(
                source_dir=review_file.parent,
                source_name=context.plan.review_judgments_file_name,
                destination_dir=context.wiki.directory,
                destination_name=context.wiki.review_judgments_file.name,
            )
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

        copy_generated_child_file(
            source_dir=review_file.parent,
            source_name=context.plan.review_file_name,
            destination_dir=context.wiki.directory,
            destination_name=context.wiki.review_file.name,
        )
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
        review_fixer_label = agent_label(review_fixer)
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
