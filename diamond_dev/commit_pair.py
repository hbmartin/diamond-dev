"""Two-commit comparison helpers."""

# pylint: disable=too-many-lines

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from loguru import logger

from diamond_dev.commands import build_codex_command
from diamond_dev.errors import CommandFailureError, DiamondDevError
from diamond_dev.naming import slugify
from diamond_dev.workflow import (
    CommitMetadata,
    CommitPairContext,
    CommitPairEntry,
    RunContext,
    safe_generated_child_path,
    write_generated_child_text,
)

if TYPE_CHECKING:
    from diamond_dev.executor import CommandExecutor

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
_LABEL_NAMES: Final = ("codex", "claude")
_ORIGIN_REMOTE_PREFIX: Final = "origin/"


@dataclass(frozen=True, slots=True)
class ResolvedCommitInput(CommitMetadata):
    """Commit metadata resolved before final workflow clone names are known."""

    explicit_branch: str | None
    source: str


@dataclass(frozen=True, slots=True)
class CommitPairRecord:
    """One stored wiki commit-pair slug record."""

    left_sha: str
    right_sha: str
    slug: str


def resolve_commit_pair_inputs(
    *,
    cwd: Path,
    repository_url: str,
    runner: CommandExecutor,
    commit_args: tuple[str, str],
) -> tuple[ResolvedCommitInput, ResolvedCommitInput]:
    """Resolve two commit-ish arguments through remote then trusted local fallback."""
    with tempfile.TemporaryDirectory(
        prefix=".diamond-dev-resolver-",
        dir=cwd,
    ) as temp_dir:
        resolver_dir = Path(temp_dir) / "repo"
        runner.run(
            ("git", "clone", repository_url, str(resolver_dir)),
            cwd=cwd,
            log_name="commit-resolver-clone",
        )
        resolved = tuple(
            _resolve_one_input(
                cwd=cwd,
                resolver_dir=resolver_dir,
                repository_url=repository_url,
                runner=runner,
                commit_arg=commit_arg,
                index=index,
            )
            for index, commit_arg in enumerate(commit_args, start=1)
        )

    left, right = resolved
    if left.sha == right.sha:
        raise DiamondDevError(
            "Commit comparison requires two distinct commits; both arguments "
            f"resolved to {left.sha}",
        )
    return left, right


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


def _resolve_one_input(  # noqa: PLR0913
    *,
    cwd: Path,
    resolver_dir: Path,
    repository_url: str,
    runner: CommandExecutor,
    commit_arg: str,
    index: int,
) -> ResolvedCommitInput:
    if resolved := _resolve_in_resolver(
        runner=runner,
        resolver_dir=resolver_dir,
        commit_arg=commit_arg,
        index=index,
    ):
        return resolved

    if not _cwd_origin_matches(
        cwd=cwd,
        repository_url=repository_url,
        runner=runner,
    ):
        raise DiamondDevError(
            "Commit not reachable from configured remote and invocation "
            f"repository origin does not match `repository_url`: {commit_arg}",
        )

    if resolved := _resolve_from_local_fallback(
        cwd=cwd,
        resolver_dir=resolver_dir,
        runner=runner,
        commit_arg=commit_arg,
        index=index,
    ):
        return resolved
    raise DiamondDevError(
        "Commit not reachable from configured remote or trusted local fallback: "
        f"{commit_arg}",
    )


def _resolve_in_resolver(
    *,
    runner: CommandExecutor,
    resolver_dir: Path,
    commit_arg: str,
    index: int,
) -> ResolvedCommitInput | None:
    sha = _resolve_commitish(
        runner=runner,
        repo_dir=resolver_dir,
        commit_arg=commit_arg,
        log_prefix=f"commit-{index}-remote",
    )
    if sha is None:
        return None
    return _commit_metadata(
        runner=runner,
        repo_dir=resolver_dir,
        commit_arg=commit_arg,
        sha=sha,
        source="remote",
        log_prefix=f"commit-{index}-remote",
        local_ref_names=(),
    )


