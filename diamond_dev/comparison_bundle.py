"""Deterministic comparison bundle generation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from diamond_dev.executor import CommandExecutor
    from diamond_dev.git_ops import ComparisonGitOperations
    from diamond_dev.workflow import ImplementationBranch, RunContext


@dataclass(slots=True)
class _DiffBudget:
    remaining_bytes: int


@dataclass(frozen=True, slots=True)
class _ChangedFile:
    status: str
    display_path: str
    diff_path: str


def write_comparison_bundle(
    *,
    context: RunContext,
    runner: CommandExecutor,
    git: ComparisonGitOperations,
) -> RunContext:
    """Write the deterministic comparison bundle and return updated context."""
    diff_budget = _DiffBudget(
        remaining_bytes=context.config.comparison.max_total_diff_bytes,
    )
    active_context = context
    lines = [
        "# Diamond Dev comparison bundle",
        "",
        *_run_identity_lines(context),
        f"- Base branch: {context.implementation.base_branch}",
        f"- Diff byte budget: {context.config.comparison.max_total_diff_bytes}",
        f"- Per-file diff byte cap: {context.config.comparison.max_file_diff_bytes}",
        "",
    ]
    for branch in context.implementation.branches:
        branch_lines, tests_ran = _branch_section(
            context=context,
            runner=runner,
            git=git,
            branch=branch,
            diff_budget=diff_budget,
        )
        lines.extend(branch_lines)
        if tests_ran:
            active_context = git.record_dirty_files(
                active_context,
                f"{branch.agent_name} comparison tests",
                branch.repo_dir,
                branch.branch,
                log_prefix=f"{branch.log_prefix}-comparison-tests",
            )

    bundle_markdown = "\n".join(lines).rstrip()
    context.comparison_bundle_file.write_text(f"{bundle_markdown}\n", encoding="utf-8")
    logger.info("Wrote comparison bundle: {}", context.comparison_bundle_file)
    return active_context


def _run_identity_lines(context: RunContext) -> list[str]:
    if context.commit_pair is None:
        return [f"- Plan: {context.plan.file_name}"]

    left, right = context.commit_pair.entries
    return [
        "- Mode: commit-pair",
        f"- Slug: {context.commit_pair.slug}",
        f"- Left arg: {left.original_arg}",
        f"- Left label: {left.label}",
        f"- Left SHA: {left.sha}",
        f"- Left message: {_commit_subject(left.message)}",
        f"- Right arg: {right.original_arg}",
        f"- Right label: {right.label}",
        f"- Right SHA: {right.sha}",
        f"- Right message: {_commit_subject(right.message)}",
    ]


def _commit_subject(message: str) -> str:
    return message.splitlines()[0] if message else ""


def _branch_section(
    *,
    context: RunContext,
    runner: CommandExecutor,
    git: ComparisonGitOperations,
    branch: ImplementationBranch,
    diff_budget: _DiffBudget,
) -> tuple[list[str], bool]:
    head_revision = git.revision(
        branch.repo_dir,
        branch.branch,
        log_name=f"{branch.log_prefix}-comparison-head-revision",
    )
    ahead_behind = git.branch_ahead_behind(
        branch.repo_dir,
        branch=branch.branch,
        base_branch=context.implementation.base_branch,
        log_name=f"{branch.log_prefix}-comparison-ahead-behind",
    )
    changed_files = _changed_files(
        git.run(
            branch.repo_dir,
            "diff",
            "--name-status",
            f"origin/{context.implementation.base_branch}...{branch.branch}",
            log_name=f"{branch.log_prefix}-comparison-name-status",
        ).output,
    )
    lines = [
        f"## {branch.agent_name}",
        "",
        f"- Branch: {branch.branch}",
        f"- Repository: {branch.repo_dir}",
        f"- Head SHA: {head_revision}",
        (
            "- Ahead/behind base: "
            f"ahead={ahead_behind.ahead}, behind={ahead_behind.behind}"
        ),
        f"- Changed files: {len(changed_files)}",
        *_change_stat_lines(changed_files),
        "",
        "### Changed file list",
        "",
        *_changed_file_list_lines(
            changed_files,
            context.config.comparison.max_file_diff_bytes,
        ),
        "",
        "### Tests",
        "",
    ]
    test_lines, tests_ran = _test_lines(
        context=context,
        runner=runner,
        branch=branch,
    )
    lines.extend(test_lines)
    lines.extend(("", "### Capped diffs", ""))
    lines.extend(
        _diff_lines(
            context=context,
            git=git,
            branch=branch,
            changed_files=changed_files,
            diff_budget=diff_budget,
        ),
    )
    lines.append("")
    return lines, tests_ran


def _changed_files(name_status_output: str) -> tuple[_ChangedFile, ...]:
    files: list[_ChangedFile] = []
    for line in name_status_output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if len(parts) == 1:
            display_path = parts[0]
            diff_path = parts[0]
        elif status.startswith(("R", "C")) and len(parts) >= 3:
            display_path = f"{parts[-2]} -> {parts[-1]}"
            diff_path = parts[-1]
        else:
            display_path = parts[-1]
            diff_path = parts[-1]
        files.append(
            _ChangedFile(
                status=status,
                display_path=display_path,
                diff_path=diff_path,
            ),
        )
    return tuple(files)


def _change_stat_lines(changed_files: Sequence[_ChangedFile]) -> list[str]:
    stats = {
        "added": 0,
        "modified": 0,
        "deleted": 0,
        "renamed": 0,
        "copied": 0,
        "other": 0,
    }
    for changed_file in changed_files:
        match changed_file.status[:1]:
            case "A":
                stats["added"] += 1
            case "M":
                stats["modified"] += 1
            case "D":
                stats["deleted"] += 1
            case "R":
                stats["renamed"] += 1
            case "C":
                stats["copied"] += 1
            case _:
                stats["other"] += 1
    summary = ", ".join(f"{key}={value}" for key, value in stats.items() if value)
    return [f"- Change stats: {summary or 'none'}"]


def _changed_file_list_lines(
    changed_files: Sequence[_ChangedFile],
    byte_budget: int,
) -> list[str]:
    if not changed_files:
        return ["- No changed files."]

    included, omitted = _capped_lines(
        (
            f"- {changed_file.status}: {changed_file.display_path}"
            for changed_file in changed_files
        ),
        max_bytes=byte_budget,
    )
    if not included:
        included = ["- All changed files omitted due to byte budget."]
    if omitted:
        included.extend(("", "Omitted changed files:", *omitted))
    return included


def _test_lines(
    *,
    context: RunContext,
    runner: CommandExecutor,
    branch: ImplementationBranch,
) -> tuple[list[str], bool]:
    if not context.config.comparison.test_commands:
        return ["- tests: not_run"], False

    lines: list[str] = []
    for index, command_text in enumerate(
        context.config.comparison.test_commands,
        start=1,
    ):
        log_name = f"{branch.log_prefix}-comparison-test-{index}"
        result = runner.run(
            ("sh", "-lc", command_text),
            cwd=branch.repo_dir,
            log_name=log_name,
            check=False,
        )
        status = "passed" if result.returncode == 0 else "failed"
        clipped_output, omitted_bytes = _clip_bytes(
            result.output,
            context.config.comparison.max_test_output_bytes,
        )
        lines.extend(
            (
                f"- Command {index}: `{command_text}`",
                f"  - Status: {status} (exit {result.returncode})",
                f"  - Log: {result.log_path}",
            ),
        )
        if clipped_output:
            lines.extend(("  - Output:", "    ```text"))
            lines.extend(f"    {line}" for line in clipped_output.splitlines())
            lines.append("    ```")
        if omitted_bytes:
            lines.append(f"  - Omitted output bytes: {omitted_bytes}")
    return lines, True


def _diff_lines(
    *,
    context: RunContext,
    git: ComparisonGitOperations,
    branch: ImplementationBranch,
    changed_files: Sequence[_ChangedFile],
    diff_budget: _DiffBudget,
) -> list[str]:
    lines: list[str] = []
    omitted: list[str] = []
    for index, changed_file in enumerate(changed_files, start=1):
        if diff_budget.remaining_bytes <= 0:
            omitted.append(
                f"- {changed_file.display_path}: total diff budget exhausted",
            )
            continue
        result = git.run(
            branch.repo_dir,
            "diff",
            "--no-color",
            f"origin/{context.implementation.base_branch}...{branch.branch}",
            "--",
            changed_file.diff_path,
            log_name=f"{branch.log_prefix}-comparison-diff-{index}",
        )
        diff_text, file_omitted = _clip_bytes(
            result.output,
            context.config.comparison.max_file_diff_bytes,
        )
        diff_text, total_omitted = _clip_bytes(diff_text, diff_budget.remaining_bytes)
        diff_budget.remaining_bytes -= len(diff_text.encode("utf-8"))
        if not diff_text.strip():
            continue
        lines.extend(
            (
                f"#### {changed_file.display_path}",
                "",
                "```diff",
                diff_text.rstrip(),
                "```",
                "",
            ),
        )
        if file_omitted:
            omitted.append(
                f"- {changed_file.display_path}: omitted {file_omitted} bytes "
                "by per-file cap",
            )
        if total_omitted:
            omitted.append(
                f"- {changed_file.display_path}: omitted {total_omitted} bytes "
                "by total diff cap",
            )
    if not lines:
        lines.append("- No diff content included.")
    if omitted:
        lines.extend(("### Omitted diff files", "", *omitted))
    return lines


def _capped_lines(
    lines: Iterable[str],
    *,
    max_bytes: int,
) -> tuple[list[str], list[str]]:
    included: list[str] = []
    omitted: list[str] = []
    used_bytes = 0
    for line in lines:
        line_bytes = len(f"{line}\n".encode("utf-8"))  # noqa: UP012
        if used_bytes + line_bytes > max_bytes:
            omitted.append(line)
            continue
        included.append(line)
        used_bytes += line_bytes
    return included, omitted


def _clip_bytes(text: str, max_bytes: int) -> tuple[str, int]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, 0
    clipped = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return clipped, len(encoded) - max_bytes
