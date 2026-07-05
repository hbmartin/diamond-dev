"""Tests for agent adapter registry behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from diamond_dev import agents
from diamond_dev.agents import AgentAdapter, resolve_adapter
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


def test_new_code_writer_adapters_build_prompt_commands(tmp_path: Path) -> None:
    expected_commands = {
        "opencode": ("opencode", "run", "--model", "m1", "do work"),
        "cursor-agent": ("cursor-agent", "--model", "m1", "-p", "--force", "do work"),
        "copilot": ("copilot", "--model", "m1", "-p", "do work", "--allow-all-tools"),
        "qwen": ("qwen", "-m", "m1", "-p", "do work", "-y"),
        "aider": ("aider", "--model", "m1", "--yes-always", "--message", "do work"),
    }
    for adapter_name, expected_command in expected_commands.items():
        adapter = agents.resolve_adapter(adapter_name)
        command = adapter.prompt_command(
            tmp_path,
            "do work",
            model="m1",
            capability="implementation",
        )
        assert command == expected_command
        assert adapter.has_capability("review_fixer")
        assert not adapter.has_capability("comparison_judge")


def test_auth_command_is_optional_for_new_adapters() -> None:
    assert agents.resolve_adapter("opencode").auth_command(model=None) == (
        "opencode",
        "auth",
        "list",
    )
    assert agents.resolve_adapter("cursor-agent").auth_command(model=None) == (
        "cursor-agent",
        "status",
    )
    for adapter_name in ("copilot", "qwen", "aider"):
        assert agents.resolve_adapter(adapter_name).auth_command(model=None) is None


class _FakeEntryPoint:
    def __init__(self, name: str, loaded: object) -> None:
        self.name = name
        self._loaded = loaded

    def load(self) -> object:
        return self._loaded


def _plugin_adapter(name: str, capabilities: frozenset | None = None) -> AgentAdapter:
    return AgentAdapter(
        name=name,
        executable=name,
        capabilities=(
            frozenset({"implementation"}) if capabilities is None else capabilities
        ),
        build_prompt_command=lambda _repo_dir, prompt, _model: (name, prompt),
    )


def _install_fake_entry_points(monkeypatch, entry_points: list) -> None:
    def fake_entry_points(*, group: str) -> list:
        assert group == agents.PLUGIN_ENTRY_POINT_GROUP
        return entry_points

    monkeypatch.setattr(agents.metadata, "entry_points", fake_entry_points)
    agents.clear_plugin_adapter_cache()


def test_plugin_adapter_is_resolvable(monkeypatch, tmp_path: Path) -> None:
    _install_fake_entry_points(
        monkeypatch,
        [_FakeEntryPoint("my-plugin", _plugin_adapter("my-agent"))],
    )
    try:
        assert "my-agent" in agents.adapter_names()
        adapter = agents.resolve_adapter("my-agent")
        command = adapter.prompt_command(
            tmp_path,
            "go",
            model=None,
            capability="implementation",
        )
        assert command == ("my-agent", "go")
    finally:
        agents.clear_plugin_adapter_cache()


def test_plugin_loader_accepts_callables_and_iterables(monkeypatch) -> None:
    _install_fake_entry_points(
        monkeypatch,
        [
            _FakeEntryPoint("factory", lambda: _plugin_adapter("factory-agent")),
            _FakeEntryPoint(
                "many",
                (_plugin_adapter("agent-a"), _plugin_adapter("agent-b")),
            ),
        ],
    )
    try:
        names = agents.adapter_names()
        assert {"factory-agent", "agent-a", "agent-b"} <= names
    finally:
        agents.clear_plugin_adapter_cache()


def test_plugin_loader_skips_invalid_entries(monkeypatch) -> None:
    _install_fake_entry_points(
        monkeypatch,
        [
            _FakeEntryPoint("override", _plugin_adapter("codex")),
            _FakeEntryPoint("bad-name", _plugin_adapter("Bad_Name")),
            _FakeEntryPoint(
                "bad-capability",
                _plugin_adapter("weird", frozenset({"not-a-capability"})),
            ),
            _FakeEntryPoint("not-an-adapter", object()),
            _FakeEntryPoint("dupe-one", _plugin_adapter("dupe")),
            _FakeEntryPoint("dupe-two", _plugin_adapter("dupe")),
        ],
    )
    try:
        assert agents.plugin_adapters().keys() == {"dupe"}
        # The built-in codex adapter is not overridden by the plugin.
        assert agents.resolve_adapter("codex").executable == "codex"
        assert agents.resolve_adapter("codex").build_auth_command is not None
    finally:
        agents.clear_plugin_adapter_cache()
