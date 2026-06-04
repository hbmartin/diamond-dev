"""Tests for deterministic comparison bundle generation."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from diamond_dev.comparison_bundle import write_comparison_bundle
from diamond_dev.config import ComparisonConfig, DiamondDevConfig
from diamond_dev.executor import CommandResult
from diamond_dev.git_ops import BranchAheadBehind
from diamond_dev.workflow import (
    DirtyRecord,
    ImplementationBranch,
    ImplementationContext,
    PlanContext,
    RunContext,
    WikiContext,
)


class _FakeRunner:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.calls: list[tuple[tuple[str, ...], Path, str, bool]] = []
        self.results: dict[str, tuple[int, str]] = {}

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        command_tuple = tuple(command)
        self.calls.append((command_tuple, cwd, log_name, check))
        returncode, output = self.results.get(log_name, (0, ""))
        return CommandResult(
            command=command_tuple,
            cwd=cwd,
            returncode=returncode,
            log_path=self.tmp_path / f"{log_name}.log",
            output=output,
        )


class _FakeGit:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.name_status: dict[str, str] = {}
        self.diffs: dict[tuple[str, str], str] = {}
        self.dirty_labels: list[str] = []

    def revision(self, repo_dir: Path, ref: str, *, log_name: str) -> str:
        del log_name
        return f"{repo_dir.name}-{ref.replace('/', '-')}-sha"

    def branch_ahead_behind(
        self,
        repo_dir: Path,
        *,
        branch: str,
        base_branch: str,
        log_name: str,
    ) -> BranchAheadBehind:
        del branch, log_name
        return BranchAheadBehind(ahead=len(repo_dir.name), behind=len(base_branch))

    def run(
        self,
        repo_dir: Path,
        *args: str,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        del check
        output = ""
        if args[:2] == ("diff", "--name-status"):
            output = self.name_status[repo_dir.name]
        elif args[:2] == ("diff", "--no-color"):
            output = self.diffs[(repo_dir.name, args[-1])]
        return CommandResult(
            command=("git", *args),
            cwd=repo_dir,
            returncode=0,
            log_path=self.tmp_path / f"{log_name}.log",
            output=output,
        )

    def record_dirty_files(
        self,
        context: RunContext,
        label: str,
        repo_dir: Path,
        branch: str,
        *,
        log_prefix: str | None = None,
    ) -> RunContext:
        del repo_dir, log_prefix
        self.dirty_labels.append(label)
        return context.with_dirty_record(
            DirtyRecord(label=label, branch=branch, files=("dirty.txt",)),
        )


def test_comparison_bundle_records_two_branches_without_tests(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    runner = _FakeRunner(tmp_path)
    git = _FakeGit(tmp_path)
    git.name_status = {
        "codex-my-plan": "M\tsrc/app.py\nA\tREADME.md\n",
        "claude-my-plan": "D\told.txt\nR100\told.py\tnew.py\n",
    }
    git.diffs = {
        ("codex-my-plan", "src/app.py"): "diff --git a/src/app.py b/src/app.py\n",
        ("codex-my-plan", "README.md"): "diff --git a/README.md b/README.md\n",
        ("claude-my-plan", "old.txt"): "diff --git a/old.txt b/old.txt\n",
        ("claude-my-plan", "new.py"): "diff --git a/old.py b/new.py\n",
    }

    updated_context = write_comparison_bundle(
        context=context,
        runner=runner,  # type: ignore[arg-type]
        git=git,  # type: ignore[arg-type]
    )

    bundle = context.comparison_bundle_file.read_text(encoding="utf-8")
    assert updated_context.dirty_records == ()
    assert "# Diamond Dev comparison bundle" in bundle
    assert "- Base branch: main" in bundle
    assert "## codex" in bundle
    assert "## claude" in bundle
    assert "- Changed files: 2" in bundle
    assert "- Change stats: added=1, modified=1" in bundle
    assert "- Change stats: deleted=1, renamed=1" in bundle
    assert "- tests: not_run" in bundle
    assert "old.py -> new.py" in bundle


def test_comparison_bundle_records_configured_test_results_and_dirty_files(
    tmp_path: Path,
) -> None:
    context = _context(
        tmp_path,
        comparison=ComparisonConfig(
            test_commands=("uv run pytest tests/unit", "uv run ruff check"),
            max_test_output_bytes=12,
        ),
    )
    runner = _FakeRunner(tmp_path)
    runner.results = {
        "codex-comparison-test-1": (0, "passed\n"),
        "codex-comparison-test-2": (1, "failure output that is clipped\n"),
        "claude-comparison-test-1": (0, "passed\n"),
        "claude-comparison-test-2": (1, "failure output that is clipped\n"),
    }
    git = _FakeGit(tmp_path)
    git.name_status = {
        "codex-my-plan": "M\tsrc/app.py\n",
        "claude-my-plan": "M\tsrc/app.py\n",
    }
    git.diffs = {
        ("codex-my-plan", "src/app.py"): "diff --git a/src/app.py b/src/app.py\n",
        ("claude-my-plan", "src/app.py"): "diff --git a/src/app.py b/src/app.py\n",
    }

    updated_context = write_comparison_bundle(
        context=context,
        runner=runner,  # type: ignore[arg-type]
        git=git,  # type: ignore[arg-type]
    )

    bundle = context.comparison_bundle_file.read_text(encoding="utf-8")
    assert [call[0] for call in runner.calls] == [
        ("sh", "-lc", "uv run pytest tests/unit"),
        ("sh", "-lc", "uv run ruff check"),
        ("sh", "-lc", "uv run pytest tests/unit"),
        ("sh", "-lc", "uv run ruff check"),
    ]
    assert all(not call[3] for call in runner.calls)
    assert "Status: passed (exit 0)" in bundle
    assert "Status: failed (exit 1)" in bundle
    assert "Omitted output bytes:" in bundle
    assert git.dirty_labels == [
        "codex comparison tests",
        "claude comparison tests",
    ]
    assert tuple(record.label for record in updated_context.dirty_records) == (
        "codex comparison tests",
        "claude comparison tests",
    )


def test_comparison_bundle_reports_capped_lists_and_diffs(tmp_path: Path) -> None:
    context = _context(
        tmp_path,
        comparison=ComparisonConfig(
            max_total_diff_bytes=20,
            max_file_diff_bytes=30,
        ),
    )
    runner = _FakeRunner(tmp_path)
    git = _FakeGit(tmp_path)
    git.name_status = {
        "codex-my-plan": (
            "M\tsrc/first-file-with-a-long-name.py\n"
            "M\tsrc/second-file-with-a-long-name.py\n"
        ),
        "claude-my-plan": "M\tsrc/third-file.py\n",
    }
    git.diffs = {
        (
            "codex-my-plan",
            "src/first-file-with-a-long-name.py",
        ): "diff --git a/src/first-file-with-a-long-name.py b/src/first-file.py\n"
        "line 1\nline 2\n",
        (
            "codex-my-plan",
            "src/second-file-with-a-long-name.py",
        ): "diff --git a/src/second-file-with-a-long-name.py b/src/second-file.py\n",
        ("claude-my-plan", "src/third-file.py"): "diff --git a/src/third-file.py\n",
    }

    write_comparison_bundle(
        context=context,
        runner=runner,  # type: ignore[arg-type]
        git=git,  # type: ignore[arg-type]
    )

    bundle = context.comparison_bundle_file.read_text(encoding="utf-8")
    assert "Omitted changed files:" in bundle
    assert "total diff budget exhausted" in bundle
    assert "by per-file cap" in bundle
    assert "by total diff cap" in bundle


def test_comparison_bundle_reports_all_changed_files_omitted_by_budget(
    tmp_path: Path,
) -> None:
    context = _context(
        tmp_path,
        comparison=ComparisonConfig(max_file_diff_bytes=1),
    )
    runner = _FakeRunner(tmp_path)
    git = _FakeGit(tmp_path)
    git.name_status = {
        "codex-my-plan": "M\tsrc/app.py\n",
        "claude-my-plan": "A\tREADME.md\n",
    }
    git.diffs = {
        ("codex-my-plan", "src/app.py"): "diff --git a/src/app.py b/src/app.py\n",
        ("claude-my-plan", "README.md"): "diff --git a/README.md b/README.md\n",
    }

    write_comparison_bundle(
        context=context,
        runner=runner,  # type: ignore[arg-type]
        git=git,  # type: ignore[arg-type]
    )

    bundle = context.comparison_bundle_file.read_text(encoding="utf-8")
    assert "- No changed files." not in bundle
    assert "- All changed files omitted due to byte budget." in bundle
    assert "Omitted changed files:" in bundle


def test_comparison_bundle_uses_utf8_bytes_for_changed_file_budget(
    tmp_path: Path,
) -> None:
    context = _context(
        tmp_path,
        comparison=ComparisonConfig(max_file_diff_bytes=13),
    )
    runner = _FakeRunner(tmp_path)
    git = _FakeGit(tmp_path)
    git.name_status = {
        "codex-my-plan": "M\tcaf\u00e9.py\n",
        "claude-my-plan": "",
    }
    git.diffs = {
        ("codex-my-plan", "caf\u00e9.py"): "diff --git a/caf\u00e9.py b/caf\u00e9.py\n",
    }

    write_comparison_bundle(
        context=context,
        runner=runner,  # type: ignore[arg-type]
        git=git,  # type: ignore[arg-type]
    )

    bundle = context.comparison_bundle_file.read_text(encoding="utf-8")
    assert "- All changed files omitted due to byte budget." in bundle
    assert "- M: caf\u00e9.py" in bundle


def _context(
    tmp_path: Path,
    *,
    comparison: ComparisonConfig | None = None,
) -> RunContext:
    comparison_config = comparison if comparison is not None else ComparisonConfig()
    return RunContext(
        cwd=tmp_path,
        config=DiamondDevConfig(
            config_path=tmp_path / ".diamond-dev.toml",
            repository_url="git@github.com:owner/repo.git",
            comparison=comparison_config,
        ),
        plan=PlanContext(
            path=tmp_path / "My Plan.md",
            slug="my-plan",
        ),
        wiki=WikiContext(
            url="git@github.com:owner/repo.wiki.git",
            directory=tmp_path / "repo.wiki",
            comparison_file=tmp_path / "repo.wiki" / "my-plan-comparison.md",
            comparison_bundle_file=tmp_path
            / "repo.wiki"
            / "my-plan-comparison-bundle.md",
            review_file=tmp_path / "repo.wiki" / "my-plan-review.md",
            review_judgments_file=tmp_path
            / "repo.wiki"
            / "my-plan-review-judgments.json",
        ),
        implementation=ImplementationContext(
            branches=(
                ImplementationBranch(
                    agent_name="codex",
                    repo_dir=tmp_path / "codex-my-plan",
                    branch="codex/my-plan",
                    log_prefix="codex",
                ),
                ImplementationBranch(
                    agent_name="claude",
                    repo_dir=tmp_path / "claude-my-plan",
                    branch="claude/my-plan",
                    log_prefix="claude",
                ),
            ),
            base_branch="main",
        ),
    )