def _resolve_from_local_fallback(
    *,
    cwd: Path,
    resolver_dir: Path,
    runner: CommandExecutor,
    commit_arg: str,
    index: int,
) -> ResolvedCommitInput | None:
    fetch_result = runner.run(
        ("git", "fetch", str(cwd), commit_arg),
        cwd=resolver_dir,
        log_name=f"commit-{index}-local-fetch",
        check=False,
    )
    if fetch_result.returncode != 0:
        return None
    sha = _resolve_ref(
        runner=runner,
        repo_dir=resolver_dir,
        ref="FETCH_HEAD",
        log_name=f"commit-{index}-local-fetch-head",
    )
    if sha is None:
        return None
    local_ref_names = _local_ref_names(
        cwd=cwd,
        runner=runner,
        sha=sha,
        log_prefix=f"commit-{index}-local",
    )
    return _commit_metadata(
        runner=runner,
        repo_dir=resolver_dir,
        commit_arg=commit_arg,
        sha=sha,
        source="local",
        log_prefix=f"commit-{index}-local",
        local_ref_names=local_ref_names,
    )


def _commit_metadata(  # noqa: PLR0913
    *,
    runner: CommandExecutor,
    repo_dir: Path,
    commit_arg: str,
    sha: str,
    source: str,
    log_prefix: str,
    local_ref_names: tuple[str, ...],
) -> ResolvedCommitInput:
    return ResolvedCommitInput(
        original_arg=commit_arg,
        sha=sha,
        short_sha=_short_sha(runner, repo_dir, sha, log_name=f"{log_prefix}-short-sha"),
        message=_commit_message(
            runner,
            repo_dir,
            sha,
            log_name=f"{log_prefix}-message",
        ),
        ref_names=tuple(
            dict.fromkeys(
                (
                    *_containing_ref_names(
                        runner,
                        repo_dir,
                        sha,
                        log_name=f"{log_prefix}-containing-refs",
                    ),
                    *local_ref_names,
                ),
            ),
        ),
        explicit_branch=_explicit_branch_for_arg(
            runner=runner,
            repo_dir=repo_dir,
            commit_arg=commit_arg,
            log_prefix=log_prefix,
        ),
        source=source,
    )


def _resolve_commitish(
    *,
    runner: CommandExecutor,
    repo_dir: Path,
    commit_arg: str,
    log_prefix: str,
) -> str | None:
    for ref in _candidate_refs(commit_arg):
        if sha := _resolve_ref(
            runner=runner,
            repo_dir=repo_dir,
            ref=ref,
            log_name=f"{log_prefix}-resolve-{slugify(ref) or 'ref'}",
        ):
            return sha
    return None


def _resolve_ref(
    *,
    runner: CommandExecutor,
    repo_dir: Path,
    ref: str,
    log_name: str,
) -> str | None:
    result = runner.run(
        ("git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"),
        cwd=repo_dir,
        log_name=log_name,
        check=False,
    )
    if result.returncode == 0:
        lines = result.output.strip().splitlines()
        return lines[-1] if lines else None
    return None


def _candidate_refs(commit_arg: str) -> tuple[str, ...]:
    candidates = [commit_arg]
    if not commit_arg.startswith((_ORIGIN_REMOTE_PREFIX, "refs/")):
        candidates.append(f"{_ORIGIN_REMOTE_PREFIX}{commit_arg}")
    return tuple(dict.fromkeys(candidates))


def _short_sha(
    runner: CommandExecutor,
    repo_dir: Path,
    sha: str,
    *,
    log_name: str,
) -> str:
    result = runner.run(
        ("git", "rev-parse", "--short=12", sha),
        cwd=repo_dir,
        log_name=log_name,
    )
    lines = result.output.strip().splitlines()
    if not lines:
        raise DiamondDevError(f"Failed to get short SHA for {sha}")
    return lines[-1]


def _commit_message(
    runner: CommandExecutor,
    repo_dir: Path,
    sha: str,
    *,
    log_name: str,
) -> str:
    result = runner.run(
        ("git", "log", "-1", "--format=%B", sha),
        cwd=repo_dir,
        log_name=log_name,
    )
    return result.output.strip()


def _containing_ref_names(
    runner: CommandExecutor,
    repo_dir: Path,
    sha: str,
    *,
    log_name: str,
) -> tuple[str, ...]:
    result = runner.run(
        ("git", "branch", "-a", "--contains", sha, "--format=%(refname:short)"),
        cwd=repo_dir,
        log_name=log_name,
        check=False,
    )
    if result.returncode != 0:
        return ()
    return _normalize_branch_names(result.output.splitlines())


