"""Repository preparation phases for Diamond Dev orchestration."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from diamond_dev.commands import build_pnpm_install_command, build_uv_sync_command
from diamond_dev.errors import DiamondDevError
from diamond_dev.executor import CommandRunner
from diamond_dev.git_ops import GitOperations

if TYPE_CHECKING:
    from diamond_dev.workflow import RunContext


class RepositoryPreparationMixin:
    """Prepare or validate wiki and implementation repositories."""

    runner: CommandRunner
    git: GitOperations

    def _prepare_wiki_with_plan(self, context: RunContext) -> None:
        self._ensure_wiki_repo(context)
        wiki_plan = context.wiki.directory / context.plan.file_name
        source_plan_markdown = context.plan.path.read_text(encoding="utf-8")
        if wiki_plan.is_file():
            wiki_plan_markdown = wiki_plan.read_text(encoding="utf-8")
            if wiki_plan_markdown != source_plan_markdown:
                raise DiamondDevError(
                    f"Plan drift detected for {context.plan.file_name}; "
                    "the wiki copy differs from the source plan",
                )
            logger.info("Wiki plan already matches source plan")
            return

        shutil.copy2(context.plan.path, wiki_plan)
        self.git.commit_if_changes(
            context.wiki.directory,
            message=f"Add {context.plan.file_name} plan",
            log_prefix="wiki-plan",
            paths=(context.plan.file_name,),
        )
        self.git.run(context.wiki.directory, "push", log_name="wiki-plan-push")

    def _ensure_wiki_repo(self, context: RunContext) -> None:
        if context.wiki.directory.exists():
            if not self.git.is_git_repo(
                context.wiki.directory,
                log_name="wiki-is-git-repo",
            ):
                raise DiamondDevError(
                    f"Existing wiki path is not a Git repo: {context.wiki.directory}",
                )
            self.git.sync_wiki(context.wiki.directory)
            return

        self.runner.run(
            ("git", "clone", context.wiki.url, str(context.wiki.directory)),
            cwd=context.cwd,
            log_name="wiki-clone",
        )

    def _prepare_implementation_clones(self, context: RunContext) -> RunContext:
        implementation = context.implementation
        clone_dirs = (implementation.codex_dir, implementation.claude_dir)
        existing_clone_count = sum(clone_dir.exists() for clone_dir in clone_dirs)
        if existing_clone_count not in {0, len(clone_dirs)}:
            raise DiamondDevError(
                "Cannot auto-resume with missing implementation clone; "
                f"expected both {implementation.codex_dir} and "
                f"{implementation.claude_dir}",
            )
        if existing_clone_count == len(clone_dirs):
            return self._resume_implementation_clones(context)

        self._ensure_remote_workflow_branches_absent(context)
        return self._clone_implementation_repositories(context)

    def _ensure_remote_workflow_branches_absent(self, context: RunContext) -> None:
        for branch in (
            context.implementation.codex_branch,
            context.implementation.claude_branch,
        ):
            if self.git.remote_url_branch_exists(
                context.cwd,
                remote_url=context.config.repository_url,
                branch=branch,
                log_name=f"remote-branch-exists-{branch}",
            ):
                raise DiamondDevError(
                    "Workflow branch exists on origin but local implementation "
                    f"clones are missing: {branch}",
                )

    def _clone_implementation_repositories(self, context: RunContext) -> RunContext:
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

    def _resume_implementation_clones(self, context: RunContext) -> RunContext:
        implementation = context.implementation
        for agent_branch in _resume_agent_branches(context):
            self._validate_resume_clone(context, agent_branch)
        implementation = implementation.with_base_branch(
            self.git.remote_default_branch(implementation.codex_dir),
        )
        self._install_packages(implementation.codex_dir, log_prefix="codex")
        self._install_packages(implementation.claude_dir, log_prefix="claude")
        return context.with_implementation(implementation)

    def _validate_resume_clone(
        self,
        context: RunContext,
        agent_branch: tuple[Path, str, str],
    ) -> None:
        repo_dir, branch, log_prefix = agent_branch
        if not self.git.is_git_repo(repo_dir, log_name=f"{log_prefix}-is-git-repo"):
            raise DiamondDevError(
                f"Existing implementation path is not a Git repo: {repo_dir}",
            )
        origin_url = self.git.origin_url(repo_dir, log_name=f"{log_prefix}-origin-url")
        if origin_url != context.config.repository_url:
            raise DiamondDevError(
                f"Implementation clone origin mismatch for {repo_dir}: "
                f"expected {context.config.repository_url}, found {origin_url}",
            )
        self.git.fetch(repo_dir, log_name=f"{log_prefix}-fetch")
        if not self.git.local_branch_exists(
            repo_dir,
            branch,
            log_name=f"{log_prefix}-local-branch-exists",
        ):
            raise DiamondDevError(
                "Cannot auto-resume because local workflow branch is missing: "
                f"{branch}",
            )
        self.git.checkout_existing_branch(
            repo_dir,
            branch=branch,
            log_prefix=log_prefix,
        )
        remote_exists = self.git.remote_branch_exists(
            repo_dir,
            branch,
            log_name=f"{log_prefix}-remote-branch-exists",
        )
        if remote_exists and not self.git.branches_match_remote(
            repo_dir,
            branch,
            log_prefix=log_prefix,
        ):
            raise DiamondDevError(
                f"Cannot auto-resume divergent workflow branch: {branch}",
            )

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


def _resume_agent_branches(context: RunContext) -> tuple[tuple[Path, str, str], ...]:
    return (
        (
            context.implementation.codex_dir,
            context.implementation.codex_branch,
            "codex",
        ),
        (
            context.implementation.claude_dir,
            context.implementation.claude_branch,
            "claude",
        ),
    )
