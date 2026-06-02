"""Workflow data structures for Diamond Dev orchestration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from diamond_dev.errors import DiamondDevError
from diamond_dev.naming import (
    derive_notes_repository_url,
    notes_directory_name,
    slug_for_plan,
)

if TYPE_CHECKING:
    from diamond_dev.acceptance import AgentChoice
    from diamond_dev.config import DiamondDevConfig


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
    def review_file_name(self) -> str:
        """Return the implementation-repo review filename."""
        return f"{self.slug}-review.md"


@dataclass(frozen=True, slots=True)
class NotesContext:
    """Resolved notes repository paths."""

    url: str
    directory: Path
    comparison_file: Path
    review_file: Path


@dataclass(frozen=True, slots=True)
class ImplementationContext:
    """Resolved implementation repository paths and branches."""

    codex_dir: Path
    claude_dir: Path
    codex_branch: str
    claude_branch: str
    base_branch: str = ""

    def with_base_branch(self, base_branch: str) -> ImplementationContext:
        """Return a copy with the resolved remote base branch."""
        return replace(self, base_branch=base_branch)


@dataclass(frozen=True, slots=True)
class RunContext:
    """Resolved state for one Diamond Dev run."""

    cwd: Path
    config: DiamondDevConfig
    plan: PlanContext
    notes: NotesContext
    implementation: ImplementationContext
    comparison_file: Path
    dirty_records: tuple[DirtyRecord, ...] = ()
    pr_url: str | None = None

    def with_implementation(
        self,
        implementation: ImplementationContext,
    ) -> RunContext:
        """Return a copy with updated implementation repository details."""
        return replace(self, implementation=implementation)

    def with_dirty_record(self, dirty_record: DirtyRecord) -> RunContext:
        """Return a copy with an added dirty-file record."""
        return replace(
            self,
            dirty_records=(*self.dirty_records, dirty_record),
        )

    def with_pr_url(self, pr_url: str) -> RunContext:
        """Return a copy with the created pull request URL."""
        return replace(self, pr_url=pr_url)


@dataclass(frozen=True, slots=True)
class SelectedImplementation:
    """The implementation branch selected from comparison notes."""

    accepted_agent: AgentChoice
    opposite_agent: AgentChoice
    repo_dir: Path
    branch: str


def resolve_plan_path(*, cwd: Path, plan_path: Path) -> Path:
    """Resolve and validate a markdown plan path."""
    candidate_path = plan_path if plan_path.is_absolute() else cwd / plan_path
    resolved_path = candidate_path.resolve()
    if not resolved_path.is_file():
        raise DiamondDevError(f"Plan file not found: {resolved_path}")
    if resolved_path.suffix.lower() != ".md":
        raise DiamondDevError(f"Plan file must be markdown: {resolved_path}")
    return resolved_path


def build_run_context(
    *,
    cwd: Path,
    plan_path: Path,
    config: DiamondDevConfig,
) -> RunContext:
    """Build resolved workflow context from config and a plan path."""
    plan_slug = slug_for_plan(plan_path)
    notes_url = config.notes_repository_url or derive_notes_repository_url(
        config.repository_url,
    )
    notes_dir = cwd / notes_directory_name(config.repository_url)
    return RunContext(
        cwd=cwd,
        config=config,
        plan=PlanContext(path=plan_path, slug=plan_slug),
        notes=NotesContext(
            url=notes_url,
            directory=notes_dir,
            comparison_file=notes_dir / f"{plan_slug}-comparison.md",
            review_file=notes_dir / f"{plan_slug}-review.md",
        ),
        implementation=ImplementationContext(
            codex_dir=cwd / f"codex-{plan_slug}",
            claude_dir=cwd / f"claude-{plan_slug}",
            codex_branch=f"codex/{plan_slug}",
            claude_branch=f"claude/{plan_slug}",
        ),
        comparison_file=cwd / "comparison.md",
    )


def selected_implementation(
    context: RunContext,
    accepted_agent: AgentChoice,
) -> SelectedImplementation:
    """Return the accepted implementation repository and opposite agent."""
    if accepted_agent == "codex":
        return SelectedImplementation(
            accepted_agent="codex",
            opposite_agent="claude",
            repo_dir=context.implementation.codex_dir,
            branch=context.implementation.codex_branch,
        )
    return SelectedImplementation(
        accepted_agent="claude",
        opposite_agent="codex",
        repo_dir=context.implementation.claude_dir,
        branch=context.implementation.claude_branch,
    )
