"""End-to-end orchestrator tests with fake external CLIs."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from diamond_dev.executor import CommandRunner
from diamond_dev.orchestrator import DiamondDevOrchestrator

if TYPE_CHECKING:
    import pytest


def test_orchestrator_e2e_happy_path_with_fake_clis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_path, calls_file = _prepare_e2e_workspace(tmp_path, monkeypatch)
    runner = CommandRunner(tmp_path / "logs")
    orchestrator = DiamondDevOrchestrator(
        cwd=tmp_path,
        runner=runner,
        sleep=lambda _seconds: None,
    )

    assert orchestrator.run(plan_path) == 0

    summary = json.loads((tmp_path / "logs" / "run.json").read_text())
    assert summary["status"] == "succeeded"
    assert summary["selected_implementation"]["accepted_agent"] == "codex"
    assert summary["context"]["artifacts"]["pr_url"] == (
        "https://github.com/owner/repo/pull/123"
    )
    assert (tmp_path / "codex-my-plan" / "codex-review-fix.txt").is_file()
    assert (tmp_path / "codex-my-plan" / "claude-comparison-fix.txt").is_file()
    assert "gemini" in calls_file.read_text(encoding="utf-8").splitlines()


def test_orchestrator_e2e_reuses_accepted_wiki_comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_path, calls_file = _prepare_e2e_workspace(
        tmp_path,
        monkeypatch,
        comparison_markdown="Stored comparison\n- [x] Accept: codex\n",
    )
    runner = CommandRunner(tmp_path / "logs")
    orchestrator = DiamondDevOrchestrator(
        cwd=tmp_path,
        runner=runner,
        sleep=lambda _seconds: None,
    )

    assert orchestrator.run(plan_path) == 0

    calls = calls_file.read_text(encoding="utf-8").splitlines()
    assert "gemini" not in calls
    assert (tmp_path / "comparison.md").read_text(encoding="utf-8") == (
        "Stored comparison\n- [x] Accept: codex\n"
    )


def _prepare_e2e_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    comparison_markdown: str | None = None,
) -> tuple[Path, Path]:
    setup_runner = CommandRunner(tmp_path / "setup-logs")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    calls_file = tmp_path / "fake-cli-calls.log"
    _write_fake_clis(fake_bin)
    monkeypatch.setenv("FAKE_CLI_CALLS", str(calls_file))
    # The orchestrator clones fresh worktrees that do not inherit local git config.
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "fake@example.test")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Fake User")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "fake@example.test")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Fake User")
    monkeypatch.setenv(
        "PATH",
        f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
    )

    origin = _create_origin_repository(tmp_path, setup_runner)
    wiki_origin = _create_wiki_repository(
        tmp_path,
        setup_runner,
        comparison_markdown=comparison_markdown,
    )
    plan_path = tmp_path / "My Plan.md"
    plan_path.write_text("# My Plan\n", encoding="utf-8")
    (tmp_path / ".diamond-dev.toml").write_text(
        f'repository_url = "{origin.as_uri()}"\n'
        f'wiki_repository_url = "{wiki_origin.as_uri()}"\n',
        encoding="utf-8",
    )
    return plan_path, calls_file


def _create_origin_repository(tmp_path: Path, runner: CommandRunner) -> Path:
    origin = tmp_path / "origin.git"
    worktree = tmp_path / "origin-worktree"
    runner.run(("git", "init", "--bare", str(origin)), cwd=tmp_path, log_name="origin-init")
    runner.run(("git", "init", str(worktree)), cwd=tmp_path, log_name="worktree-init")
    _configure_git_user(runner, worktree)
    (worktree / "app.txt").write_text("base\n", encoding="utf-8")
    runner.run(("git", "add", "app.txt"), cwd=worktree, log_name="worktree-add")
    runner.run(
        ("git", "commit", "-m", "Initial commit"),
        cwd=worktree,
        log_name="worktree-commit",
    )
    runner.run(("git", "branch", "-M", "main"), cwd=worktree, log_name="worktree-main")
    runner.run(
        ("git", "remote", "add", "origin", str(origin)),
        cwd=worktree,
        log_name="worktree-remote",
    )
    runner.run(("git", "push", "-u", "origin", "main"), cwd=worktree, log_name="push-main")
    runner.run(
        ("git", "symbolic-ref", "HEAD", "refs/heads/main"),
        cwd=origin,
        log_name="origin-head",
    )
    return origin


def _create_wiki_repository(
    tmp_path: Path,
    runner: CommandRunner,
    *,
    comparison_markdown: str | None,
) -> Path:
    wiki_origin = tmp_path / "repo.wiki.git"
    wiki_worktree = tmp_path / "wiki-worktree"
    runner.run(
        ("git", "init", "--bare", str(wiki_origin)),
        cwd=tmp_path,
        log_name="wiki-origin-init",
    )
    runner.run(
        ("git", "init", str(wiki_worktree)),
        cwd=tmp_path,
        log_name="wiki-worktree-init",
    )
    _configure_git_user(runner, wiki_worktree)
    (wiki_worktree / "Home.md").write_text("Home\n", encoding="utf-8")
    if comparison_markdown is not None:
        (wiki_worktree / "my-plan-comparison.md").write_text(
            comparison_markdown,
            encoding="utf-8",
        )
    runner.run(("git", "add", "."), cwd=wiki_worktree, log_name="wiki-add")
    runner.run(
        ("git", "commit", "-m", "Initial wiki"),
        cwd=wiki_worktree,
        log_name="wiki-commit",
    )
    runner.run(
        ("git", "branch", "-M", "main"),
        cwd=wiki_worktree,
        log_name="wiki-main",
    )
    runner.run(
        ("git", "remote", "add", "origin", str(wiki_origin)),
        cwd=wiki_worktree,
        log_name="wiki-remote",
    )
    runner.run(
        ("git", "push", "-u", "origin", "main"),
        cwd=wiki_worktree,
        log_name="wiki-push",
    )
    runner.run(
        ("git", "symbolic-ref", "HEAD", "refs/heads/main"),
        cwd=wiki_origin,
        log_name="wiki-origin-head",
    )
    return wiki_origin


def _configure_git_user(runner: CommandRunner, repo_dir: Path) -> None:
    runner.run(
        ("git", "config", "user.email", "fake@example.test"),
        cwd=repo_dir,
        log_name=f"{repo_dir.name}-git-email",
    )
    runner.run(
        ("git", "config", "user.name", "Fake User"),
        cwd=repo_dir,
        log_name=f"{repo_dir.name}-git-name",
    )


def _write_fake_clis(fake_bin: Path) -> None:
    _write_executable(
        fake_bin / "gh",
        """#!/bin/sh
