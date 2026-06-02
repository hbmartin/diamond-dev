"""External command and prompt construction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ComparisonPromptContext:
    """Context supplied to Gemini for branch comparison."""

    base_branch: str
    codex_branch: str
    claude_branch: str
    codex_dir: Path
    claude_dir: Path


def build_codex_command(repo_dir: Path, prompt: str) -> tuple[str, ...]:
    """Build a non-interactive Codex command with full edit permissions."""
    return (
        "codex",
        "exec",
        "-C",
        str(repo_dir),
        "--dangerously-bypass-approvals-and-sandbox",
        prompt,
    )


def build_claude_print_command(prompt: str) -> tuple[str, ...]:
    """Build a non-interactive Claude command with bypass permissions."""
    return (
        "claude",
        "-p",
        "--permission-mode",
        "bypassPermissions",
        "--dangerously-skip-permissions",
        prompt,
    )


def build_gemini_command(prompt: str) -> tuple[str, ...]:
    """Build a headless Gemini command."""
    return ("gemini", "-p", prompt, "--skip-trust", "-y")


def build_coderabbit_review_command(base_branch: str) -> tuple[str, ...]:
    """Build a plain CodeRabbit review command."""
    return ("coderabbit", "review", "--plain", "--base", base_branch)


def build_gh_pr_create_command(
    *,
    base_branch: str,
    head_branch: str,
    title: str,
    body: str,
) -> tuple[str, ...]:
    """Build a deterministic GitHub PR creation command."""
    return (
        "gh",
        "pr",
        "create",
        "--base",
        base_branch,
        "--head",
        head_branch,
        "--title",
        title,
        "--body",
        body,
    )


def build_claude_interactive_review_command(pr_number: str) -> tuple[str, ...]:
    """Build the final interactive Claude PR review command."""
    return (
        "claude",
        "--permission-mode",
        "bypassPermissions",
        "--dangerously-skip-permissions",
        f"/review {pr_number}",
    )


def initial_implementation_prompt(plan_file_name: str) -> str:
    """Return the initial implementation prompt for Codex or Claude."""
    return (
        f"Read and implement `{plan_file_name}`. Commit your changes on the "
        "current branch. Do not push; diamond-dev will push committed work."
    )


def comparison_implementation_prompt(comparison_file_name: str) -> str:
    """Return the prompt for the opposite agent comparison implementation."""
    return (
        f"Read `{comparison_file_name}` and implement the requested comparison "
        "follow-up changes on the current branch. Commit your changes. Do not push; "
        "diamond-dev will push committed work."
    )


def review_judgment_prompt(review_file_name: str) -> str:
    """Return the prompt asking Codex to classify CodeRabbit findings."""
    return (
        f"Read and evaluate `{review_file_name}`. For each review item, judge it "
        "to be (A) should fix, (B) decline fix, or (C) requirements ambiguous or "
        "input needed. Then append your judgements to the CodeRabbit review output "
        "file but leave the existing file content as is. Commit the updated review "
        "file. Do not push; diamond-dev will push committed work."
    )


def review_fix_prompt(review_file_name: str) -> str:
    """Return the prompt asking Codex to implement accepted review fixes."""
    return (
        f"Read `{review_file_name}`. Implement every review item judged as "
        "(A) should fix. Do not implement items judged as (B) decline fix. Leave "
        "items judged as (C) requirements ambiguous or input needed unchanged. "
        "Commit your changes. Do not push; diamond-dev will push committed work."
    )


def gemini_comparison_prompt(
    configured_prompt: str | None,
    context: ComparisonPromptContext,
) -> str:
    """Return the Gemini comparison prompt with mandatory run context."""
    custom_prompt = (
        configured_prompt.strip() if configured_prompt else _fallback_prompt()
    )
    return (
        f"{custom_prompt}\n\n"
        "Required context:\n"
        f"- Base branch: `{context.base_branch}`\n"
        f"- Codex branch: `{context.codex_branch}` in `{context.codex_dir}`\n"
        f"- Claude branch: `{context.claude_branch}` in `{context.claude_dir}`\n"
        "- Write the final comparison to `comparison.md` in the current directory.\n"
        "- Do not modify either implementation repository."
    )


def _fallback_prompt() -> str:
    return (
        "Compare the Codex and Claude implementation branches against the base "
        "branch. Evaluate correctness, completeness, maintainability, tests, and "
        "risk. Recommend either Codex or Claude as the base implementation and "
        "describe any follow-up changes the opposite agent should apply."
    )
