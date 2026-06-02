"""Tests for external command construction."""

from __future__ import annotations

from pathlib import Path

from diamond_dev.commands import (
    ComparisonPromptContext,
    build_claude_interactive_review_command,
    build_claude_print_command,
    build_coderabbit_review_command,
    build_codex_command,
    build_gemini_command,
    build_gh_pr_create_command,
    build_pnpm_install_command,
    build_uv_sync_command,
    gemini_comparison_prompt,
    initial_implementation_prompt,
)


def test_build_codex_command_uses_exec_and_bypass_flag(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    command = build_codex_command(repo_dir, "do work")

    assert command == (
        "codex",
        "exec",
        "-C",
        str(repo_dir),
        "--dangerously-bypass-approvals-and-sandbox",
        "do work",
    )


def test_build_claude_print_command_uses_bypass_permissions() -> None:
    command = build_claude_print_command("do work")

    assert command == (
        "claude",
        "-p",
        "--permission-mode",
        "bypassPermissions",
        "--dangerously-skip-permissions",
        "do work",
    )


def test_build_gemini_command_is_headless_and_trusted() -> None:
    assert build_gemini_command("compare") == (
        "gemini",
        "-p",
        "compare",
        "--skip-trust",
        "-y",
    )


def test_build_coderabbit_review_command_uses_plain_base() -> None:
    assert build_coderabbit_review_command("main") == (
        "coderabbit",
        "review",
        "--plain",
        "--base",
        "main",
    )


def test_build_uv_sync_command_is_locked() -> None:
    assert build_uv_sync_command() == ("uv", "sync", "--locked")


def test_build_pnpm_install_command_is_frozen() -> None:
    assert build_pnpm_install_command() == (
        "pnpm",
        "install",
        "--frozen-lockfile",
    )


def test_build_gh_pr_create_command_is_deterministic() -> None:
    command = build_gh_pr_create_command(
        base_branch="main",
        head_branch="codex/my-plan",
        title="Implement My Plan",
        body="body",
    )

    assert command == (
        "gh",
        "pr",
        "create",
        "--base",
        "main",
        "--head",
        "codex/my-plan",
        "--title",
        "Implement My Plan",
        "--body",
        "body",
    )


def test_build_claude_interactive_review_command() -> None:
    assert build_claude_interactive_review_command("123") == (
        "claude",
        "--permission-mode",
        "bypassPermissions",
        "--dangerously-skip-permissions",
        "/review 123",
    )


def test_initial_prompt_tells_agent_not_to_push() -> None:
    prompt = initial_implementation_prompt("plan.md")

    assert "plan.md" in prompt
    assert "Do not push" in prompt


def test_gemini_prompt_includes_custom_prompt_and_context(tmp_path: Path) -> None:
    codex_dir = tmp_path / "codex-my-plan"
    claude_dir = tmp_path / "claude-my-plan"
    prompt = gemini_comparison_prompt(
        "Custom compare rules.",
        ComparisonPromptContext(
            base_branch="main",
            codex_branch="codex/my-plan",
            claude_branch="claude/my-plan",
            codex_dir=codex_dir,
            claude_dir=claude_dir,
        ),
    )

    assert "Custom compare rules." in prompt
    assert "codex/my-plan" in prompt
    assert "claude/my-plan" in prompt
    assert "comparison.md" in prompt


def test_gemini_prompt_uses_fallback_for_whitespace_prompt(
    tmp_path: Path,
) -> None:
    prompt = gemini_comparison_prompt(
        "   ",
        ComparisonPromptContext(
            base_branch="main",
            codex_branch="codex/my-plan",
            claude_branch="claude/my-plan",
            codex_dir=tmp_path / "codex-my-plan",
            claude_dir=tmp_path / "claude-my-plan",
        ),
    )

    assert "Compare the Codex and Claude implementation branches" in prompt