def _local_ref_names(
    *,
    cwd: Path,
    runner: CommandExecutor,
    sha: str,
    log_prefix: str,
) -> tuple[str, ...]:
    result = runner.run(
        ("git", "branch", "--contains", sha, "--format=%(refname:short)"),
        cwd=cwd,
        log_name=f"{log_prefix}-containing-local-refs",
        check=False,
    )
    if result.returncode != 0:
        return ()
    return _normalize_branch_names(result.output.splitlines())


def _normalize_branch_names(ref_names: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for ref_name in ref_names:
        clean_ref = ref_name.strip().lstrip("*").strip()
        clean_ref = clean_ref.removeprefix("remotes/")
        if clean_ref == f"{_ORIGIN_REMOTE_PREFIX}HEAD" or " -> " in clean_ref:
            continue
        clean_ref = clean_ref.removeprefix(_ORIGIN_REMOTE_PREFIX)
        if clean_ref and clean_ref not in normalized:
            normalized.append(clean_ref)
    return tuple(normalized)


def _explicit_branch_for_arg(
    *,
    runner: CommandExecutor,
    repo_dir: Path,
    commit_arg: str,
    log_prefix: str,
) -> str | None:
    normalized_arg = _normalize_explicit_branch_name(commit_arg)
    if _branch_ref_exists(
        runner=runner,
        repo_dir=repo_dir,
        branch=normalized_arg,
        log_name=f"{log_prefix}-explicit-branch-{slugify(normalized_arg) or 'arg'}",
    ):
        return normalized_arg
    return None


def _normalize_explicit_branch_name(commit_arg: str) -> str:
    return (
        commit_arg.removeprefix("refs/heads/")
        .removeprefix("refs/remotes/origin/")
        .removeprefix(_ORIGIN_REMOTE_PREFIX)
    )


def _branch_ref_exists(
    *,
    runner: CommandExecutor,
    repo_dir: Path,
    branch: str,
    log_name: str,
) -> bool:
    for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        result = runner.run(
            ("git", "show-ref", "--verify", "--quiet", ref),
            cwd=repo_dir,
            log_name=f"{log_name}-{slugify(ref) or 'ref'}",
            check=False,
        )
        if result.returncode == 0:
            return True
    return False


def _cwd_origin_matches(
    *,
    cwd: Path,
    repository_url: str,
    runner: CommandExecutor,
) -> bool:
    if not shutil.which("git"):
        return False
    result = runner.run(
        ("git", "remote", "get-url", "origin"),
        cwd=cwd,
        log_name="commit-local-origin-url",
        check=False,
    )
    return result.returncode == 0 and _normalized_repository_url(
        result.output,
    ) == _normalized_repository_url(repository_url)


def _normalized_repository_url(url: str) -> str:
    clean_url = url.strip().rstrip("/")
    if not clean_url:
        return ""

    if "://" in clean_url:
        parsed = urlsplit(clean_url)
        if parsed.scheme == "file":
            return _strip_repository_url_suffix(parsed.path)

        host = (parsed.hostname or "").lower()
        if not host:
            return _strip_repository_url_suffix(clean_url)

        port = _normalized_url_port(parsed.scheme, parsed.port)
        path = parsed.path.strip("/")
        return _strip_repository_url_suffix(f"{host}{port}/{path}")

    if not re.match(r"^[a-zA-Z]:[\\/]", clean_url) and (
        scp_match := re.match(
            r"^(?:[^/@:]+@)?(?P<host>[^/:]+):(?P<path>.+)$",
            clean_url,
        )
    ):
        host = scp_match.group("host").lower()
        path = scp_match.group("path")
        return _strip_repository_url_suffix(f"{host}/{path.strip('/')}")

    return _strip_repository_url_suffix(clean_url)


def _normalized_url_port(scheme: str, port: int | None) -> str:
    if port is None:
        return ""
    default_ports = {
        "git": 9418,
        "http": 80,
        "https": 443,
        "ssh": 22,
    }
    if default_ports.get(scheme) == port:
        return ""
    return f":{port}"


def _strip_repository_url_suffix(url: str) -> str:
    return url.rstrip("/").removesuffix(".git")


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
