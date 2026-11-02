"""Read-only run inspection, cleanup, and config preview commands."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from diamond_dev import workflow
from diamond_dev.acceptance import parse_acceptance
from diamond_dev.config import load_config
from diamond_dev.errors import DiamondDevError, MalformedAcceptanceError
from diamond_dev.naming import (
    derive_wiki_repository_url,
    slug_for_plan,
    slugify,
    wiki_directory_name,
)
from diamond_dev.workflow import safe_generated_child_path

if TYPE_CHECKING:
    from diamond_dev.config import DiamondDevConfig
    from diamond_dev.executor import CommandExecutor
    from diamond_dev.workflow import RunContext


@dataclass(frozen=True, slots=True)
class BranchStatus:
    """Observed state of one implementation clone."""

    agent_name: str
    repo_dir: Path
    clone_exists: bool
    expected_branch: str
    current_branch: str | None = None
    dirty_file_count: int | None = None


@dataclass(frozen=True, slots=True)
class RunStatusReport:
    """Observed filesystem state for one run slug."""

    # pylint: disable=too-many-instance-attributes

    slug: str
    branches: tuple[BranchStatus, ...]
    wiki_dir: Path
    wiki_exists: bool
    comparison_exists: bool
    accepted_agent: str | None
    acceptance_error: str | None
    bundle_exists: bool
    review_exists: bool
    last_run_status: str | None


def resolve_run_slug(cwd: Path, plan_or_slug: str) -> str:
    """Return the run slug for a markdown plan path or a raw slug."""
    candidate = Path(plan_or_slug)
    if candidate.suffix.lower() == ".md":
        return slug_for_plan(candidate if candidate.is_absolute() else cwd / candidate)
    slug = slugify(plan_or_slug)
    if not slug:
        raise DiamondDevError(f"Could not derive a run slug from `{plan_or_slug}`")
    return slug


def run_status(
    *,
    cwd: Path,
    runner: CommandExecutor,
    plan_or_slug: str,
    config_path: Path | None = None,
) -> RunStatusReport:
    """Inspect and log the on-disk state of one run without changing it."""
    config = load_config(cwd, config_path)
    slug = resolve_run_slug(cwd, plan_or_slug)
    wiki_dir = _wiki_dir(cwd, config)
    accepted_agent, acceptance_error = _accepted_agent(wiki_dir, slug, config)
    report = RunStatusReport(
        slug=slug,
        branches=tuple(
            _branch_status(cwd=cwd, runner=runner, agent_name=agent_name, slug=slug)
            for agent_name in config.workflow.implementers
        ),
        wiki_dir=wiki_dir,
        wiki_exists=wiki_dir.is_dir(),
        comparison_exists=_wiki_file(wiki_dir, f"{slug}-comparison.md").is_file(),
        accepted_agent=accepted_agent,
        acceptance_error=acceptance_error,
        bundle_exists=_wiki_file(wiki_dir, f"{slug}-comparison-bundle.md").is_file(),
        review_exists=_wiki_file(wiki_dir, f"{slug}-review.md").is_file(),
        last_run_status=_last_run_status(cwd),
    )
    _log_status(report)
    return report


def run_clean(
    *,
    cwd: Path,
    runner: CommandExecutor,
    plan_or_slug: str,
    config_path: Path | None = None,
    force: bool = False,
) -> tuple[Path, ...]:
    """Remove generated implementation clones and local artifacts for a run."""
    config = load_config(cwd, config_path)
    slug = resolve_run_slug(cwd, plan_or_slug)
    removed: list[Path] = []
    for agent_name in config.workflow.implementers:
        clone_dir = safe_generated_child_path(cwd, f"{agent_name}-{slug}")
        if not clone_dir.exists():
            continue
        if not force:
            _require_matching_clone(
                runner=runner,
                clone_dir=clone_dir,
                repository_url=config.repository_url,
            )
        shutil.rmtree(clone_dir)
        logger.info("Removed implementation clone: {}", clone_dir)
        removed.append(clone_dir)

    bundle_file = safe_generated_child_path(cwd, f"{slug}-comparison-bundle.md")
    if bundle_file.is_file():
        bundle_file.unlink()
        logger.info("Removed local comparison bundle: {}", bundle_file)
        removed.append(bundle_file)

    comparison_file = safe_generated_child_path(
        cwd,
        workflow.LOCAL_COMPARISON_FILE_NAME,
    )
    if comparison_file.is_file():
        if force:
            comparison_file.unlink()
            logger.info("Removed local comparison: {}", comparison_file)
            removed.append(comparison_file)
        else:
            logger.info(
                "Keeping local comparison {} (it is shared across runs; use "
                "--force to remove it)",
                comparison_file,
            )

    if not removed:
        logger.info("Nothing to clean for run `{}`", slug)
    logger.info(
        "Clean never touches the wiki clone or remote branches; remove those "
        "manually if needed",
    )
    return tuple(removed)


def run_validate(cwd: Path, config_path: Path | None = None) -> int:
    """Load and validate the config file, logging the resolved settings."""
    config = load_config(cwd, config_path)
    logger.info("Config OK: {}", config.config_path)
    logger.info("Repository: {}", config.repository_url)
    logger.info("Wiki repository: {}", _wiki_url(config))
    logger.info("Implementers: {}", ", ".join(config.workflow.implementers))
    _log_roles(config)
    logger.info("Required CLIs: {}", ", ".join(config.required_cli_names()))
    return 0


def run_dry_run(
    *,
    cwd: Path,
    plan_path: Path,
    config_path: Path | None = None,
) -> int:
    """Log the resolved run plan without cloning or launching agents."""
    resolved_plan_path = workflow.resolve_plan_path(cwd=cwd, plan_path=plan_path)
    config = load_config(cwd, config_path)
    context = workflow.build_run_context(
        cwd=cwd,
        plan_path=resolved_plan_path,
        config=config,
    )
    _log_dry_run(context)
    return 0


def _log_dry_run(context: RunContext) -> None:
    config = context.config
    logger.info("Dry run for plan {} (slug `{}`)", context.plan.path, context.plan.slug)
    logger.info("Repository: {}", config.repository_url)
    logger.info("Wiki: {} -> {}", context.wiki.url, context.wiki.directory)
    for branch in context.implementation.branches:
        logger.info(
            "Implementer {}: clone {} on branch {}",
            branch.agent_name,
            branch.repo_dir,
            branch.branch,
        )
    _log_roles(config)
    logger.info("Required CLIs: {}", ", ".join(config.required_cli_names()))
    if config.comparison.test_commands:
        logger.info(
            "Comparison test commands: {}",
            "; ".join(config.comparison.test_commands),
        )
    if config.comparison.signal_commands:
        logger.info(
            "Comparison signal commands: {}",
            "; ".join(config.comparison.signal_commands),
        )
    logger.info("Notification format: {}", config.notifications.format)
    logger.info("Dry run complete; no repositories were cloned")


def _log_roles(config: DiamondDevConfig) -> None:
    for role_name, agent_name in (
        ("comparison_judge", config.workflow.comparison_judge),
        ("comparison_fixer", config.workflow.comparison_fixer or "<first loser>"),
        ("review_provider", config.workflow.review_provider),
        ("review_judge", config.workflow.review_judge),
        ("review_fixer", config.workflow.review_fixer),
        ("final_reviewer", config.workflow.final_reviewer),
    ):
        suffix = ""
        if agent_name in config.workflow.role_agent_names():
            agent_config = config.agent_config(agent_name)
            adapter_name = agent_config.adapter_name(agent_name)
            if adapter_name != agent_name:
                suffix = f" (adapter {adapter_name})"
            if agent_config.model is not None:
                suffix = f"{suffix} (model {agent_config.model})"
        logger.info("Role {}: {}{}", role_name, agent_name, suffix)


def _branch_status(
    *,
    cwd: Path,
    runner: CommandExecutor,
    agent_name: str,
    slug: str,
) -> BranchStatus:
    repo_dir = safe_generated_child_path(cwd, f"{agent_name}-{slug}")
    expected_branch = f"{agent_name}/{slug}"
    if not repo_dir.is_dir():
        return BranchStatus(
            agent_name=agent_name,
            repo_dir=repo_dir,
            clone_exists=False,
            expected_branch=expected_branch,
        )
    branch_result = runner.run(
        ("git", "rev-parse", "--abbrev-ref", "HEAD"),
        cwd=repo_dir,
        log_name=f"status-{agent_name}-branch",
        check=False,
    )
    current_branch = (
        branch_result.output.strip() if branch_result.returncode == 0 else None
    )
    porcelain_result = runner.run(
        ("git", "status", "--porcelain"),
        cwd=repo_dir,
        log_name=f"status-{agent_name}-porcelain",
        check=False,
    )
    dirty_file_count = (
        len(porcelain_result.output.splitlines())
        if porcelain_result.returncode == 0
        else None
    )
    return BranchStatus(
        agent_name=agent_name,
        repo_dir=repo_dir,
        clone_exists=True,
        expected_branch=expected_branch,
        current_branch=current_branch,
        dirty_file_count=dirty_file_count,
    )


def _require_matching_clone(
    *,
    runner: CommandExecutor,
    clone_dir: Path,
    repository_url: str,
) -> None:
    if not (clone_dir / ".git").exists():
        raise DiamondDevError(
            f"Refusing to remove {clone_dir}: not a Git repository "
            "(use --force to override)",
        )
    origin_result = runner.run(
        ("git", "config", "--get", "remote.origin.url"),
        cwd=clone_dir,
        log_name=f"clean-origin-{clone_dir.name}",
        check=False,
    )
    origin_url = origin_result.output.strip()
    if origin_result.returncode != 0 or origin_url != repository_url:
        raise DiamondDevError(
            f"Refusing to remove {clone_dir}: origin `{origin_url}` does not "
            f"match configured repository (use --force to override)",
        )


def _wiki_url(config: DiamondDevConfig) -> str:
    return config.wiki_repository_url or derive_wiki_repository_url(
        config.repository_url,
    )


def _wiki_dir(cwd: Path, config: DiamondDevConfig) -> Path:
    return cwd / wiki_directory_name(_wiki_url(config))


def _wiki_file(wiki_dir: Path, file_name: str) -> Path:
    return safe_generated_child_path(wiki_dir, file_name)


def _accepted_agent(
    wiki_dir: Path,
    slug: str,
    config: DiamondDevConfig,
) -> tuple[str | None, str | None]:
    comparison_file = _wiki_file(wiki_dir, f"{slug}-comparison.md")
    if not comparison_file.is_file():
        return None, None
    try:
        markdown = comparison_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return None, str(error)
    try:
        return parse_acceptance(markdown, config.workflow.implementers), None
    except (MalformedAcceptanceError,) as error:
        return None, str(error)


def _last_run_status(cwd: Path) -> str | None:
    run_summary_path = cwd / "logs" / "run.json"
    if not run_summary_path.is_file():
        return None
    try:
        payload = json.loads(run_summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    status = payload.get("status")
    return status if isinstance(status, str) else None


def _log_status(report: RunStatusReport) -> None:
    logger.info("Status for run `{}`:", report.slug)
    for branch in report.branches:
        if not branch.clone_exists:
            logger.info(
                "- {}: clone missing at {}",
                branch.agent_name,
                branch.repo_dir,
            )
            continue
        logger.info(
            "- {}: clone at {}, branch {} (expected {}), dirty files: {}",
            branch.agent_name,
            branch.repo_dir,
            branch.current_branch or "<unknown>",
            branch.expected_branch,
            "unknown" if branch.dirty_file_count is None else branch.dirty_file_count,
        )
    if not report.wiki_exists:
        logger.info("- wiki: clone missing at {}", report.wiki_dir)
    else:
        logger.info(
            "- wiki: clone at {}; comparison: {}; bundle: {}; review: {}",
            report.wiki_dir,
            "present" if report.comparison_exists else "missing",
            "present" if report.bundle_exists else "missing",
            "present" if report.review_exists else "missing",
        )
    if report.accepted_agent is not None:
        logger.info("- acceptance: accepted `{}`", report.accepted_agent)
    elif report.acceptance_error is not None:
        logger.warning("- acceptance: {}", report.acceptance_error)
    elif report.comparison_exists:
        logger.info("- acceptance: waiting for a checked box")
    if report.last_run_status is not None:
        logger.info("- last run report: {} (logs/run.json)", report.last_run_status)
