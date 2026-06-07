"""Pull request finalization phase for Diamond Dev orchestration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from diamond_dev import pr
from diamond_dev.errors import CommandFailureError, DiamondDevError
from diamond_dev.notify import notify_url
from diamond_dev.orchestrator_agents import agent_label, final_review_command
from diamond_dev.report import PhaseWarning

if TYPE_CHECKING:
    from diamond_dev.executor import CommandRunnerLike
    from diamond_dev.git_ops import GitOperations
    from diamond_dev.providers import GitHubWorkflowProvider
    from diamond_dev.workflow import RunContext, SelectedImplementation


class PullRequestFinalizationMixin:
    """Finalize the selected implementation branch and open a pull request."""

    git: GitOperations
    runner: CommandRunnerLike
    workflow_provider: GitHubWorkflowProvider

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

        pr_title = (
            f"Compare {selected.branch}"
            if context.commit_pair is not None
            else f"Implement {context.plan.path.stem}"
        )
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
        final_reviewer_label = agent_label(final_reviewer)
        final_review_log_name = f"{final_reviewer}-final-review"
        try:
            self.runner.run_interactive(
                final_review_command(
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
