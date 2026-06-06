"""Repository preparation phases for Diamond Dev orchestration."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from diamond_dev.commands import build_pnpm_install_command, build_uv_sync_command
from diamond_dev.errors import DiamondDevError
from diamond_dev.markdown import read_normalized_markdown

if TYPE_CHECKING:
    from diamond_dev.executor import CommandRunner
    from diamond_dev.git_ops import GitOperations
    from diamond_dev.providers import GitHubWorkflowProvider
    from diamond_dev.workflow import (
        CommitPairEntry,
        ImplementationBranch,
        ImplementationContext,
        RunContext,
    )


class RepositoryPreparationMixin:
    """Prepare or validate wiki and implementation repositories."""

    runner: CommandRunner
    git: GitOperations
    workflow_provider: GitHubWorkflowProvider

    def _prepare_wiki_with_plan(self, context: RunContext) -> None:
        self._ensure_wiki_repo(context)
        wiki_plan = context.wiki.directory / context.plan.file_name
        source_plan_markdown = read_normalized_markdown(context.plan.path)
        if wiki_plan.is_file():
            wiki_plan_markdown = read_normalized_markdown(wiki_plan)
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
        self.workflow_provider.push_wiki(
            context.wiki.directory,
            log_name="wiki-plan-push",
        )

    def _ensure_wiki_repo(self, context: RunContext) -> None:
        self._ensure_wiki_repo_at(
            cwd=context.cwd,
            wiki_url=context.wiki.url,
            wiki_dir=context.wiki.directory,
        )

    def _ensure_wiki_repo_at(
        self,
        *,
        cwd: Path,
        wiki_url: str,
        wiki_dir: Path,
    ) -> None:
        if wiki_dir.exists():
            if not self.git.is_git_repo(
                wiki_dir,
                log_name="wiki-is-git-repo",
            ):
                raise DiamondDevError(
                    f"Existing wiki path is not a Git repo: {wiki_dir}",
                )
            self.workflow_provider.sync_wiki(wiki_dir)
            return

        self.runner.run(
            ("git", "clone", wiki_url, str(wiki_dir)),
            cwd=cwd,
            log_name="wiki-clone",
        )

    def _prepare_implementation_clones(self, context: RunContext) -> RunContext:
        implementation = context.implementation
        clone_dirs = tuple(branch.repo_dir for branch in implementation.branches)
        existing_clone_count = sum(clone_dir.exists() for clone_dir in clone_dirs)
        if existing_clone_count not in {0, len(clone_dirs)}:
            raise DiamondDevError(
                "Cannot auto-resume with missing implementation clone; "
                f"expected all of {', '.join(str(path) for path in clone_dirs)}",
            )
        if existing_clone_count == len(clone_dirs):
            return self._resume_implementation_clones(context)

        self._ensure_remote_workflow_branches_absent(context)
        return self._clone_implementation_repositories(context)

    def _prepare_commit_pair_clones(self, context: RunContext) -> RunContext:
        """Prepare or resume implementation clones for a commit-pair run."""
        implementation = context.implementation
        clone_dirs = tuple(branch.repo_dir for branch in implementation.branches)
        existing_clone_count = sum(clone_dir.exists() for clone_dir in clone_dirs)
        if existing_clone_count not in {0, len(clone_dirs)}:
            raise DiamondDevError(
                "Cannot auto-resume with missing commit comparison clone; "
                f"expected all of {', '.join(str(path) for path in clone_dirs)}",
            )
        if existing_clone_count == len(clone_dirs):
            return self._resume_commit_pair_clones(context)
        return self._clone_commit_pair_repositories(context)

    def _ensure_remote_workflow_branches_absent(self, context: RunContext) -> None:
        for implementation_branch in context.implementation.branches:
            if self.workflow_provider.remote_workflow_branch_exists(
                context.cwd,
                remote_url=context.config.repository_url,
                branch=implementation_branch.branch,
                log_name=f"remote-branch-exists-{implementation_branch.branch}",
            ):
                raise DiamondDevError(
                    "Workflow branch exists on origin but local implementation "
                    f"clones are missing: {implementation_branch.branch}",
                )

    def _clone_implementation_repositories(self, context: RunContext) -> RunContext:
        implementation = context.implementation
        primary_branch = implementation.primary_branch
        self.runner.run(
            (
                "git",
                "clone",
                context.config.repository_url,
                str(primary_branch.repo_dir),
            ),
            cwd=context.cwd,
            log_name=f"{primary_branch.log_prefix}-clone",
        )
        context = self._with_remote_base_branch(context)
        implementation = context.implementation
        for branch in implementation.branches[1:]:
            self._copy_implementation_repository(
                source_dir=implementation.primary_branch.repo_dir,
                target_dir=branch.repo_dir,
            )
        for branch in implementation.branches:
            self.git.checkout_branch(
                branch.repo_dir,
                branch=branch.branch,
                base_branch=implementation.base_branch,
                log_prefix=branch.log_prefix,
            )
        self._install_implementation_packages(implementation)

        for branch in implementation.branches:
            shutil.copy2(context.plan.path, branch.repo_dir / context.plan.file_name)
        return context

    def _clone_commit_pair_repositories(self, context: RunContext) -> RunContext:
        commit_pair = context.commit_pair
        if commit_pair is None:
            raise DiamondDevError("Commit-pair clone preparation requires metadata")

        implementation = context.implementation
        primary_branch = implementation.primary_branch
        self.runner.run(
            (
                "git",
                "clone",
                context.config.repository_url,
                str(primary_branch.repo_dir),
            ),
            cwd=context.cwd,
            log_name=f"{primary_branch.log_prefix}-clone",
        )
        context = self._with_remote_base_branch(context)
        implementation = context.implementation
        for branch in implementation.branches[1:]:
            self._copy_implementation_repository(
                source_dir=implementation.primary_branch.repo_dir,
                target_dir=branch.repo_dir,
            )
        for branch, entry in zip(
            implementation.branches,
            commit_pair.entries,
            strict=True,
        ):
            self._checkout_commit_pair_branch(context, branch, entry)
        self._install_implementation_packages(implementation)
        return context

    def _copy_implementation_repository(
        self,
        *,
        source_dir: Path,
        target_dir: Path,
    ) -> None:
        logger.info(
            "Copying implementation repository from {} to {}",
            source_dir,
            target_dir,
        )
        shutil.copytree(
            source_dir,
            target_dir,
            copy_function=shutil.copy2,
            symlinks=True,
        )

    def _resume_implementation_clones(self, context: RunContext) -> RunContext:
        for agent_branch in context.implementation.branches:
            self._validate_resume_clone(context, agent_branch)
        context = self._with_remote_base_branch(context)
        self._install_implementation_packages(context.implementation)
        return context

    def _resume_commit_pair_clones(self, context: RunContext) -> RunContext:
        for agent_branch in context.implementation.branches:
            self._validate_resume_clone(context, agent_branch)
        context = self._with_remote_base_branch(context)
        self._install_implementation_packages(context.implementation)
        return context

    def _with_remote_base_branch(self, context: RunContext) -> RunContext:
        implementation = context.implementation.with_base_branch(
            self.git.remote_default_branch(
                context.implementation.primary_branch.repo_dir,
            ),
        )
        return context.with_implementation(implementation)

    def _install_implementation_packages(
        self,
        implementation: ImplementationContext,
    ) -> None:
        for branch in implementation.branches:
            self._install_packages(branch.repo_dir, log_prefix=branch.log_prefix)

    def _validate_resume_clone(
        self,
        context: RunContext,
        agent_branch: ImplementationBranch,
    ) -> None:
        repo_dir = agent_branch.repo_dir
        branch = agent_branch.branch
        log_prefix = agent_branch.log_prefix
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

    def _checkout_commit_pair_branch(
        self,
        context: RunContext,
        branch: ImplementationBranch,
        entry: CommitPairEntry,
    ) -> None:
        if entry.source == "local":
            self.runner.run(
                ("git", "fetch", str(context.cwd), entry.sha),
                cwd=branch.repo_dir,
                log_name=f"{branch.log_prefix}-local-commit-fetch",
            )
        remote_exists = self.git.remote_branch_exists(
            branch.repo_dir,
            branch.branch,
            log_name=f"{branch.log_prefix}-commit-remote-branch-exists",
        )
        generated_branch = branch.branch.startswith(f"diamond-dev/{context.plan.slug}/")
        checkout_ref = (
            f"origin/{branch.branch}"
            if remote_exists and generated_branch
            else entry.sha
        )
        self.git.run(
            branch.repo_dir,
            "checkout",
            "-B",
            branch.branch,
            checkout_ref,
            log_name=f"{branch.log_prefix}-commit-checkout",
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
