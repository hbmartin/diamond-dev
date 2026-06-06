"""Tests for two-commit comparison helpers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from diamond_dev.commit_pair import (
    ResolvedCommitInput,
    build_commit_pair_entries,
    choose_commit_pair_slug,
    comparison_has_matching_commit_pair_marker,
    infer_commit_labels,
    resolve_commit_pair_inputs,
    upsert_commit_pair_index,
)
from diamond_dev.config import DiamondDevConfig
from diamond_dev.errors import DiamondDevError
from diamond_dev.executor import CommandResult, CommandRunner
from diamond_dev.workflow import (
    CommitPairContext,
    CommitPairEntry,
    ImplementationBranch,
    ImplementationContext,
    PlanContext,
    RunContext,
    WikiContext,
)


def test_infer_commit_labels_prefers_messages_over_refs() -> None:
    left = _resolved(message="Claude implementation", refs=("codex/feature",))
    right = _resolved(message="Codex implementation", refs=("claude/feature",))

    assert infer_commit_labels(left, right) == ("claude", "codex")


def test_infer_commit_labels_uses_refs_and_infers_missing_pair() -> None:
    left = _resolved(message="Implementation", refs=("codex/feature",))
    right = _resolved(message="Implementation", refs=())

    assert infer_commit_labels(left, right) == ("codex", "claude")


def test_infer_commit_labels_falls_back_to_a_b_for_conflicts() -> None:
    left = _resolved(message="Codex and Claude implementation", refs=())
    right = _resolved(message="Implementation", refs=("codex/other",))

    assert infer_commit_labels(left, right) == ("a", "b")


def test_build_commit_pair_entries_selects_branch_priority() -> None:
    entries = build_commit_pair_entries(
        resolved=(
            _resolved(original_arg="feature", explicit_branch="feature"),
            _resolved(refs=("single-containing",)),
        ),
        labels=("codex", "claude"),
        slug="my-compare",
    )

    assert entries[0].branch == "feature"
    assert entries[1].branch == "single-containing"


def test_build_commit_pair_entries_generates_branch_for_ambiguous_refs() -> None:
    entries = build_commit_pair_entries(
        resolved=(
            _resolved(refs=("one", "two")),
            _resolved(refs=()),
        ),
        labels=("a", "b"),
        slug="my-compare",
    )

    assert entries[0].branch == "diamond-dev/my-compare/a"
    assert entries[1].branch == "diamond-dev/my-compare/b"


def test_commit_pair_slug_discovers_wiki_index(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "repo.wiki"
    wiki_dir.mkdir()
    left = "a" * 40
    right = "b" * 40
    context = _context_for_pair(tmp_path, left_sha=left, right_sha=right)

    assert upsert_commit_pair_index(wiki_dir, context.commit_pair)
    slug = choose_commit_pair_slug(
        cwd=tmp_path,
        wiki_dir=wiki_dir,
        runner=_SlugRunner(output="ignored"),
        resolved=(
            _resolved(sha=left, short_sha="aaaaaaaaaaaa"),
            _resolved(sha=right, short_sha="bbbbbbbbbbbb"),
        ),
    )

    assert slug == context.commit_pair.slug


def test_commit_pair_slug_falls_back_and_appends_on_collision(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "repo.wiki"
    wiki_dir.mkdir()
    (wiki_dir / "compare-aaaaaaaaaaaa-vs-bbbbbbbbbbbb-comparison.md").write_text(
        "legacy page without marker\n",
        encoding="utf-8",
    )

    slug = choose_commit_pair_slug(
        cwd=tmp_path,
        wiki_dir=wiki_dir,
        runner=_SlugRunner(returncode=1),
        resolved=(
            _resolved(sha="a" * 40, short_sha="aaaaaaaaaaaa"),
            _resolved(sha="b" * 40, short_sha="bbbbbbbbbbbb"),
        ),
    )

    assert slug == (
        "compare-aaaaaaaaaaaa-vs-bbbbbbbbbbbb-aaaaaaaaaaaa-vs-bbbbbbbbbbbb"
    )


def test_comparison_marker_match_requires_ordered_pair(tmp_path: Path) -> None:
    context = _context_for_pair(tmp_path, left_sha="a" * 40, right_sha="b" * 40)
    marker = context.commit_pair.marker
    reversed_context = _context_for_pair(
        tmp_path,
        left_sha="b" * 40,
        right_sha="a" * 40,
    )

    assert comparison_has_matching_commit_pair_marker(marker, context)
    assert not comparison_has_matching_commit_pair_marker(marker, reversed_context)


def test_resolve_commit_pair_inputs_reads_remote_commits(tmp_path: Path) -> None:
    runner = CommandRunner(tmp_path / "logs")
    remote, worktree = _git_fixture(tmp_path, runner)
    left_sha = _commit_file(runner, worktree, "codex.txt", "Codex change\n")
    runner.run(
        ("git", "checkout", "-b", "codex/feature"),
        cwd=worktree,
        log_name="checkout-codex",
    )
    runner.run(
        ("git", "push", "origin", "codex/feature"),
        cwd=worktree,
        log_name="push-codex",
    )
    runner.run(
        ("git", "checkout", "main"),
        cwd=worktree,
        log_name="checkout-main",
    )
    right_sha = _commit_file(runner, worktree, "claude.txt", "Claude change\n")
    runner.run(
        ("git", "checkout", "-b", "claude/feature"),
        cwd=worktree,
        log_name="checkout-claude",
    )
    runner.run(
        ("git", "push", "origin", "claude/feature"),
        cwd=worktree,
        log_name="push-claude",
    )

    left, right = resolve_commit_pair_inputs(
        cwd=worktree,
        repository_url=str(remote),
        runner=runner,
        commit_args=("codex/feature", "claude/feature"),
    )

    assert left.sha == left_sha
    assert left.explicit_branch == "codex/feature"
    assert right.sha == right_sha
    assert right.explicit_branch == "claude/feature"


def test_resolve_commit_pair_inputs_uses_matching_local_origin(
    tmp_path: Path,
) -> None:
    runner = CommandRunner(tmp_path / "logs")
    remote, worktree = _git_fixture(tmp_path, runner)
    pushed_sha = _commit_file(runner, worktree, "pushed.txt", "Codex pushed\n")
    runner.run(
        ("git", "push", "origin", "main"),
        cwd=worktree,
        log_name="push-main",
    )
    local_sha = _commit_file(runner, worktree, "local.txt", "Claude local\n")

    left, right = resolve_commit_pair_inputs(
        cwd=worktree,
        repository_url=str(remote),
        runner=runner,
        commit_args=(pushed_sha, local_sha),
    )

    assert left.source == "remote"
    assert right.source == "local"
    assert right.sha == local_sha


def test_resolve_commit_pair_inputs_rejects_same_sha(tmp_path: Path) -> None:
    runner = CommandRunner(tmp_path / "logs")
    remote, worktree = _git_fixture(tmp_path, runner)
    sha = _commit_file(runner, worktree, "same.txt", "Codex same\n")
    runner.run(
        ("git", "push", "origin", "main"),
        cwd=worktree,
        log_name="push-main",
    )

    with pytest.raises(DiamondDevError, match="two distinct commits"):
        resolve_commit_pair_inputs(
            cwd=worktree,
            repository_url=str(remote),
            runner=runner,
            commit_args=(sha, sha),
        )


def _git_fixture(
    tmp_path: Path,
    runner: CommandRunner,
) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    worktree = tmp_path / "worktree"
    runner.run(("git", "init", "--bare", str(remote)), cwd=tmp_path, log_name="init")
    runner.run(
        ("git", "clone", str(remote), str(worktree)),
        cwd=tmp_path,
        log_name="clone",
    )
    runner.run(
        ("git", "config", "user.email", "test@example.com"),
        cwd=worktree,
        log_name="config-email",
    )
    runner.run(
        ("git", "config", "user.name", "Test User"),
        cwd=worktree,
        log_name="config-name",
    )
    runner.run(
        ("git", "checkout", "-b", "main"),
        cwd=worktree,
        log_name="initial-main",
    )
    _commit_file(runner, worktree, "README.md", "Initial\n")
    runner.run(("git", "push", "-u", "origin", "main"), cwd=worktree, log_name="push")
    runner.run(
        ("git", "symbolic-ref", "HEAD", "refs/heads/main"),
        cwd=remote,
        log_name="remote-head",
    )
    return remote, worktree


def _commit_file(
    runner: CommandRunner,
    worktree: Path,
    file_name: str,
    message: str,
) -> str:
    (worktree / file_name).write_text(message, encoding="utf-8")
    runner.run(("git", "add", file_name), cwd=worktree, log_name=f"add-{file_name}")
    runner.run(("git", "commit", "-m", message), cwd=worktree, log_name=f"commit-{file_name}")
    result = runner.run(("git", "rev-parse", "HEAD"), cwd=worktree, log_name=f"sha-{file_name}")
    return result.output.strip()


def _resolved(
    *,
    original_arg: str = "abc123",
    sha: str = "a" * 40,
    short_sha: str = "aaaaaaaaaaaa",
    message: str = "Implementation",
    refs: tuple[str, ...] = (),
    explicit_branch: str | None = None,
    source: str = "remote",
) -> ResolvedCommitInput:
    return ResolvedCommitInput(
        original_arg=original_arg,
        sha=sha,
        short_sha=short_sha,
        message=message,
        ref_names=refs,
        explicit_branch=explicit_branch,
        source=source,
    )


def _context_for_pair(tmp_path: Path, *, left_sha: str, right_sha: str) -> RunContext:
    entries = (
        CommitPairEntry(
            label="a",
            original_arg=left_sha,
            sha=left_sha,
            short_sha=left_sha[:12],
            message="Left",
            ref_names=(),
            branch="diamond-dev/compare/a",
        ),
        CommitPairEntry(
            label="b",
            original_arg=right_sha,
            sha=right_sha,
            short_sha=right_sha[:12],
            message="Right",
            ref_names=(),
            branch="diamond-dev/compare/b",
        ),
    )
    return RunContext(
        cwd=tmp_path,
        config=DiamondDevConfig(
            config_path=tmp_path / ".diamond-dev.toml",
            repository_url="git@github.com:owner/repo.git",
        ),
        plan=PlanContext(path=tmp_path / "compare.md", slug="compare"),
        wiki=WikiContext(
            url="git@github.com:owner/repo.wiki.git",
            directory=tmp_path / "repo.wiki",
            comparison_file=tmp_path / "repo.wiki" / "compare-comparison.md",
            comparison_bundle_file=tmp_path
            / "repo.wiki"
            / "compare-comparison-bundle.md",
            review_file=tmp_path / "repo.wiki" / "compare-review.md",
            review_judgments_file=tmp_path
            / "repo.wiki"
            / "compare-review-judgments.json",
        ),
        implementation=ImplementationContext(
            branches=(
                ImplementationBranch(
                    agent_name="a",
                    repo_dir=tmp_path / "a-compare",
                    branch="diamond-dev/compare/a",
                    log_prefix="a",
                ),
                ImplementationBranch(
                    agent_name="b",
                    repo_dir=tmp_path / "b-compare",
                    branch="diamond-dev/compare/b",
                    log_prefix="b",
                ),
            ),
            base_branch="main",
        ),
        commit_pair=CommitPairContext(slug="compare", entries=entries),
    )


class _SlugRunner:
    def __init__(self, *, output: str = "", returncode: int = 0) -> None:
        self.output = output
        self.returncode = returncode

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        assert isinstance(check, bool)
        return CommandResult(
            command=tuple(command),
            cwd=cwd,
            returncode=self.returncode,
            log_path=cwd / f"{log_name}.log",
            output=self.output,
        )
