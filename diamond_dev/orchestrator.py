"""Diamond Dev workflow orchestration."""

from __future__ import annotations

import re
import shlex
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
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
from diamond_dev.config import DiamondDevConfig, load_config, read_gemini_prompt
from diamond_dev.errors import CommandFailureError, DiamondDevError
from diamond_dev.executor import CommandResult, CommandRunner
from diamond_dev.naming import (
    derive_notes_repository_url,
    notes_directory_name,
    slug_for_plan,
)
from diamond_dev.notify import notify_url


@dataclass(slots=True)
class DirtyRecord:
    """Uncommitted files observed after an agent phase."""

    label: str
    branch: str
    files: tuple[str, ...]


@dataclass(slots=True)
class RunContext:
    """Resolved paths and names for one Diamond Dev run."""

    cwd: Path
    config: DiamondDevConfig
    plan_path: Path
    plan_slug: str
    notes_url: str
    notes_dir: Path
    codex_dir: Path
    claude_dir: Path
    codex_branch: str
    claude_branch: str
    comparison_file: Path
    notes_comparison_file: Path
    notes_review_file: Path
    base_branch: str = ""
    dirty_records: list[DirtyRecord] = field(default_factory=list)

    @property
    def plan_file_name(self) -> str:
        """Return the source plan filename."""
        return self.plan_path.name

    @property
    def comparison_file_name(self) -> str:
        """Return the implementation-repo comparison filename."""
        return f"{self.plan_slug}-comparison.md"

    @property
    def review_file_name(self) -> str:
        """Return the implementation-repo review filename."""
        return f"{self.plan_slug}-review.md"


