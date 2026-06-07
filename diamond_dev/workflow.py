"""Workflow data structures for Diamond Dev orchestration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from diamond_dev.errors import DiamondDevError
from diamond_dev.naming import (
    derive_wiki_repository_url,
    slug_for_plan,
    wiki_directory_name,
)
from diamond_dev.path_safety import (
    copy_child_file,
    copy_generated_child_file,
    read_child_text,
    read_generated_child_text,
    safe_child_path,
    safe_generated_child_path,
    write_child_text,
    write_generated_child_text,
)

if TYPE_CHECKING:
    from diamond_dev.config import DiamondDevConfig


__all__ = (
    "LOCAL_COMPARISON_FILE_NAME",
    "CommitMetadata",
    "CommitPairContext",
    "CommitPairEntry",
    "DirtyRecord",
    "ImplementationBranch",
    "ImplementationContext",
    "PlanContext",
    "RunContext",
    "SelectedImplementation",
    "WikiContext",
    "build_commit_pair_run_context",
    "build_run_context",
    "copy_child_file",
    "copy_generated_child_file",
    "read_child_text",
    "read_generated_child_text",
    "resolve_plan_path",
    "safe_child_path",
    "safe_generated_child_path",
    "selected_implementation",
    "write_child_text",
    "write_generated_child_text",
)

LOCAL_COMPARISON_FILE_NAME: Final = "comparison.md"


@dataclass(frozen=True, slots=True)
class DirtyRecord:
    """Uncommitted files observed after an agent phase."""

    label: str
    branch: str
    files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanContext:
    """Resolved plan file identity."""

    path: Path
    slug: str

    @property
    def file_name(self) -> str:
        """Return the source plan filename."""
        return self.path.name

    @property
    def comparison_file_name(self) -> str:
        """Return the implementation-repo comparison filename."""
        return f"{self.slug}-comparison.md"

    @property
    def comparison_bundle_file_name(self) -> str:
        """Return the comparison bundle artifact filename."""
        return f"{self.slug}-comparison-bundle.md"

    @property
    def review_file_name(self) -> str:
        """Return the implementation-repo review filename."""
        return f"{self.slug}-review.md"

    @property
    def review_judgments_file_name(self) -> str:
        """Return the structured review judgment sidecar filename."""
        return f"{self.slug}-review-judgments.json"


@dataclass(frozen=True, slots=True)
class CommitMetadata:
    """Shared commit identity metadata for commit-pair workflows."""

    original_arg: str
    sha: str
    short_sha: str
    message: str
    ref_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CommitPairEntry(CommitMetadata):
    """Resolved metadata for one commit-pair comparison input."""

    # pylint: disable=too-many-instance-attributes

    label: str
    branch: str
    source: str = "remote"


@dataclass(frozen=True, slots=True)
class CommitPairContext:
    """Resolved metadata for a two-commit comparison workflow."""

    slug: str
    entries: tuple[CommitPairEntry, CommitPairEntry]

    @property
    def marker(self) -> str:
        """Return the stable hidden marker payload for wiki artifacts."""
        left, right = self.entries
        return (
            "<!-- diamond-dev commit-pair: "
            f"left={left.sha} right={right.sha} slug={self.slug} "
            f"left_label={left.label} right_label={right.label} "
            "-->"
        )

    @property
    def index_line(self) -> str:
        """Return the stable wiki index entry for this ordered commit pair."""
        left, right = self.entries
        return (
            f"- `{left.sha}` vs `{right.sha}` -> `{self.slug}` "
            f"({left.label}/{right.label})"
        )

    @property
    def labels(self) -> tuple[str, str]:
        """Return labels in argument order."""
        left, right = self.entries
        return (left.label, right.label)

    @property
    def shas(self) -> tuple[str, str]:
        """Return full SHAs in argument order."""
        left, right = self.entries
        return (left.sha, right.sha)


@dataclass(frozen=True, slots=True)
class WikiContext:
    """Resolved GitHub Gollum wiki repository paths."""

    url: str
    directory: Path
    comparison_file: Path
    comparison_bundle_file: Path
    review_file: Path
    review_judgments_file: Path


@dataclass(frozen=True, slots=True)
class ImplementationBranch:
    """Resolved repository and branch details for one implementation agent."""

    agent_name: str
    repo_dir: Path
    branch: str
    log_prefix: str


@dataclass(frozen=True, slots=True)
class ImplementationContext:
    """Resolved implementation repository paths and branches."""

    branches: tuple[ImplementationBranch, ...]
    base_branch: str = ""

    def with_base_branch(self, base_branch: str) -> ImplementationContext:
        """Return a copy with the resolved remote base branch."""
        return ImplementationContext(
            branches=self.branches,
            base_branch=base_branch,
        )

    @property
    def implementer_names(self) -> tuple[str, ...]:
        """Return implementation agent names in workflow order."""
        return tuple(branch.agent_name for branch in self.branches)

    @property
    def primary_branch(self) -> ImplementationBranch:
        """Return the first implementation branch."""
        try:
            return self.branches[0]
        except (IndexError,) as error:
            raise DiamondDevError("Workflow has no implementation branches") from error

    def branch_for(self, agent_name: str) -> ImplementationBranch:
        """Return branch details for an implementation agent."""
        for branch in self.branches:
            if branch.agent_name == agent_name:
                return branch
        raise DiamondDevError(f"Unknown implementation agent: {agent_name}")

    @property
    def codex_dir(self) -> Path:
        """Return the default Codex repo dir for legacy callers."""
        return self.branch_for("codex").repo_dir

    @property
    def claude_dir(self) -> Path:
        """Return the default Claude repo dir for legacy callers."""
        return self.branch_for("claude").repo_dir

    @property
    def codex_branch(self) -> str:
        """Return the default Codex branch for legacy callers."""
        return self.branch_for("codex").branch

    @property
    def claude_branch(self) -> str:
        """Return the default Claude branch for legacy callers."""
        return self.branch_for("claude").branch


@dataclass(frozen=True, slots=True)
class RunContext:
    """Resolved state for one Diamond Dev run."""

    # pylint: disable=too-many-instance-attributes

    cwd: Path
    config: DiamondDevConfig
    plan: PlanContext
    wiki: WikiContext
    implementation: ImplementationContext
    commit_pair: CommitPairContext | None = None
    dirty_records: tuple[DirtyRecord, ...] = ()
    pr_url: str | None = None

    @property
    def is_commit_pair(self) -> bool:
        """Return whether this run compares two existing commits."""
        return self.commit_pair is not None

    @property
    def comparison_file(self) -> Path:
        """Return the local comparison artifact path."""
        return safe_generated_child_path(self.cwd, LOCAL_COMPARISON_FILE_NAME)

    @property
    def comparison_bundle_file(self) -> Path:
        """Return the local comparison bundle artifact path."""
        return safe_generated_child_path(
            self.cwd,
            self.plan.comparison_bundle_file_name,
        )

    def with_implementation(
        self,
        implementation: ImplementationContext,
    ) -> RunContext:
        """Return a copy with updated implementation repository details."""
        return cast("RunContext", replace(self, implementation=implementation))

    def with_commit_pair_entries(
        self,
        entries: tuple[CommitPairEntry, CommitPairEntry],
    ) -> RunContext:
        """Return a copy with commit-pair and implementation branches synchronized."""
        if self.commit_pair is None:
            raise DiamondDevError("Cannot set commit-pair entries for a plan run")
        if len(entries) != len(self.implementation.branches):
            raise DiamondDevError(
                "Commit-pair entries must match implementation branches",
            )
        commit_pair = self.commit_pair
        implementation = ImplementationContext(
            branches=tuple(
                ImplementationBranch(
                    agent_name=branch.agent_name,
                    repo_dir=branch.repo_dir,
                    branch=entry.branch,
                    log_prefix=branch.log_prefix,
                )
                for branch, entry in zip(
                    self.implementation.branches,
                    entries,
                    strict=True,
                )
            ),
            base_branch=self.implementation.base_branch,
        )
        return cast(
            "RunContext",
            replace(
                self,
                implementation=implementation,
                commit_pair=CommitPairContext(slug=commit_pair.slug, entries=entries),
            ),
        )

    def with_dirty_record(self, dirty_record: DirtyRecord) -> RunContext:
        """Return a copy with an added dirty-file record."""
        return cast(
            "RunContext",
            replace(self, dirty_records=(*self.dirty_records, dirty_record)),
        )

    def with_pr_url(self, pr_url: str) -> RunContext:
        """Return a copy with the created pull request URL."""
        return cast("RunContext", replace(self, pr_url=pr_url))


@dataclass(frozen=True, slots=True, init=False)
class SelectedImplementation:
    """The implementation branch selected from the wiki comparison."""

    accepted_agent: str
    comparison_fixer: str
    repo_dir: Path
    branch: str

    def __init__(
        self,
        *,
        accepted_agent: str,
        comparison_fixer: str | None = None,
        opposite_agent: str | None = None,
        repo_dir: Path,
        branch: str,
    ) -> None:
        """Create selected implementation details.

        `opposite_agent` is accepted as a compatibility alias for older tests and
        callers; new code should pass `comparison_fixer`.
        """
        selected_fixer = comparison_fixer or opposite_agent
        if selected_fixer is None:
            raise DiamondDevError("Selected implementation requires a comparison fixer")
        object.__setattr__(self, "accepted_agent", accepted_agent)
        object.__setattr__(self, "comparison_fixer", selected_fixer)
        object.__setattr__(self, "repo_dir", repo_dir)
        object.__setattr__(self, "branch", branch)

    @property
    def opposite_agent(self) -> str:
        """Return the comparison fixer for legacy callers."""
        return self.comparison_fixer


def resolve_plan_path(*, cwd: Path, plan_path: Path) -> Path:
    """Resolve and validate a markdown plan path."""
    candidate_path = plan_path if plan_path.is_absolute() else cwd / plan_path
    resolved_path = candidate_path.resolve()
    if not resolved_path.is_file():
        raise DiamondDevError(f"Plan file not found: {resolved_path}")
    if resolved_path.suffix.lower() != ".md":
        raise DiamondDevError(f"Plan file must be markdown: {resolved_path}")
    safe_child_path(resolved_path.parent, resolved_path.name)
    return resolved_path


def build_run_context(
    *,
    cwd: Path,
    plan_path: Path,
    config: DiamondDevConfig,
) -> RunContext:
    """Build resolved workflow context from config and a plan path."""
    plan_slug = slug_for_plan(plan_path)
    wiki_url = config.wiki_repository_url or derive_wiki_repository_url(
        config.repository_url,
    )
    wiki_dir = cwd / wiki_directory_name(wiki_url)
    return RunContext(
        cwd=cwd,
        config=config,
        plan=PlanContext(path=plan_path, slug=plan_slug),
        wiki=_wiki_context(wiki_url=wiki_url, wiki_dir=wiki_dir, slug=plan_slug),
        implementation=ImplementationContext(
            branches=tuple(
                _implementation_branch(cwd, plan_slug, agent_name)
                for agent_name in config.workflow.implementers
            ),
        ),
    )


def build_commit_pair_run_context(
    *,
    cwd: Path,
    slug: str,
    entries: tuple[CommitPairEntry, CommitPairEntry],
    config: DiamondDevConfig,
) -> RunContext:
    """Build resolved workflow context for a two-commit comparison."""
    wiki_url = config.wiki_repository_url or derive_wiki_repository_url(
        config.repository_url,
    )
    wiki_dir = cwd / wiki_directory_name(wiki_url)
    return RunContext(
        cwd=cwd,
        config=config,
        plan=PlanContext(path=safe_generated_child_path(cwd, f"{slug}.md"), slug=slug),
        wiki=_wiki_context(wiki_url=wiki_url, wiki_dir=wiki_dir, slug=slug),
        implementation=ImplementationContext(
            branches=tuple(
                ImplementationBranch(
                    agent_name=entry.label,
                    repo_dir=safe_generated_child_path(cwd, f"{entry.label}-{slug}"),
                    branch=entry.branch,
                    log_prefix=entry.label,
                )
                for entry in entries
            ),
        ),
        commit_pair=CommitPairContext(slug=slug, entries=entries),
    )


def selected_implementation(
    context: RunContext,
    accepted_agent: str,
) -> SelectedImplementation:
    """Return the accepted implementation repository and comparison fixer."""
    accepted_branch = context.implementation.branch_for(accepted_agent)
    if context.commit_pair is not None:
        comparison_fixer = _commit_pair_comparison_fixer(context, accepted_agent)
    else:
        comparison_fixer = context.config.workflow.comparison_fixer_for(accepted_agent)
    return SelectedImplementation(
        accepted_agent=accepted_agent,
        comparison_fixer=comparison_fixer,
        repo_dir=accepted_branch.repo_dir,
        branch=accepted_branch.branch,
    )


def _implementation_branch(
    cwd: Path,
    plan_slug: str,
    agent_name: str,
) -> ImplementationBranch:
    return ImplementationBranch(
        agent_name=agent_name,
        repo_dir=safe_generated_child_path(cwd, f"{agent_name}-{plan_slug}"),
        branch=f"{agent_name}/{plan_slug}",
        log_prefix=agent_name,
    )


def _wiki_context(*, wiki_url: str, wiki_dir: Path, slug: str) -> WikiContext:
    return WikiContext(
        url=wiki_url,
        directory=wiki_dir,
        comparison_file=safe_generated_child_path(wiki_dir, f"{slug}-comparison.md"),
        comparison_bundle_file=safe_generated_child_path(
            wiki_dir,
            f"{slug}-comparison-bundle.md",
        ),
        review_file=safe_generated_child_path(wiki_dir, f"{slug}-review.md"),
        review_judgments_file=safe_generated_child_path(
            wiki_dir,
            f"{slug}-review-judgments.json",
        ),
    )


def _commit_pair_comparison_fixer(context: RunContext, accepted_agent: str) -> str:
    if context.config.workflow.comparison_fixer is not None:
        return context.config.workflow.comparison_fixer

    labels = set(context.commit_pair.labels if context.commit_pair is not None else ())
    if labels == {"codex", "claude"}:
        return "claude" if accepted_agent == "codex" else "codex"
    return "codex"
