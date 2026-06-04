"""Tests for external command construction."""

from __future__ import annotations

from pathlib import Path

from diamond_dev.commands import (
    ComparisonBranchContext,
    ComparisonPromptContext,
    build_claude_interactive_review_command,
    build_claude_print_command,
    build_coderabbit_review_command,
    build_codex_command,
    build_gemini_command,
    build_gh_pr_create_command,
    build_gh_pr_edit_body_command,
    build_gh_pr_list_command,
    build_pnpm_install_command,
    build_uv_sync_command,
    gemini_comparison_prompt,
    initial_implementation_prompt,
    review_fix_prompt,
    review_judgment_prompt,
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


def test_build_codex_command_accepts_model(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    command = build_codex_command(repo_dir, "do work", model="gpt-5")

    assert command == (
        "codex",
        "exec",
        "-C",
        str(repo_dir),
        "-m",
        "gpt-5",
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


def test_build_claude_print_command_accepts_model() -> None:
    command = build_claude_print_command("do work", model="sonnet")

    assert command == (
        "claude",
        "--model",
        "sonnet",
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


def test_build_gemini_command_accepts_model() -> None:
    assert build_gemini_command("compare", model="gemini-3") == (
        "gemini",
        "-m",
        "gemini-3",
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


def test_build_gh_pr_edit_body_command_is_deterministic() -> None:
    assert build_gh_pr_edit_body_command(
        pr_url="https://github.com/owner/repo/pull/123",
        body="body",
    ) == (
        "gh",
        "pr",
        "edit",
        "https://github.com/owner/repo/pull/123",
        "--body",
        "body",
    )


def test_build_gh_pr_list_command_checks_all_states() -> None:
    assert build_gh_pr_list_command("codex/my-plan") == (
        "gh",
        "pr",
        "list",
        "--head",
        "codex/my-plan",
        "--state",
        "all",
        "--json",
        "number,state,url",
        "--limit",
        "1",
    )


def test_build_claude_interactive_review_command() -> None:
    assert build_claude_interactive_review_command("123") == (
        "claude",
        "--permission-mode",
        "bypassPermissions",
        "--dangerously-skip-permissions",
        "/review 123",
    )


def test_build_claude_interactive_review_command_accepts_model() -> None:
    assert build_claude_interactive_review_command("123", model="opus") == (
        "claude",
        "--model",
        "opus",
        "--permission-mode",
        "bypassPermissions",
        "--dangerously-skip-permissions",
        "/review 123",
    )


def test_initial_prompt_tells_agent_not_to_push() -> None:
    prompt = initial_implementation_prompt("plan.md")

    assert "plan.md" in prompt
    assert "Do not push" in prompt


def test_custom_initial_prompt_keeps_required_context() -> None:
    prompt = initial_implementation_prompt("plan.md", "Custom implementation rules.")

    assert "Custom implementation rules." in prompt
    assert "Plan file: `plan.md`" in prompt
    assert "Do not push" in prompt


def test_review_judgment_prompt_requires_structured_sidecar() -> None:
    prompt = review_judgment_prompt(
        "my-plan-review.md",
        "my-plan-review-judgments.json",
        "coderabbit",
        "codex",
        "Custom review judgment rules.",
    )

    assert "Custom review judgment rules." in prompt
    assert "my-plan-review.md" in prompt
    assert "my-plan-review-judgments.json" in prompt
    assert "`schema_version`" in prompt
    assert "`review_provider`: `coderabbit`" in prompt
    assert "`review_judge`: `codex`" in prompt
    assert "`fix`, `decline`, and `needs_input`" in prompt
    assert "(A) should fix" in prompt
    assert "Commit the updated review file and structured judgment sidecar" in prompt


def test_review_fix_prompt_prefers_sidecar_and_keeps_legacy_fallback() -> None:
    prompt = review_fix_prompt(
        "my-plan-review.md",
        "my-plan-review-judgments.json",
        "Custom review fix rules.",
    )

    assert "Custom review fix rules." in prompt
    assert "my-plan-review.md" in prompt
    assert "my-plan-review-judgments.json" in prompt
    assert "decision `fix`" in prompt
    assert "sidecar is missing or invalid" in prompt
    assert "(A) should fix" in prompt
    assert "decision `decline` or `needs_input`" in prompt


def test_gemini_prompt_includes_custom_prompt_and_context(tmp_path: Path) -> None:
    codex_dir = tmp_path / "codex-my-plan"
    claude_dir = tmp_path / "claude-my-plan"
    prompt = gemini_comparison_prompt(
        "Custom compare rules.",
        ComparisonPromptContext(
            base_branch="main",
            comparison_bundle_file_name="my-plan-comparison-bundle.md",
            branches=(
                ComparisonBranchContext(
                    agent_name="codex",
                    branch="codex/my-plan",
                    repo_dir=codex_dir,
                ),
                ComparisonBranchContext(
                    agent_name="claude",
                    branch="claude/my-plan",
                    repo_dir=claude_dir,
                ),
            ),
        ),
    )

    assert "Custom compare rules." in prompt
    assert "codex/my-plan" in prompt
    assert "claude/my-plan" in prompt
    assert "my-plan-comparison-bundle.md" in prompt
    assert "comparison.md" in prompt


def test_gemini_prompt_uses_fallback_for_whitespace_prompt(
    tmp_path: Path,
) -> None:
    prompt = gemini_comparison_prompt(
        "   ",
        ComparisonPromptContext(
            base_branch="main",
            comparison_bundle_file_name="my-plan-comparison-bundle.md",
            branches=(
                ComparisonBranchContext(
                    agent_name="codex",
                    branch="codex/my-plan",
                    repo_dir=tmp_path / "codex-my-plan",
                ),
                ComparisonBranchContext(
                    agent_name="claude",
                    branch="claude/my-plan",
                    repo_dir=tmp_path / "claude-my-plan",
                ),
            ),
        ),
    )

    assert "Compare the implementation branches" in prompt
