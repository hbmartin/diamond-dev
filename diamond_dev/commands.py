"""External command and prompt construction."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_DO_NOT_PUSH_INSTRUCTION: Final = (
    "- Do not push; diamond-dev will push committed work."
)


@dataclass(frozen=True, slots=True)
class ComparisonBranchContext:
    """One implementation branch supplied to a comparison judge."""

    agent_name: str
    branch: str
    repo_dir: Path


@dataclass(frozen=True, slots=True)
class ComparisonPromptContext:
    """Context supplied to a comparison judge."""

    base_branch: str
    comparison_bundle_file_name: str
    branches: Sequence[ComparisonBranchContext]


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
            _DO_NOT_PUSH_INSTRUCTION,
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
            _DO_NOT_PUSH_INSTRUCTION,
        ),
    )


def review_judgment_prompt(
    review_file_name: str,
    review_judgments_file_name: str,
    review_provider: str,
    review_judge: str,
    configured_prompt: str | None = None,
) -> str:
    """Return the prompt asking Codex to classify CodeRabbit findings."""
    return _prompt_with_required_context(
        configured_prompt,
        fallback_prompt="Evaluate each CodeRabbit review item.",
        required_lines=(
            f"- Review file: `{review_file_name}`",
            f"- Structured judgment sidecar: `{review_judgments_file_name}`",
            "- Write valid JSON to the structured judgment sidecar with "
            "`schema_version`, `review_file`, `review_provider`, "
            "`review_judge`, and `findings`.",
            f"- Use `review_provider`: `{review_provider}`.",
            f"- Use `review_judge`: `{review_judge}`.",
            "- Each finding must have `id`, `decision`, `confidence`, and "
            "`rationale`.",
            "- Allowed decisions are `fix`, `decline`, and `needs_input`.",
            "- Judge each item as (A) should fix, (B) decline fix, or "
            "(C) requirements ambiguous or input needed.",
            "- Append your judgements to the review file without removing "
            "existing content.",
            "- Commit the updated review file and structured judgment sidecar.",
            _DO_NOT_PUSH_INSTRUCTION,
        ),
    )


def review_fix_prompt(
    review_file_name: str,
    review_judgments_file_name: str,
    configured_prompt: str | None = None,
) -> str:
    """Return the prompt asking Codex to implement accepted review fixes."""
    return _prompt_with_required_context(
        configured_prompt,
        fallback_prompt="Implement accepted CodeRabbit review fixes.",
        required_lines=(
            f"- Review file: `{review_file_name}`",
            f"- Structured judgment sidecar: `{review_judgments_file_name}`",
            "- If the structured judgment sidecar exists and is valid, implement "
            "every finding with decision `fix`.",
            "- If the sidecar is missing or invalid, fall back to legacy markdown "
            "judgments and implement every review item judged as (A) should fix.",
            "- Do not implement items judged as (B) decline fix.",
            "- Do not implement findings with decision `decline` or `needs_input`.",
            "- Leave items judged as (C) requirements ambiguous or input needed "
            "unchanged.",
            "- Inspect the current branch first because this prompt may be rerun.",
            "- Avoid duplicating work that is already applied.",
            "- Commit your changes.",
            _DO_NOT_PUSH_INSTRUCTION,
        ),
    )


def gemini_comparison_prompt(
    configured_prompt: str | None,
    context: ComparisonPromptContext,
) -> str:
    """Return the comparison judgment prompt with mandatory run context."""
    return _prompt_with_required_context(
        configured_prompt,
        fallback_prompt=_fallback_prompt(),
        required_lines=_comparison_required_lines(context),
    )


def _fallback_prompt() -> str:
    return (
        "Compare the implementation branches against the base branch. Evaluate "
        "correctness, completeness, maintainability, tests, and risk. Recommend "
        "one implementation as the base and describe any follow-up changes the "
        "comparison fixer should apply."
    )


def _comparison_required_lines(
    context: ComparisonPromptContext,
) -> tuple[str, ...]:
    branch_lines = tuple(
        f"- {branch.agent_name} branch: `{branch.branch}` in `{branch.repo_dir}`"
        for branch in context.branches
    )
    return (
        f"- Base branch: `{context.base_branch}`",
        f"- Comparison bundle: `{context.comparison_bundle_file_name}`",
        "- Read the comparison bundle before judging branch quality.",
        *branch_lines,
        "- Write the final comparison to `comparison.md` in the current directory.",
        "- Do not modify any implementation repository.",
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
