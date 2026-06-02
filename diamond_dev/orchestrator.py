"""Diamond Dev workflow orchestration."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path

from loguru import logger

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
    comparison_implementation_prompt,
    gemini_comparison_prompt,
    initial_implementation_prompt,
    review_fix_prompt,
    review_judgment_prompt,
)
from diamond_dev.config import load_config, read_gemini_prompt
from diamond_dev.errors import CommandFailureError, DiamondDevError
from diamond_dev.executor import CommandResult, CommandRunner
from diamond_dev.git_ops import GitOperations
from diamond_dev.notify import notify_url
from diamond_dev.pr import build_pr_body, parse_pr_number
from diamond_dev.workflow import (
    DirtyRecord,
    ImplementationContext,
    NotesContext,
    PlanContext,
    RunContext,
    SelectedImplementation,
    build_run_context,
    resolve_plan_path,
    selected_implementation,
)

__all__ = (
    "DiamondDevOrchestrator", "DirtyRecord", "ImplementationContext",
    "NotesContext", "PlanContext", "RunContext", "SelectedImplementation",
    "build_pr_body",
)


class DiamondDevOrchestrator:
    """Coordinate the full Diamond Dev multi-agent workflow."""

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        runner: CommandRunner | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create an orchestrator."""
        self.cwd = cwd or Path.cwd()
        self.runner = runner or CommandRunner(self.cwd / "logs")
        self.git = GitOperations(self.runner)
        self.sleep = sleep

    def run(self, plan_path: Path) -> int:
        """Run the Diamond Dev workflow for a markdown plan."""
        resolved_plan_path = resolve_plan_path(cwd=self.cwd, plan_path=plan_path)
        config = load_config(self.cwd)
        context = build_run_context(
            cwd=self.cwd,
            plan_path=resolved_plan_path,
            config=config,
        )

        self._validate_initial_state(context)
        self._prepare_notes_with_plan(context)
        self._prepare_implementation_clones(context)
        self._run_initial_agents(context)
        self._run_gemini_comparison(context)
        accepted_agent = self._poll_acceptance(context)
        selected = selected_implementation(context, accepted_agent)
        self._run_opposite_agent(context, selected)
        self._run_review_phases(context, selected)
        self._finalize_pr(context, selected)
        return 0

    def _validate_initial_state(self, context: RunContext) -> None:
        for clone_dir in (
            context.implementation.codex_dir,
            context.implementation.claude_dir,
        ):
            if clone_dir.exists():
                raise DiamondDevError(
                    f"Implementation clone already exists: {clone_dir}",
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

    def _prepare_implementation_clones(self, context: RunContext) -> None:
        self.runner.run(
            (
                "git",
                "clone",
                context.config.repository_url,
                str(context.implementation.codex_dir),
            ),
            cwd=context.cwd,
            log_name="codex-clone",
        )
        context.implementation.base_branch = self.git.remote_default_branch(
            context.implementation.codex_dir,
        )
        self.git.ensure_remote_branch_absent(
            context.implementation.codex_dir,
            context.implementation.codex_branch,
        )
        self.git.ensure_remote_branch_absent(
            context.implementation.codex_dir,
            context.implementation.claude_branch,
        )

        self.runner.run(
            (
                "git",
                "clone",
                context.config.repository_url,
                str(context.implementation.claude_dir),
            ),
            cwd=context.cwd,
            log_name="claude-clone",
        )
        self.git.checkout_branch(
            context.implementation.codex_dir,
            branch=context.implementation.codex_branch,
            base_branch=context.implementation.base_branch,
            log_prefix="codex",
        )
        self.git.checkout_branch(
            context.implementation.claude_dir,
            branch=context.implementation.claude_branch,
            base_branch=context.implementation.base_branch,
            log_prefix="claude",
        )

        for repo_dir in (
            context.implementation.codex_dir,
            context.implementation.claude_dir,
        ):
            shutil.copy2(context.plan.path, repo_dir / context.plan.file_name)

    def _run_initial_agents(self, context: RunContext) -> None:
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

        self.git.push_agent_branch(
            context,
            label="codex initial",
            repo_dir=context.implementation.codex_dir,
            branch=context.implementation.codex_branch,
        )
        self.git.push_agent_branch(
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
    ) -> None:
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

        self.git.push_agent_branch(
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
    ) -> None:
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
        self.git.record_dirty_files(
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
        pr_body = build_pr_body(context, selected)
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
        pr_number = parse_pr_number(pr_result.output)
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
