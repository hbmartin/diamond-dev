"""Label inference and workflow entries for commit-pair comparisons."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

from diamond_dev.errors import DiamondDevError
from diamond_dev.workflow import CommitPairEntry

if TYPE_CHECKING:
    from diamond_dev.commit_pair.resolve import ResolvedCommitInput
    from diamond_dev.workflow import CommitPairContext

_LABEL_NAMES: Final = ("codex", "claude")


def infer_commit_labels(
    left: ResolvedCommitInput,
    right: ResolvedCommitInput,
) -> tuple[str, str]:
    """Infer comparison labels from commit messages first, then refs."""
    left_matches = _label_matches((left.message,))
    right_matches = _label_matches((right.message,))
    if not left_matches:
        left_matches = _label_matches((*left.ref_names, left.original_arg))
    if not right_matches:
        right_matches = _label_matches((*right.ref_names, right.original_arg))

    left_unique = _unique_match(left_matches)
    right_unique = _unique_match(right_matches)
    labels = ("a", "b")
    has_conflict = (
        (left_matches and left_unique is None)
        or (right_matches and right_unique is None)
    )
    if has_conflict:
        return labels
    if (
        left_unique is not None
        and right_unique is not None
        and left_unique != right_unique
    ):
        labels = (left_unique, right_unique)
    elif left_unique is not None and right_unique is None:
        labels = (left_unique, _other_label(left_unique))
    elif right_unique is not None and left_unique is None:
        labels = (_other_label(right_unique), right_unique)
    return labels


def build_commit_pair_entries(
    *,
    resolved: tuple[ResolvedCommitInput, ResolvedCommitInput],
    labels: tuple[str, str],
    slug: str,
) -> tuple[CommitPairEntry, CommitPairEntry]:
    """Build final commit entries after the stable slug is known."""
    left, right = (
        CommitPairEntry(
            label=label,
            original_arg=commit.original_arg,
            sha=commit.sha,
            short_sha=commit.short_sha,
            message=commit.message,
            ref_names=commit.ref_names,
            branch=_branch_for_commit(commit, label, slug),
            source=commit.source,
        )
        for commit, label in zip(resolved, labels, strict=True)
    )
    entries = (left, right)
    if _has_duplicate_branches(entries):
        return _generated_entry_pair(entries, slug)
    return entries


def commit_pair_entries_avoiding_base_branch(
    commit_pair: CommitPairContext,
    base_branch: str,
) -> tuple[CommitPairEntry, CommitPairEntry]:
    """Return entries that avoid using the resolved base branch."""
    if not base_branch:
        return commit_pair.entries
    if not any(entry.branch == base_branch for entry in commit_pair.entries):
        return commit_pair.entries

    left, right = commit_pair.entries
    adjusted_entries = (
        _entry_with_generated_branch(left, commit_pair.slug)
        if left.branch == base_branch
        else left,
        _entry_with_generated_branch(right, commit_pair.slug)
        if right.branch == base_branch
        else right,
    )
    if _has_duplicate_branches(adjusted_entries):
        return _generated_entry_pair(commit_pair.entries, commit_pair.slug)
    return adjusted_entries


def generated_commit_pair_branch(slug: str, label: str) -> str:
    """Return the generated workflow branch for one commit-pair entry."""
    return f"diamond-dev/{slug}/{label}"


def _label_matches(values: Sequence[str]) -> frozenset[str]:
    matches: set[str] = set()
    for value in values:
        for label in _LABEL_NAMES:
            if re.search(rf"(?<![a-z0-9]){label}(?![a-z0-9])", value.lower()):
                matches.add(label)
    return frozenset(matches)


def _unique_match(matches: frozenset[str]) -> str | None:
    if len(matches) == 1:
        return next(iter(matches))
    return None


def _other_label(label: str) -> str:
    return "claude" if label == "codex" else "codex"


def _branch_for_commit(
    commit: ResolvedCommitInput,
    label: str,
    slug: str,
) -> str:
    if commit.explicit_branch is not None:
        return commit.explicit_branch
    if len(commit.ref_names) == 1:
        return commit.ref_names[0]
    return generated_commit_pair_branch(slug, label)


def _generated_entry_pair(
    entries: tuple[CommitPairEntry, CommitPairEntry],
    slug: str,
) -> tuple[CommitPairEntry, CommitPairEntry]:
    left, right = entries
    entry_pair = (
        _entry_with_generated_branch(left, slug),
        _entry_with_generated_branch(right, slug),
    )
    if _has_duplicate_branches(entry_pair):
        branches = ", ".join(entry.branch for entry in entry_pair)
        raise DiamondDevError(
            "Generated commit-pair branches must be distinct; "
            f"got duplicate branch names from labels: {branches}",
        )
    return entry_pair


def _entry_with_generated_branch(
    entry: CommitPairEntry,
    slug: str,
) -> CommitPairEntry:
    return CommitPairEntry(
        original_arg=entry.original_arg,
        sha=entry.sha,
        short_sha=entry.short_sha,
        message=entry.message,
        ref_names=entry.ref_names,
        label=entry.label,
        branch=generated_commit_pair_branch(slug, entry.label),
        source=entry.source,
    )


def _has_duplicate_branches(entries: Sequence[CommitPairEntry]) -> bool:
    branches = [entry.branch for entry in entries]
    return len(set(branches)) != len(branches)
