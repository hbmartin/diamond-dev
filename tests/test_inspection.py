"""Tests for status, clean, validate, and dry-run commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from diamond_dev.errors import DiamondDevError
from diamond_dev.executor import CommandRunner
from diamond_dev.inspection import (
    resolve_run_slug,
    run_clean,
    run_dry_run,
    run_status,
    run_validate,
)

_REPO_URL = "git@github.com:owner/repo.git"


def _write_config(tmp_path: Path, extra: str = "") -> None:
    (tmp_path / ".diamond-dev.toml").write_text(
        f'repository_url = "{_REPO_URL}"\n{extra}',
        encoding="utf-8",
    )


def _runner(tmp_path: Path) -> CommandRunner:
    return CommandRunner(tmp_path / "logs")


def _git(runner: CommandRunner, cwd: Path, *args: str, log_name: str) -> None:
    runner.run(("git", *args), cwd=cwd, log_name=log_name)


def _make_clone(runner: CommandRunner, clone_dir: Path, *, origin: str) -> None:
    clone_dir.mkdir()
    _git(runner, clone_dir, "init", log_name=f"{clone_dir.name}-init")
    _git(
        runner,
        clone_dir,
        "remote",
        "add",
        "origin",
        origin,
        log_name=f"{clone_dir.name}-remote",
    )


def test_resolve_run_slug_accepts_plans_and_slugs(tmp_path: Path) -> None:
    assert resolve_run_slug(tmp_path, "My Plan.md") == "my-plan"
    assert resolve_run_slug(tmp_path, "My Plan") == "my-plan"
    assert resolve_run_slug(tmp_path, "my-plan") == "my-plan"
    with pytest.raises(DiamondDevError):
        resolve_run_slug(tmp_path, "!!!")


def test_run_status_reports_clones_and_wiki_state(tmp_path: Path) -> None:
    _write_config(tmp_path)
    runner = _runner(tmp_path)
    _make_clone(runner, tmp_path / "codex-my-plan", origin=_REPO_URL)
    wiki_dir = tmp_path / "repo.wiki"
    wiki_dir.mkdir()
    (wiki_dir / "my-plan-comparison.md").write_text(
        "Comparison\n- [x] Accept: codex\n",
        encoding="utf-8",
    )
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "logs" / "run.json").write_text(
        '{"status": "succeeded"}',
        encoding="utf-8",
    )

    report = run_status(cwd=tmp_path, runner=runner, plan_or_slug="My Plan.md")

    assert report.slug == "my-plan"
    codex_status, claude_status = report.branches
    assert codex_status.clone_exists
    assert codex_status.dirty_file_count == 0
    assert not claude_status.clone_exists
    assert report.wiki_exists
    assert report.comparison_exists
    assert report.accepted_agent == "codex"
    assert report.acceptance_error is None
    assert not report.review_exists
    assert report.last_run_status == "succeeded"


def test_run_status_reports_malformed_acceptance(tmp_path: Path) -> None:
    _write_config(tmp_path)
    wiki_dir = tmp_path / "repo.wiki"
    wiki_dir.mkdir()
    (wiki_dir / "my-plan-comparison.md").write_text(
        "Comparison\n- [x] Accept: mystery\n",
        encoding="utf-8",
    )

    report = run_status(
        cwd=tmp_path,
        runner=_runner(tmp_path),
        plan_or_slug="my-plan",
    )

    assert report.accepted_agent is None
    assert report.acceptance_error is not None


def test_run_clean_removes_matching_clones_and_bundle(tmp_path: Path) -> None:
    _write_config(tmp_path)
    runner = _runner(tmp_path)
    codex_dir = tmp_path / "codex-my-plan"
    claude_dir = tmp_path / "claude-my-plan"
    _make_clone(runner, codex_dir, origin=_REPO_URL)
    _make_clone(runner, claude_dir, origin=_REPO_URL)
    bundle = tmp_path / "my-plan-comparison-bundle.md"
    bundle.write_text("bundle\n", encoding="utf-8")
    comparison = tmp_path / "comparison.md"
    comparison.write_text("comparison\n", encoding="utf-8")

    removed = run_clean(cwd=tmp_path, runner=runner, plan_or_slug="my-plan")

    assert set(removed) == {codex_dir, claude_dir, bundle}
    assert not codex_dir.exists()
    assert not claude_dir.exists()
    assert not bundle.exists()
    assert comparison.exists()


def test_run_clean_refuses_foreign_clones_without_force(tmp_path: Path) -> None:
    _write_config(tmp_path)
    runner = _runner(tmp_path)
    codex_dir = tmp_path / "codex-my-plan"
    _make_clone(runner, codex_dir, origin="git@github.com:other/repo.git")

    with pytest.raises(DiamondDevError, match="does not match"):
        run_clean(cwd=tmp_path, runner=runner, plan_or_slug="my-plan")
    assert codex_dir.exists()

    removed = run_clean(
        cwd=tmp_path,
        runner=runner,
        plan_or_slug="my-plan",
        force=True,
    )
    assert codex_dir in removed
    assert not codex_dir.exists()


def test_run_clean_refuses_non_git_directories_without_force(tmp_path: Path) -> None:
    _write_config(tmp_path)
    codex_dir = tmp_path / "codex-my-plan"
    codex_dir.mkdir()
    (codex_dir / "file.txt").write_text("data\n", encoding="utf-8")

    with pytest.raises(DiamondDevError, match="not a Git repository"):
        run_clean(cwd=tmp_path, runner=_runner(tmp_path), plan_or_slug="my-plan")
    assert codex_dir.exists()


def test_run_clean_force_removes_local_comparison(tmp_path: Path) -> None:
    _write_config(tmp_path)
    comparison = tmp_path / "comparison.md"
    comparison.write_text("comparison\n", encoding="utf-8")

    removed = run_clean(
        cwd=tmp_path,
        runner=_runner(tmp_path),
        plan_or_slug="my-plan",
        force=True,
    )

    assert comparison in removed
    assert not comparison.exists()


def test_run_validate_accepts_valid_config(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "[workflow]\n"
        'implementers = ["codex", "claude", "claude-b"]\n'
        "[agents.claude-b]\n"
        'adapter = "claude"\n',
    )

    assert run_validate(tmp_path) == 0


def test_run_dry_run_logs_plan_without_cloning(tmp_path: Path) -> None:
    _write_config(tmp_path)
    plan_path = tmp_path / "My Plan.md"
    plan_path.write_text("# My Plan\n", encoding="utf-8")

    assert run_dry_run(cwd=tmp_path, plan_path=plan_path) == 0
    assert not (tmp_path / "codex-my-plan").exists()
    assert not (tmp_path / "repo.wiki").exists()