_fake_record gh
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "list" ]; then
  printf '[]\\n'
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "create" ]; then
  printf 'https://github.com/owner/repo/pull/123\\n'
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "edit" ]; then
  exit 0
fi
exit 1
""",
    )
    _write_executable(
        fake_bin / "gemini",
        """#!/bin/sh
case "$*" in
  *"Diamond Dev doctor authentication check"*)
    _fake_record gemini-auth
    printf 'OK\\n'
    exit 0
    ;;
esac
_fake_record gemini
cat > comparison.md <<'EOF'
Fake comparison
- [x] Accept: codex
EOF
""",
    )
    _write_executable(
        fake_bin / "coderabbit",
        """#!/bin/sh
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  _fake_record coderabbit-auth
  printf '{"authenticated":true}\\n'
  exit 0
fi
_fake_record coderabbit
printf 'Review\\n(A) Fix accepted item.\\n'
""",
    )
    _write_executable(
        fake_bin / "codex",
        """#!/bin/sh
if [ "$1" = "login" ] && [ "$2" = "status" ]; then
  _fake_record codex-auth
  printf 'Logged in\\n'
  exit 0
fi
_fake_record codex
repo=
previous=
for arg in "$@"; do
  if [ "$previous" = "-C" ]; then
    repo="$arg"
  fi
  previous="$arg"
done
if [ -z "$repo" ]; then
  repo="$PWD"
fi
cd "$repo" || exit 1
_fake_git_user
case "$*" in
  *"Implement accepted CodeRabbit review fixes"*)
    printf 'fixed review\\n' > codex-review-fix.txt
    git add codex-review-fix.txt
    git commit -m "fake codex review fix"
    ;;
  *"Evaluate each CodeRabbit review item"*)
    review_file=$(ls *-review.md | head -n 1)
    judgments_file=${review_file%-review.md}-review-judgments.json
    cat > "$judgments_file" <<'EOF'
{
  "findings": [
    {
      "confidence": 0.9,
      "decision": "fix",
      "id": "CR-1",
      "rationale": "Valid fake issue."
    }
  ],
  "review_file": "my-plan-review.md",
  "review_judge": "codex",
  "review_provider": "coderabbit",
  "schema_version": 1
}
EOF
    printf '\\n(A) Fake judgment.\\n' >> "$review_file"
    git add "$review_file" "$judgments_file"
    git commit -m "fake codex review judgment"
    ;;
  *)
    printf 'codex initial\\n' > codex.txt
    git add codex.txt
    git commit -m "fake codex initial"
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "claude",
        """#!/bin/sh
if [ "$1" = "auth" ] && [ "$2" = "status" ]; then
  _fake_record claude-auth
  printf 'Authenticated\\n'
  exit 0
fi
_fake_record claude
case "$*" in
  *"/review "*)
    exit 0
    ;;
esac
_fake_git_user
case "$*" in
  *"comparison follow-up"*)
    printf 'claude comparison fix\\n' > claude-comparison-fix.txt
    git add claude-comparison-fix.txt
    git commit -m "fake claude comparison fix"
    ;;
  *)
    printf 'claude initial\\n' > claude.txt
    git add claude.txt
    git commit -m "fake claude initial"
    ;;
esac
""",
    )
    _write_fake_support(fake_bin)


def _write_fake_support(fake_bin: Path) -> None:
    support_script = """#!/bin/sh
_fake_record() {
  if [ -n "$FAKE_CLI_CALLS" ]; then
    printf '%s\\n' "$1" >> "$FAKE_CLI_CALLS"
  fi
}
_fake_git_user() {
  git config user.email fake@example.test
  git config user.name "Fake User"
}
"""
    _write_executable(fake_bin / "_fake_record", support_script)
    for script_path in ("gh", "gemini", "coderabbit", "codex", "claude"):
        path = fake_bin / script_path
        original = path.read_text(encoding="utf-8")
        path.write_text(
            original.replace(
                "#!/bin/sh\n",
                f"#!/bin/sh\n. {shlex.quote(str(fake_bin / '_fake_record'))}\n",
            ),
            encoding="utf-8",
        )


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
