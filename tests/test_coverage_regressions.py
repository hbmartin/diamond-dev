"""Focused tests for defensive branches that should stay covered."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from diamond_dev import commit_pair as commit_pair_module
from diamond_dev import logging_setup
from diamond_dev import main as main_module
from diamond_dev import pr as pr_module
from diamond_dev.agents import AgentAdapter
from diamond_dev.commit_pair import (
    ResolvedCommitInput,
    commit_pair_entries_avoiding_base_branch,
    ensure_commit_pair_marker,
    infer_commit_labels,
    upsert_commit_pair_index,
)
from diamond_dev.errors import CommandFailureError, DiamondDevError, UrlDerivationError
from diamond_dev.executor import CommandResult
from diamond_dev.git_ops import ComparisonGitOperations, GitHubGitOperations
from diamond_dev.naming import (
    derive_wiki_repository_url,
    is_git_remote_url,
    repository_name_from_url,
)
from diamond_dev.orchestrator_acceptance import AcceptancePollingMixin
from diamond_dev.workflow import CommitPairContext, CommitPairEntry


def test_prompt_adapter_requires_prompt_builder(tmp_path: Path) -> None:
    adapter = AgentAdapter(
        name="noop",
        executable="noop",
        capabilities=frozenset({"implementation"}),
    )

    with pytest.raises(DiamondDevError, match="cannot build prompt commands"):
        adapter.prompt_command(
            tmp_path,
            "do work",
            model=None,
            capability="implementation",
        )


def test_review_adapter_requires_review_builder() -> None:
    adapter = AgentAdapter(
        name="noop",
        executable="noop",
        capabilities=frozenset({"review_provider"}),
    )

    with pytest.raises(DiamondDevError, match="cannot build review commands"):
        adapter.review_command("main", model=None)


def test_final_review_adapter_requires_interactive_review_builder() -> None:
    adapter = AgentAdapter(
        name="noop",
        executable="noop",
        capabilities=frozenset({"final_reviewer"}),
    )

    with pytest.raises(DiamondDevError, match="cannot build interactive review commands"):
        adapter.interactive_review_command("123", model=None)


def test_github_git_operations_protocol_defaults_raise(tmp_path: Path) -> None:
    operations = _DefaultGitHubGitOperations()

    with pytest.raises(NotImplementedError):
        operations.sync_wiki(tmp_path)
    with pytest.raises(NotImplementedError):
        operations.run(tmp_path, "status", log_name="status")
    with pytest.raises(NotImplementedError):
        operations.remote_url_branch_exists(
            tmp_path,
            remote_url="git@github.com:owner/repo.git",
            branch="main",
            log_name="remote-branch",
        )


def test_comparison_git_operations_protocol_defaults_raise(tmp_path: Path) -> None:
    operations = _DefaultComparisonGitOperations()

    with pytest.raises(NotImplementedError):
        operations.run(tmp_path, "status", log_name="status")
    with pytest.raises(NotImplementedError):
        operations.revision(tmp_path, "HEAD", log_name="revision")
    with pytest.raises(NotImplementedError):
        operations.branch_ahead_behind(
            tmp_path,
            branch="feature",
            base_branch="main",
            log_name="ahead-behind",
        )
    with pytest.raises(NotImplementedError):
        operations.record_dirty_files(
            SimpleNamespace(),
            "codex",
            tmp_path,
            "feature",
        )


@pytest.mark.parametrize(
    "argv",
    [
        ("init", "plan.md"),
        ("doctor", "--force"),
    ],
)
def test_parse_args_rejects_extra_arguments(argv: tuple[str, ...]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main_module.parse_args(argv)

    assert exit_info.value.code == 2


def test_source_tree_version_returns_unknown_when_pyproject_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "PROJECT_ROOT", tmp_path)

    assert main_module._source_tree_version() == "unknown"  # noqa: SLF001


@pytest.mark.parametrize(
    ("pyproject_toml",),
    [
        ('project = "diamond-dev"\n',),
        ("[project]\nversion = 1\n",),
    ],
)
def test_source_tree_version_returns_unknown_for_invalid_project_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pyproject_toml: str,
) -> None:
    (tmp_path / "pyproject.toml").write_text(pyproject_toml, encoding="utf-8")
    monkeypatch.setattr(main_module, "PROJECT_ROOT", tmp_path)

    assert main_module._source_tree_version() == "unknown"  # noqa: SLF001


def test_parse_pr_url_rejects_output_without_pull_url() -> None:
    with pytest.raises(DiamondDevError, match="Could not parse PR URL"):
        pr_module.parse_pr_url("created without a URL")


def test_parse_pr_number_rejects_pull_url_without_numeric_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pr_module,
        "parse_pr_url",
        lambda _output: "https://github.com/owner/repo/pull/not-a-number",
    )

    with pytest.raises(DiamondDevError, match="Could not parse PR number"):
        pr_module.parse_pr_number("ignored")


def test_trace_context_patcher_defaults_when_trace_context_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TraceModule:
        @staticmethod
        def get_current_span() -> object:
            return object()

    monkeypatch.setattr(
        logging_setup,
        "import_module",
        lambda module_name: TraceModule if module_name == "opentelemetry.trace" else None,
    )
    patcher = logging_setup._trace_context_patcher()  # noqa: SLF001
    record: dict[str, dict[str, object]] = {"extra": {}}

    patcher(record)

    assert record["extra"]["otelTraceID"] == "0"
    assert record["extra"]["otelSpanID"] == "0"
    assert record["extra"]["otelTraceSampled"] is False
    assert record["extra"]["otelServiceName"] == ""


def test_acceptance_polling_accepts_after_wait(tmp_path: Path) -> None:
    context = _acceptance_context(tmp_path)
    context.wiki.comparison_file.write_text("No accepted implementation yet\n")
    polling = _AcceptancePollingHarness(context.wiki.comparison_file)

    assert polling._poll_acceptance(context) == "codex"  # noqa: SLF001
    assert polling.sleep_calls == [1]
    assert polling.workflow_provider.synced_paths == [
        context.wiki.directory,
        context.wiki.directory,
    ]


def test_repository_name_rejects_empty_url() -> None:
    with pytest.raises(UrlDerivationError, match="Could not derive repository name"):
        repository_name_from_url("")


def test_is_git_remote_url_rejects_invalid_urlparse_value() -> None:
    assert not is_git_remote_url("https://[::1")


def test_is_git_remote_url_rejects_invalid_port() -> None:
    assert not is_git_remote_url("https://github.com:bad/owner/repo.git")


def test_derive_wiki_repository_url_rejects_empty_owner_or_repo() -> None:
    with pytest.raises(UrlDerivationError, match="Could not derive a GitHub wiki URL"):
        derive_wiki_repository_url("https://github.com/owner/.git")


def test_infer_commit_labels_infers_left_from_right_unique_match() -> None:
    left = _resolved_commit(message="Implementation")
    right = _resolved_commit(message="Claude implementation")

    assert infer_commit_labels(left, right) == ("codex", "claude")


def test_commit_pair_entries_avoid_empty_base_branch() -> None:
    context = _commit_pair_context(
        (
            _commit_pair_entry(label="a", branch="main"),
            _commit_pair_entry(label="b", branch="feature"),
        ),
    )

    assert commit_pair_entries_avoiding_base_branch(context, "") == context.entries


def test_commit_pair_entries_regenerate_branch_matching_base() -> None:
    context = _commit_pair_context(
        (
            _commit_pair_entry(label="a", branch="main"),
            _commit_pair_entry(label="b", branch="feature"),
        ),
    )

    adjusted = commit_pair_entries_avoiding_base_branch(context, "main")

    assert adjusted[0].branch == "diamond-dev/compare/a"
    assert adjusted[1].branch == "feature"


def test_commit_pair_entries_reject_duplicate_regenerated_base_branches() -> None:
    context = _commit_pair_context(
        (
            _commit_pair_entry(label="same", branch="main"),
            _commit_pair_entry(label="same", branch="main"),
        ),
    )

    with pytest.raises(DiamondDevError, match="Generated commit-pair branches"):
        commit_pair_entries_avoiding_base_branch(context, "main")


def test_ensure_commit_pair_marker_skips_non_commit_pair_context() -> None:
    context = SimpleNamespace(commit_pair=None)

    assert ensure_commit_pair_marker("comparison\n", context) == "comparison\n"


def test_ensure_commit_pair_marker_prepends_missing_marker() -> None:
    context = SimpleNamespace(
        commit_pair=_commit_pair_context(
            (
                _commit_pair_entry(label="a", sha="a" * 40),
                _commit_pair_entry(label="b", sha="b" * 40),
            ),
        ),
    )

    marked = ensure_commit_pair_marker("  comparison\n", context)

    assert marked.startswith("<!-- diamond-dev commit-pair:")
    assert marked.endswith("comparison\n")


def test_upsert_commit_pair_index_rerenders_valid_records_and_detects_existing_record(
    tmp_path: Path,
) -> None:
    context = _commit_pair_context(
        (
            _commit_pair_entry(label="a", sha="a" * 40),
            _commit_pair_entry(label="b", sha="b" * 40),
        ),
    )
    index_path = tmp_path / "diamond-dev-commit-comparisons.md"
    existing_line = f"- `{'c' * 40}` vs `{'d' * 40}` -> `legacy-slug` (old labels)"
    index_path.write_text(
        f"# Existing comparisons\n\n{existing_line}\n\nignored raw note\n",
        encoding="utf-8",
    )

    assert upsert_commit_pair_index(tmp_path, context)
    assert index_path.read_text(encoding="utf-8") == (
        "# Diamond Dev commit comparisons\n\n"
        f"- `{'c' * 40}` vs `{'d' * 40}` -> `legacy-slug`\n"
        f"{context.index_line}\n"
    )
    assert not upsert_commit_pair_index(tmp_path, context)


def test_commit_pair_ref_helpers_return_empty_on_command_failure(
    tmp_path: Path,
) -> None:
    runner = _StaticRunner(returncode=1)

    assert (
        commit_pair_module._containing_ref_names(  # noqa: SLF001
            runner,
            tmp_path,
            "a" * 40,
            log_name="contains",
        )
        == ()
    )
    assert (
        commit_pair_module._local_ref_names(  # noqa: SLF001
            cwd=tmp_path,
            runner=runner,
            sha="a" * 40,
            log_prefix="local",
        )
        == ()
    )


def test_commit_pair_branch_ref_exists_returns_false_for_missing_refs(
    tmp_path: Path,
) -> None:
    assert not commit_pair_module._branch_ref_exists(  # noqa: SLF001
        runner=_StaticRunner(returncode=1),
        repo_dir=tmp_path,
        branch="missing",
        log_name="branch",
    )


def test_commit_pair_normalize_branch_names_skips_origin_head() -> None:
    assert commit_pair_module._normalize_branch_names(  # noqa: SLF001
        (
            "remotes/origin/HEAD -> origin/main",
            " remotes/origin/feature ",
            "* main",
        ),
    ) == ("feature", "main")


def test_commit_pair_normalizes_repository_url_edges() -> None:
    normalize_repository_url = (
        commit_pair_module._normalized_repository_url  # noqa: SLF001
    )
    assert normalize_repository_url("") == ""
    assert normalize_repository_url("https:///owner/repo.git") == "https:///owner/repo"
    assert (
        normalize_repository_url("ssh://github.com:2222/owner/repo.git")
        == "github.com:2222/owner/repo"
    )


def test_commit_pair_cwd_origin_mismatch_when_git_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(commit_pair_module.shutil, "which", lambda _command: None)

    assert not commit_pair_module._cwd_origin_matches(  # noqa: SLF001
        cwd=tmp_path,
        repository_url="git@github.com:owner/repo.git",
        runner=_StaticRunner(),
    )


def test_codex_generated_commit_pair_slug_handles_command_failure(
    tmp_path: Path,
) -> None:
    slug = commit_pair_module._codex_generated_slug(  # noqa: SLF001
        cwd=tmp_path,
        runner=_FailingRunner(tmp_path),
        left=_resolved_commit(short_sha="a" * 12, message="Left"),
        right=_resolved_commit(short_sha="b" * 12, message="Right"),
    )

    assert slug is None


def test_codex_generated_commit_pair_slug_uses_first_slug_line(
    tmp_path: Path,
) -> None:
    slug = commit_pair_module._codex_generated_slug(  # noqa: SLF001
        cwd=tmp_path,
        runner=_StaticRunner(output="\nUseful comparison slug!\n"),
        left=_resolved_commit(short_sha="a" * 12, message="Left"),
        right=_resolved_commit(short_sha="b" * 12, message="Right"),
    )

    assert slug == "useful-comparison-slug"


def test_slug_used_for_different_pair_returns_false_for_empty_wiki(
    tmp_path: Path,
) -> None:
    assert not commit_pair_module._slug_used_for_different_pair(  # noqa: SLF001
        wiki_dir=tmp_path,
        slug="compare",
        left_sha="a" * 40,
        right_sha="b" * 40,
    )


class _DefaultGitHubGitOperations(GitHubGitOperations):
    pass


class _DefaultComparisonGitOperations(ComparisonGitOperations):
    pass


class _AcceptancePollingHarness(AcceptancePollingMixin):
    def __init__(self, comparison_file: Path) -> None:
        self.comparison_file = comparison_file
        self.sleep_calls: list[float] = []
        self.workflow_provider = _RecordingWorkflowProvider()

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.comparison_file.write_text("- [x] Accept: codex\n", encoding="utf-8")


class _RecordingWorkflowProvider:
    def __init__(self) -> None:
        self.synced_paths: list[Path] = []

    def sync_wiki(self, wiki_dir: Path) -> None:
        self.synced_paths.append(wiki_dir)


def _acceptance_context(tmp_path: Path) -> SimpleNamespace:
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    return SimpleNamespace(
        wiki=SimpleNamespace(
            directory=wiki_dir,
            comparison_file=wiki_dir / "comparison.md",
        ),
        config=SimpleNamespace(
            acceptance=SimpleNamespace(
                poll_interval_seconds=1,
                max_wait_seconds=1,
            ),
        ),
        implementation=SimpleNamespace(
            implementer_names=("codex", "claude"),
        ),
    )


def _resolved_commit(
    *,
    original_arg: str = "abc123",
    sha: str = "a" * 40,
    short_sha: str = "aaaaaaaaaaaa",
    message: str = "Implementation",
    ref_names: tuple[str, ...] = (),
    explicit_branch: str | None = None,
    source: str = "remote",
) -> ResolvedCommitInput:
    return ResolvedCommitInput(
        original_arg=original_arg,
        sha=sha,
        short_sha=short_sha,
        message=message,
        ref_names=ref_names,
        explicit_branch=explicit_branch,
        source=source,
    )


def _commit_pair_entry(
    *,
    label: str,
    branch: str | None = None,
    sha: str = "a" * 40,
) -> CommitPairEntry:
    return CommitPairEntry(
        label=label,
        original_arg=sha,
        sha=sha,
        short_sha=sha[:12],
        message=f"{label} implementation",
        ref_names=(),
        branch=branch or f"diamond-dev/compare/{label}",
    )


def _commit_pair_context(
    entries: tuple[CommitPairEntry, CommitPairEntry],
) -> CommitPairContext:
    return CommitPairContext(slug="compare", entries=entries)


class _StaticRunner:
    def __init__(self, *, returncode: int = 0, output: str = "") -> None:
        self.returncode = returncode
        self.output = output

    def run(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        assert isinstance(check, bool)
        return CommandResult(
            command=command,
            cwd=cwd,
            returncode=self.returncode,
            log_path=cwd / f"{log_name}.log",
            output=self.output,
        )


class _FailingRunner:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd

    def run(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        del command, cwd, log_name, check
        raise CommandFailureError(
            command="codex",
            cwd=str(self.cwd),
            returncode=1,
            log_path=str(self.cwd / "codex.log"),
        )
