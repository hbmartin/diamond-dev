"""Tests for agent adapter registry behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from diamond_dev.agents import resolve_adapter
from diamond_dev.errors import DiamondDevError


def test_codex_adapter_builds_prompt_command_with_model(tmp_path: Path) -> None:
    adapter = resolve_adapter("codex")

    command = adapter.prompt_command(
        tmp_path,
        "do work",
        model="gpt-5",
        capability="implementation",
    )

    assert adapter.executable == "codex"
    assert command == (
        "codex",
        "exec",
        "-C",
        str(tmp_path),
        "-m",
        "gpt-5",
        "--dangerously-bypass-approvals-and-sandbox",
        "do work",
    )


def test_claude_adapter_supports_review_fix_and_final_review(
    tmp_path: Path,
) -> None:
    adapter = resolve_adapter("claude")

    prompt_command = adapter.prompt_command(
        tmp_path,
        "fix review",
        model="opus",
        capability="review_fixer",
    )
    review_command = adapter.interactive_review_command("123", model="sonnet")

    assert prompt_command[:3] == ("claude", "--model", "opus")
    assert review_command == (
        "claude",
        "--model",
        "sonnet",
        "--permission-mode",
        "bypassPermissions",
        "--dangerously-skip-permissions",
        "/review 123",
    )


def test_coderabbit_adapter_builds_review_provider_command() -> None:
    adapter = resolve_adapter("coderabbit")

    assert adapter.review_command("main", model=None) == (
        "coderabbit",
        "review",
        "--plain",
        "--base",
        "main",
    )


def test_adapter_rejects_unsupported_capability(tmp_path: Path) -> None:
    adapter = resolve_adapter("gemini")

    with pytest.raises(DiamondDevError, match="does not support"):
        adapter.prompt_command(
            tmp_path,
            "fix review",
            model=None,
            capability="review_fixer",
        )


def test_resolve_adapter_rejects_unknown_name() -> None:
    with pytest.raises(DiamondDevError, match="Unknown agent adapter"):
        resolve_adapter("unknown")