@dataclass(frozen=True, slots=True)
class SelectedImplementation:
    """The implementation branch selected from comparison notes."""

    accepted_agent: AgentChoice
    opposite_agent: AgentChoice
    repo_dir: Path
    branch: str


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
        self.sleep = sleep

    def run(self, plan_path: Path) -> int:
        """Run the Diamond Dev workflow for a markdown plan."""
        resolved_plan_path = self._resolve_plan_path(plan_path)
        config = load_config(self.cwd)
        context = self._build_context(resolved_plan_path, config)

        self._validate_initial_state(context)
        self._prepare_notes_with_plan(context)
        self._prepare_implementation_clones(context)
        self._run_initial_agents(context)
        self._run_gemini_comparison(context)
        accepted_agent = self._poll_acceptance(context)
        selected = self._selected_implementation(context, accepted_agent)
        self._run_opposite_agent(context, selected)
        self._run_review_phases(context, selected)
        self._finalize_pr(context, selected)
        return 0

    def _resolve_plan_path(self, plan_path: Path) -> Path:
        candidate_path = plan_path if plan_path.is_absolute() else self.cwd / plan_path
        resolved_path = candidate_path.resolve()
        if not resolved_path.is_file():
            raise DiamondDevError(f"Plan file not found: {resolved_path}")
        if resolved_path.suffix.lower() != ".md":
            raise DiamondDevError(f"Plan file must be markdown: {resolved_path}")
        return resolved_path

    def _build_context(self, plan_path: Path, config: DiamondDevConfig) -> RunContext:
        plan_slug = slug_for_plan(plan_path)
        notes_url = config.notes_repository_url or derive_notes_repository_url(
            config.repository_url,
        )
        notes_dir = self.cwd / notes_directory_name(config.repository_url)
        return RunContext(
            cwd=self.cwd,
            config=config,
            plan_path=plan_path,
            plan_slug=plan_slug,
            notes_url=notes_url,
            notes_dir=notes_dir,
            codex_dir=self.cwd / f"codex-{plan_slug}",
            claude_dir=self.cwd / f"claude-{plan_slug}",
            codex_branch=f"codex/{plan_slug}",
            claude_branch=f"claude/{plan_slug}",
            comparison_file=self.cwd / "comparison.md",
            notes_comparison_file=notes_dir / f"{plan_slug}-comparison.md",
            notes_review_file=notes_dir / f"{plan_slug}-review.md",
        )

    def _validate_initial_state(self, context: RunContext) -> None:
        for clone_dir in (context.codex_dir, context.claude_dir):
            if clone_dir.exists():
                raise DiamondDevError(
                    f"Implementation clone already exists: {clone_dir}",
                )

    def _prepare_notes_with_plan(self, context: RunContext) -> None:
        self._ensure_notes_repo(context)
        shutil.copy2(context.plan_path, context.notes_dir / context.plan_file_name)
        self._commit_if_changes(
            context.notes_dir,
            message=f"Add {context.plan_file_name} plan",
            log_prefix="notes-plan",
            paths=(context.plan_file_name,),
        )
        self._run_git(context.notes_dir, "push", log_name="notes-plan-push")

    def _ensure_notes_repo(self, context: RunContext) -> None:
        if context.notes_dir.exists():
            if not (context.notes_dir / ".git").is_dir():
                raise DiamondDevError(
                    f"Existing notes path is not a Git repo: {context.notes_dir}",
                )
            self._sync_notes(context)
            return

        self.runner.run(
            ("git", "clone", context.notes_url, str(context.notes_dir)),
            cwd=context.cwd,
            log_name="notes-clone",
        )

    def _prepare_implementation_clones(self, context: RunContext) -> None:
        self.runner.run(
            ("git", "clone", context.config.repository_url, str(context.codex_dir)),
            cwd=context.cwd,
            log_name="codex-clone",
        )
        context.base_branch = self._remote_default_branch(context.codex_dir)
        self._ensure_remote_branch_absent(context.codex_dir, context.codex_branch)
        self._ensure_remote_branch_absent(context.codex_dir, context.claude_branch)

        self.runner.run(
            ("git", "clone", context.config.repository_url, str(context.claude_dir)),
            cwd=context.cwd,
            log_name="claude-clone",
        )
        self._checkout_branch(
            context.codex_dir,
            branch=context.codex_branch,
            base_branch=context.base_branch,
            log_prefix="codex",
        )
        self._checkout_branch(
            context.claude_dir,
            branch=context.claude_branch,
            base_branch=context.base_branch,
            log_prefix="claude",
        )

        for repo_dir in (context.codex_dir, context.claude_dir):
            shutil.copy2(context.plan_path, repo_dir / context.plan_file_name)

    def _run_initial_agents(self, context: RunContext) -> None:
        prompt = initial_implementation_prompt(context.plan_file_name)
        codex_process = self.runner.start(
            build_codex_command(context.codex_dir, prompt),
            cwd=context.codex_dir,
            log_name="codex-initial-agent",
        )
        claude_process = self.runner.start(
            build_claude_print_command(prompt),
            cwd=context.claude_dir,
            log_name="claude-initial-agent",
        )
        notify_url(
            context.config.notify_initial_implementation_url,
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

        self._push_agent_branch(
            context,
            label="codex initial",
            repo_dir=context.codex_dir,
            branch=context.codex_branch,
        )
        self._push_agent_branch(
            context,
            label="claude initial",
            repo_dir=context.claude_dir,
            branch=context.claude_branch,
        )

    def _run_gemini_comparison(self, context: RunContext) -> None:
        context.comparison_file.unlink(missing_ok=True)
        configured_prompt = read_gemini_prompt(context.config)
        prompt = gemini_comparison_prompt(
            configured_prompt,
            ComparisonPromptContext(
                base_branch=context.base_branch,
                codex_branch=context.codex_branch,
                claude_branch=context.claude_branch,
                codex_dir=context.codex_dir,
                claude_dir=context.claude_dir,
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
        shutil.copy2(context.comparison_file, context.notes_comparison_file)
        self._commit_if_changes(
            context.notes_dir,
            message=f"Add {context.plan_slug} comparison",
            log_prefix="notes-comparison",
            paths=(context.notes_comparison_file.name,),
        )
        self._run_git(context.notes_dir, "push", log_name="notes-comparison-push")
        notify_url(context.config.notify_comparison_url, label="comparison")

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
            self._sync_notes(context)
            comparison_markdown = context.notes_comparison_file.read_text(
                encoding="utf-8",
            )
            if accepted_agent := parse_acceptance(comparison_markdown):
                logger.info("Accepted implementation: {}", accepted_agent)
                return accepted_agent
            logger.info("No accepted implementation found yet")

        raise DiamondDevError("No valid acceptance found after polling window")

    def _selected_implementation(
        self,
        context: RunContext,
        accepted_agent: AgentChoice,
    ) -> SelectedImplementation:
        if accepted_agent == "codex":
            return SelectedImplementation(
                accepted_agent="codex",
                opposite_agent="claude",
                repo_dir=context.codex_dir,
                branch=context.codex_branch,
            )
        return SelectedImplementation(
            accepted_agent="claude",
            opposite_agent="codex",
            repo_dir=context.claude_dir,
            branch=context.claude_branch,
        )

    def _run_opposite_agent(
        self,
        context: RunContext,
        selected: SelectedImplementation,
    ) -> None:
        plan_file = selected.repo_dir / context.plan_file_name
        plan_file.unlink(missing_ok=True)
        self._commit_if_changes(
            selected.repo_dir,
            message="Remove original plan artifact",
            log_prefix="selected-plan-cleanup",
            paths=(context.plan_file_name,),
        )

        comparison_file = selected.repo_dir / context.comparison_file_name
        shutil.copy2(context.notes_comparison_file, comparison_file)
        self._run_git(selected.repo_dir, "fetch", log_name="selected-fetch")

        prompt = comparison_implementation_prompt(context.comparison_file_name)
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

        self._push_agent_branch(
            context,
            label=f"{selected.opposite_agent} comparison",
            repo_dir=selected.repo_dir,
            branch=selected.branch,
        )

        comparison_file.unlink(missing_ok=True)
        self._commit_if_changes(
            selected.repo_dir,
            message="Remove comparison artifact",
            log_prefix="selected-comparison-cleanup",
            paths=(context.comparison_file_name,),
        )
        self._run_git(
            selected.repo_dir,
            "push",
            "-u",
            "origin",
            selected.branch,
            log_name="selected-comparison-cleanup-push",
        )
        notify_url(
            context.config.notify_comparison_implementation_url,
            label="comparison implementation",
        )

    def _run_review_phases(
        self,
        context: RunContext,
        selected: SelectedImplementation,
    ) -> None:
        review_file = selected.repo_dir / context.review_file_name
        try:
            self.runner.run_to_file(
                build_coderabbit_review_command(context.base_branch),
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
                    review_judgment_prompt(context.review_file_name),
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
                context.config.notify_review_input_needed_url,
                label="review input needed",
            )

        shutil.copy2(review_file, context.notes_review_file)
        self._commit_if_changes(
            context.notes_dir,
            message=f"Add {context.plan_slug} review",
            log_prefix="notes-review",
            paths=(context.notes_review_file.name,),
        )
        self._run_git(context.notes_dir, "push", log_name="notes-review-push")

        try:
            self.runner.run(
                build_codex_command(
                    selected.repo_dir,
                    review_fix_prompt(context.review_file_name),
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
            context.plan_file_name,
            context.comparison_file_name,
            context.review_file_name,
        ):
            (selected.repo_dir / artifact_name).unlink(missing_ok=True)

        self._commit_if_changes(
            selected.repo_dir,
            message="post work cleanup",
            log_prefix="post-work-cleanup",
            paths=(
                context.plan_file_name,
                context.comparison_file_name,
                context.review_file_name,
            ),
        )
        self._record_dirty_files(
            context,
            "final selected branch",
            selected.repo_dir,
            selected.branch,
        )
        self._run_git(
            selected.repo_dir,
            "push",
            "-u",
            "origin",
            selected.branch,
            log_name="final-push",
        )

        pr_title = f"Implement {context.plan_path.stem}"
        pr_body = build_pr_body(context, selected)
        pr_result = self.runner.run(
            build_gh_pr_create_command(
                base_branch=context.base_branch,
                head_branch=selected.branch,
                title=pr_title,
                body=pr_body,
            ),
            cwd=selected.repo_dir,
            log_name="gh-pr-create",
        )
        pr_number = _parse_pr_number(pr_result.output)
        notify_url(context.config.notify_open_pr_url, label="open pr")

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

    def _push_agent_branch(
        self,
        context: RunContext,
        *,
        label: str,
        repo_dir: Path,
        branch: str,
    ) -> None:
        self._record_dirty_files(context, label, repo_dir, branch)
        self._run_git(
            repo_dir,
            "push",
            "-u",
            "origin",
            branch,
            log_name=f"{label}-push",
        )

    def _record_dirty_files(
        self,
        context: RunContext,
        label: str,
        repo_dir: Path,
        branch: str,
    ) -> None:
        dirty_files = self._dirty_files(repo_dir, log_name=f"{label}-dirty-status")
        if not dirty_files:
            return

        context.dirty_records.append(
            DirtyRecord(label=label, branch=branch, files=dirty_files),
        )
        logger.warning(
            "Dirty files remain after {} and will not be pushed: {}",
            label,
            ", ".join(dirty_files),
        )

    def _dirty_files(self, repo_dir: Path, *, log_name: str) -> tuple[str, ...]:
        result = self._run_git(repo_dir, "status", "--porcelain", log_name=log_name)
        return tuple(
            status_line[3:] if len(status_line) > 3 else status_line
            for status_line in result.output.splitlines()
            if status_line
        )

    def _remote_default_branch(self, repo_dir: Path) -> str:
        result = self._run_git(
            repo_dir,
            "symbolic-ref",
            "--quiet",
            "--short",
            "refs/remotes/origin/HEAD",
            log_name="remote-default-branch",
        )
        remote_ref = result.output.strip().splitlines()[-1]
        if not remote_ref.startswith("origin/"):
            raise DiamondDevError(f"Unexpected remote HEAD ref: {remote_ref}")
        return remote_ref.removeprefix("origin/")

    def _ensure_remote_branch_absent(self, repo_dir: Path, branch: str) -> None:
        result = self._run_git(
            repo_dir,
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            branch,
            log_name=f"branch-exists-{branch}",
            check=False,
        )
        if result.returncode == 0:
            raise DiamondDevError(f"Expected remote branch already exists: {branch}")
        if result.returncode != 2:
            raise CommandFailureError(
                command=shlex.join(result.command),
                cwd=str(repo_dir),
                returncode=result.returncode,
                log_path=str(result.log_path),
            )

    def _checkout_branch(
        self,
        repo_dir: Path,
        *,
        branch: str,
        base_branch: str,
        log_prefix: str,
    ) -> None:
        self._run_git(
            repo_dir,
            "checkout",
            "-b",
            branch,
            f"origin/{base_branch}",
            log_name=f"{log_prefix}-checkout",
        )

    def _sync_notes(self, context: RunContext) -> None:
        self._run_git(context.notes_dir, "fetch", "--prune", log_name="notes-fetch")
        self._run_git(context.notes_dir, "pull", "--ff-only", log_name="notes-pull")

    def _commit_if_changes(
        self,
        repo_dir: Path,
        *,
        message: str,
        log_prefix: str,
        paths: tuple[str, ...],
    ) -> bool:
        committable_paths = self._committable_paths(repo_dir, paths, log_prefix)
        if not committable_paths:
            logger.info("No committable paths for {}", log_prefix)
            return False

        self._run_git(
            repo_dir,
            "add",
            "--all",
            "--",
            *committable_paths,
            log_name=f"{log_prefix}-add",
        )
        staged_diff = self._run_git(
            repo_dir,
            "diff",
            "--cached",
            "--quiet",
            "--exit-code",
            "--",
            *committable_paths,
            log_name=f"{log_prefix}-staged-diff",
            check=False,
        )
        if staged_diff.returncode == 0:
            logger.info("No changes to commit for {}", log_prefix)
            return False
        if staged_diff.returncode != 1:
            raise CommandFailureError(
                command=shlex.join(staged_diff.command),
                cwd=str(repo_dir),
                returncode=staged_diff.returncode,
                log_path=str(staged_diff.log_path),
            )
        self._run_git(
            repo_dir,
            "commit",
            "-m",
            message,
            log_name=f"{log_prefix}-commit",
        )
        return True

    def _committable_paths(
        self,
        repo_dir: Path,
        paths: tuple[str, ...],
        log_prefix: str,
    ) -> tuple[str, ...]:
        committable_paths: list[str] = []
        for path in paths:
            if (repo_dir / path).exists() or self._is_tracked(
                repo_dir,
                path,
                log_name=f"{log_prefix}-tracked-{path}",
            ):
                committable_paths.append(path)
                continue

            logger.info(
                "Skipping missing untracked path for {}: {}",
                log_prefix,
                path,
            )
        return tuple(committable_paths)

    def _is_tracked(self, repo_dir: Path, path: str, *, log_name: str) -> bool:
        result = self._run_git(
            repo_dir,
            "ls-files",
            "--error-unmatch",
            "--",
            path,
            log_name=log_name,
            check=False,
        )
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise CommandFailureError(
            command=shlex.join(result.command),
            cwd=str(repo_dir),
            returncode=result.returncode,
            log_path=str(result.log_path),
        )

    def _run_git(
        self,
        repo_dir: Path,
        *args: str,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        return self.runner.run(
            ("git", *args),
            cwd=repo_dir,
            log_name=log_name,
            check=check,
        )


def _parse_pr_number(gh_output: str) -> str:
    match = re.search(r"/pull/(\d+)", gh_output)
    if match is None:
        raise DiamondDevError(f"Could not parse PR number from gh output: {gh_output}")
    return match.group(1)


def build_pr_body(context: RunContext, selected: SelectedImplementation) -> str:
    """Build deterministic pull request body text."""
    body_lines = [
        "Automated diamond-dev implementation.",
        "",
        f"- Accepted implementation: {selected.accepted_agent}",
        f"- Selected branch: {selected.branch}",
        f"- Base branch: {context.base_branch}",
        f"- Comparison notes: {context.notes_comparison_file.name}",
        f"- Review notes: {context.notes_review_file.name}",
    ]
    if context.dirty_records:
        body_lines.extend(("", "Uncommitted dirty files observed:"))
        body_lines.extend(
            (
                f"- {record.label} ({record.branch}): {', '.join(record.files)}"
                for record in context.dirty_records
            ),
        )
    return "\n".join(body_lines)
