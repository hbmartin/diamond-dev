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


def build_codex_command(
    repo_dir: Path,
    prompt: str,
    *,
    model: str | None = None,
) -> tuple[str, ...]:
    """Build a non-interactive Codex command with full edit permissions."""
    command = [
        "codex",
        "exec",
        "-C",
        str(repo_dir),
    ]
    if model is not None:
        command.extend(("-m", model))
    command.extend(("--dangerously-bypass-approvals-and-sandbox", prompt))
    return tuple(command)


def build_claude_print_command(
    prompt: str,
    *,
    model: str | None = None,
) -> tuple[str, ...]:
    """Build a non-interactive Claude command with bypass permissions."""
    command = ["claude"]
    if model is not None:
        command.extend(("--model", model))
    command.extend((
        "-p",
        "--permission-mode",
        "bypassPermissions",
        "--dangerously-skip-permissions",
        prompt,
    ))
    return tuple(command)


def build_gemini_command(prompt: str, *, model: str | None = None) -> tuple[str, ...]:
    """Build a headless Gemini command."""
    command = ["gemini"]
    if model is not None:
        command.extend(("-m", model))
    command.extend(("-p", prompt, "--skip-trust", "-y"))
    return tuple(command)


def build_coderabbit_review_command(base_branch: str) -> tuple[str, ...]:
    """Build a plain CodeRabbit review command."""
    return ("coderabbit", "review", "--plain", "--base", base_branch)


def build_uv_sync_command() -> tuple[str, ...]:
    """Build a locked uv package install command."""
    return ("uv", "sync", "--locked")


def build_pnpm_install_command() -> tuple[str, ...]:
    """Build a frozen pnpm package install command."""
    return ("pnpm", "install", "--frozen-lockfile")


def build_gh_pr_list_command(head_branch: str) -> tuple[str, ...]:
    """Build a deterministic GitHub PR lookup command for a workflow branch."""
    return (
        "gh",
        "pr",
        "list",
        "--head",
        head_branch,
        "--state",
        "all",
        "--json",
        "number,state,url",
        "--limit",
        "1",
    )


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


def build_gh_pr_edit_body_command(*, pr_url: str, body: str) -> tuple[str, ...]:
    """Build a deterministic GitHub PR body update command."""
    return ("gh", "pr", "edit", pr_url, "--body", body)


def build_claude_interactive_review_command(
    pr_number: str,
    *,
    model: str | None = None,
) -> tuple[str, ...]:
    """Build the final interactive Claude PR review command."""
    command = ["claude"]
    if model is not None:
        command.extend(("--model", model))
    command.extend((
        "--permission-mode",
        "bypassPermissions",
        "--dangerously-skip-permissions",
        f"/review {pr_number}",
    ))
    return tuple(command)


def initial_implementation_prompt(
    plan_file_name: str,
    configured_prompt: str | None = None,
) -> str:
    """Return the initial implementation prompt for Codex or Claude."""
    return _prompt_with_required_context(
        configured_prompt,
        fallback_prompt="Read and implement the supplied plan.",
        required_lines=(
            f"- Plan file: `{plan_file_name}`",
            "- Commit your changes on the current branch.",
            "- Do not push; diamond-dev will push committed work.",
        ),
    )


def comparison_implementation_prompt(
    comparison_file_name: str,
    configured_prompt: str | None = None,
) -> str:
    """Return the prompt for the opposite agent comparison implementation."""
    return _prompt_with_required_context(
        configured_prompt,
        fallback_prompt="Implement the requested comparison follow-up changes.",
        required_lines=(
            f"- Comparison file: `{comparison_file_name}`",
            "- Inspect the current branch first because this prompt may be rerun.",
            "- Avoid duplicating work that is already applied.",
            "- Commit your changes.",
            "- Do not push; diamond-dev will push committed work.",
        ),
    )


def review_judgment_prompt(
    review_file_name: str,
    configured_prompt: str | None = None,
) -> str:
    """Return the prompt asking Codex to classify CodeRabbit findings."""
    return _prompt_with_required_context(
        configured_prompt,
        fallback_prompt="Evaluate each CodeRabbit review item.",
        required_lines=(
            f"- Review file: `{review_file_name}`",
            "- Judge each item as (A) should fix, (B) decline fix, or "
            "(C) requirements ambiguous or input needed.",
            "- Append your judgements to the review file without removing "
            "existing content.",
            "- Commit the updated review file.",
            "- Do not push; diamond-dev will push committed work.",
        ),
    )


def review_fix_prompt(
    review_file_name: str,
    configured_prompt: str | None = None,
) -> str:
    """Return the prompt asking Codex to implement accepted review fixes."""
    return _prompt_with_required_context(
        configured_prompt,
        fallback_prompt="Implement accepted CodeRabbit review fixes.",
        required_lines=(
            f"- Review file: `{review_file_name}`",
            "- Implement every review item judged as (A) should fix.",
            "- Do not implement items judged as (B) decline fix.",
            "- Leave items judged as (C) requirements ambiguous or input needed "
            "unchanged.",
            "- Inspect the current branch first because this prompt may be rerun.",
            "- Avoid duplicating work that is already applied.",
            "- Commit your changes.",
            "- Do not push; diamond-dev will push committed work.",
        ),
    )


def gemini_comparison_prompt(
    configured_prompt: str | None,
    context: ComparisonPromptContext,
) -> str:
    """Return the Gemini comparison prompt with mandatory run context."""
    return _prompt_with_required_context(
        configured_prompt,
        fallback_prompt=_fallback_prompt(),
        required_lines=(
            f"- Base branch: `{context.base_branch}`",
            f"- Codex branch: `{context.codex_branch}` in `{context.codex_dir}`",
            f"- Claude branch: `{context.claude_branch}` in `{context.claude_dir}`",
            "- Write the final comparison to `comparison.md` in the current "
            "directory.",
            "- Do not modify either implementation repository.",
        ),
    )


def _fallback_prompt() -> str:
    return (
        "Compare the Codex and Claude implementation branches against the base "
        "branch. Evaluate correctness, completeness, maintainability, tests, and "
        "risk. Recommend either Codex or Claude as the base implementation and "
        "describe any follow-up changes the opposite agent should apply."
    )


def _prompt_with_required_context(
    configured_prompt: str | None,
    *,
    fallback_prompt: str,
    required_lines: tuple[str, ...],
) -> str:
    stripped_prompt = configured_prompt.strip() if configured_prompt else ""
    prompt = stripped_prompt or fallback_prompt
    required_context = "\n".join(required_lines)
    return f"{prompt}\n\nRequired context:\n{required_context}"
