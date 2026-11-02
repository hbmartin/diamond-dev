"""Wiki markers, index records, and slug selection for commit pairs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from loguru import logger

from diamond_dev.commands import build_codex_command
from diamond_dev.errors import CommandFailureError
from diamond_dev.naming import slugify
from diamond_dev.workflow import (
    safe_generated_child_path,
    write_generated_child_text,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from diamond_dev.commit_pair.resolve import ResolvedCommitInput
    from diamond_dev.executor import CommandExecutor
    from diamond_dev.workflow import CommitPairContext, RunContext

COMMIT_PAIR_INDEX_FILE_NAME: Final = "diamond-dev-commit-comparisons.md"
_COMMIT_SHA_PATTERN: Final = r"[0-9a-f]{40}"
_SLUG_PATTERN: Final = r"[a-z0-9][a-z0-9-]*"
_MARKER_PATTERN: Final = re.compile(
    r"<!--\s*diamond-dev commit-pair:\s*"
    rf"left=(?P<left>{_COMMIT_SHA_PATTERN})\s+"
    rf"right=(?P<right>{_COMMIT_SHA_PATTERN})\s+"
    rf"slug=(?P<slug>{_SLUG_PATTERN})"
    r"[^>]*-->",
)
_INDEX_PATTERN: Final = re.compile(
    rf"^- `(?P<left>{_COMMIT_SHA_PATTERN})` vs "
    rf"`(?P<right>{_COMMIT_SHA_PATTERN})` -> `(?P<slug>{_SLUG_PATTERN})`",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class CommitPairRecord:
    """One stored wiki commit-pair slug record."""

    left_sha: str
    right_sha: str
    slug: str


def discover_commit_pair_slug(
    *,
    wiki_dir: Path,
    left_sha: str,
    right_sha: str,
) -> str | None:
    """Return a stored slug for an ordered commit pair, if one exists."""
    for record in _iter_commit_pair_records(wiki_dir):
        if record.left_sha == left_sha and record.right_sha == right_sha:
            return record.slug
    return None


def choose_commit_pair_slug(
    *,
    cwd: Path,
    wiki_dir: Path,
    runner: CommandExecutor,
    resolved: tuple[ResolvedCommitInput, ResolvedCommitInput],
) -> str:
    """Discover or generate a stable slug for an ordered commit pair."""
    left, right = resolved
    if stored_slug := discover_commit_pair_slug(
        wiki_dir=wiki_dir,
        left_sha=left.sha,
        right_sha=right.sha,
    ):
        return stored_slug

    slug = _codex_generated_slug(cwd=cwd, runner=runner, left=left, right=right)
    if not slug:
        slug = f"compare-{left.short_sha}-vs-{right.short_sha}"
    if _slug_used_for_different_pair(
        wiki_dir=wiki_dir,
        slug=slug,
        left_sha=left.sha,
        right_sha=right.sha,
    ):
        slug = f"{slug}-{left.short_sha}-vs-{right.short_sha}"
    return slug


def comparison_has_matching_commit_pair_marker(
    comparison_markdown: str,
    context: RunContext,
) -> bool:
    """Return whether markdown belongs to this commit-pair run."""
    if context.commit_pair is None:
        return True
    left_sha, right_sha = context.commit_pair.shas
    for record in _records_from_text(comparison_markdown):
        if (
            record.left_sha == left_sha
            and record.right_sha == right_sha
            and record.slug == context.commit_pair.slug
        ):
            return True
    return False


def ensure_commit_pair_marker(
    comparison_markdown: str,
    context: RunContext,
) -> str:
    """Ensure comparison markdown includes the commit-pair marker."""
    if context.commit_pair is None:
        return comparison_markdown
    if comparison_has_matching_commit_pair_marker(comparison_markdown, context):
        return comparison_markdown
    return f"{context.commit_pair.marker}\n{comparison_markdown.lstrip()}"


def upsert_commit_pair_index(
    wiki_dir: Path,
    commit_pair: CommitPairContext,
) -> bool:
    """Upsert the ordered commit-pair slug in the wiki index."""
    index_path = safe_generated_child_path(wiki_dir, COMMIT_PAIR_INDEX_FILE_NAME)
    records = (
        _records_from_text(index_path.read_text(encoding="utf-8"))
        if index_path.is_file()
        else ()
    )
    left_sha, right_sha = commit_pair.shas
    for record in records:
        if (
            record.left_sha == left_sha
            and record.right_sha == right_sha
            and record.slug == commit_pair.slug
        ):
            return False

    write_generated_child_text(
        wiki_dir,
        COMMIT_PAIR_INDEX_FILE_NAME,
        _render_commit_pair_index(records, new_index_line=commit_pair.index_line),
    )
    return True


def _render_commit_pair_index(
    records: Sequence[CommitPairRecord],
    *,
    new_index_line: str,
) -> str:
    lines = [
        "# Diamond Dev commit comparisons",
        "",
        *(_commit_pair_record_line(record) for record in records),
        new_index_line,
    ]
    return "\n".join(lines) + "\n"


def _commit_pair_record_line(record: CommitPairRecord) -> str:
    return f"- `{record.left_sha}` vs `{record.right_sha}` -> `{record.slug}`"


def _codex_generated_slug(
    *,
    cwd: Path,
    runner: CommandExecutor,
    left: ResolvedCommitInput,
    right: ResolvedCommitInput,
) -> str | None:
    prompt = (
        "Generate one concise git branch slug for comparing these two commits. "
        "Return only the slug words, no explanation.\n\n"
        f"Commit A ({left.short_sha}) message:\n{left.message}\n\n"
        f"Commit B ({right.short_sha}) message:\n{right.message}\n"
    )
    try:
        result = runner.run(
            build_codex_command(cwd, prompt),
            cwd=cwd,
            log_name="codex-commit-pair-slug",
            check=False,
        )
    except (CommandFailureError,) as error:
        logger.warning("Codex commit-pair slug generation failed: {}", error)
        return None
    if result.returncode != 0:
        logger.warning(
            "Codex commit-pair slug generation exited with {}",
            result.returncode,
        )
        return None
    for line in result.output.splitlines():
        if slug := slugify(line):
            return slug
    return None


def _slug_used_for_different_pair(
    *,
    wiki_dir: Path,
    slug: str,
    left_sha: str,
    right_sha: str,
) -> bool:
    comparison_path = safe_generated_child_path(wiki_dir, f"{slug}-comparison.md")
    if comparison_path.is_file():
        comparison_records = _records_from_text(
            comparison_path.read_text(encoding="utf-8"),
        )
        if not comparison_records:
            # Markerless legacy comparisons may own this slug; avoid overwriting them.
            return True
    return any(
        record.slug == slug
        and (record.left_sha != left_sha or record.right_sha != right_sha)
        for record in _iter_commit_pair_records(wiki_dir)
    )


def _iter_commit_pair_records(wiki_dir: Path) -> tuple[CommitPairRecord, ...]:
    records: list[CommitPairRecord] = []
    index_path = safe_generated_child_path(wiki_dir, COMMIT_PAIR_INDEX_FILE_NAME)
    if index_path.is_file():
        records.extend(_records_from_text(index_path.read_text(encoding="utf-8")))
    if wiki_dir.is_dir():
        for comparison_path in wiki_dir.glob("*-comparison.md"):
            records.extend(
                _records_from_text(comparison_path.read_text(encoding="utf-8")),
            )
    return tuple(records)


def _records_from_text(text: str) -> tuple[CommitPairRecord, ...]:
    records: list[CommitPairRecord] = []
    for pattern in (_MARKER_PATTERN, _INDEX_PATTERN):
        records.extend(
            CommitPairRecord(
                left_sha=match.group("left"),
                right_sha=match.group("right"),
                slug=match.group("slug"),
            )
            for match in pattern.finditer(text)
        )
    return tuple(records)
