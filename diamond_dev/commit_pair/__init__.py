"""Two-commit comparison helpers."""

from __future__ import annotations

from diamond_dev.commit_pair.entries import (
    build_commit_pair_entries,
    commit_pair_entries_avoiding_base_branch,
    generated_commit_pair_branch,
    infer_commit_labels,
)
from diamond_dev.commit_pair.records import (
    COMMIT_PAIR_INDEX_FILE_NAME,
    CommitPairRecord,
    choose_commit_pair_slug,
    comparison_has_matching_commit_pair_marker,
    discover_commit_pair_slug,
    ensure_commit_pair_marker,
    upsert_commit_pair_index,
)
from diamond_dev.commit_pair.resolve import (
    ResolvedCommitInput,
    resolve_commit_pair_inputs,
)

__all__ = (
    "COMMIT_PAIR_INDEX_FILE_NAME",
    "CommitPairRecord",
    "ResolvedCommitInput",
    "build_commit_pair_entries",
    "choose_commit_pair_slug",
    "commit_pair_entries_avoiding_base_branch",
    "comparison_has_matching_commit_pair_marker",
    "discover_commit_pair_slug",
    "ensure_commit_pair_marker",
    "generated_commit_pair_branch",
    "infer_commit_labels",
    "resolve_commit_pair_inputs",
    "upsert_commit_pair_index",
)
