"""Built-in and plugin agent adapter registry for Diamond Dev."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Final, Literal, get_args

from loguru import logger

from diamond_dev.commands import (
    build_aider_command,
    build_claude_interactive_review_command,
    build_claude_print_command,
    build_coderabbit_review_command,
    build_codex_command,
    build_copilot_command,
    build_cursor_agent_command,
    build_gemini_command,
    build_opencode_command,
    build_qwen_command,
)
from diamond_dev.errors import DiamondDevError

type AgentCapability = Literal[
    "implementation",
    "comparison_judge",
    "comparison_fixer",
    "review_provider",
    "review_judge",
    "review_fixer",
    "final_reviewer",
]
type PromptCommandBuilder = Callable[[Path, str, str | None], tuple[str, ...]]
type ReviewCommandBuilder = Callable[[str, str | None], tuple[str, ...]]
type InteractiveReviewCommandBuilder = Callable[[str, str | None], tuple[str, ...]]
type AuthCommandBuilder = Callable[[str | None], tuple[str, ...]]

PLUGIN_ENTRY_POINT_GROUP: Final = "diamond_dev.agents"
_ADAPTER_NAME_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CAPABILITY_NAMES: Final = frozenset(get_args(AgentCapability.__value__))
_DOCTOR_GEMINI_PROMPT: Final = (
    "Diamond Dev doctor authentication check. Reply with exactly OK and do not "
    "use tools."
)


@dataclass(frozen=True, slots=True)
class AgentAdapter:
    """Command adapter and capability metadata for one agent CLI."""

    # pylint: disable=too-many-instance-attributes

    name: str
    executable: str
    capabilities: frozenset[AgentCapability]
    build_prompt_command: PromptCommandBuilder | None = None
    build_review_command: ReviewCommandBuilder | None = None
    build_interactive_review_command: InteractiveReviewCommandBuilder | None = None
    build_auth_command: AuthCommandBuilder | None = None

    def has_capability(self, capability: AgentCapability) -> bool:
        """Return whether this adapter can fill a workflow role."""
        return capability in self.capabilities

    def require_capability(self, capability: AgentCapability) -> None:
        """Raise when this adapter cannot fill a workflow role."""
        if not self.has_capability(capability):
            raise DiamondDevError(
                f"Agent adapter `{self.name}` does not support `{capability}`",
            )

    def prompt_command(
        self,
        repo_dir: Path,
        prompt: str,
        *,
        model: str | None,
        capability: AgentCapability,
    ) -> tuple[str, ...]:
        """Build a prompt-driven command for a capable adapter."""
        self.require_capability(capability)
        if self.build_prompt_command is None:
            raise DiamondDevError(
                f"Agent adapter `{self.name}` cannot build prompt commands",
            )
        return self.build_prompt_command(repo_dir, prompt, model)

    def review_command(
        self,
        base_branch: str,
        *,
        model: str | None,
    ) -> tuple[str, ...]:
        """Build a review-provider command."""
        self.require_capability("review_provider")
        if self.build_review_command is None:
            raise DiamondDevError(
                f"Agent adapter `{self.name}` cannot build review commands",
            )
        return self.build_review_command(base_branch, model)

    def interactive_review_command(
        self,
        pr_number: str,
        *,
        model: str | None,
    ) -> tuple[str, ...]:
        """Build an interactive final-review command."""
        self.require_capability("final_reviewer")
        if self.build_interactive_review_command is None:
            raise DiamondDevError(
                f"Agent adapter `{self.name}` cannot build interactive review commands",
            )
        return self.build_interactive_review_command(pr_number, model)

    def auth_command(self, *, model: str | None) -> tuple[str, ...] | None:
        """Build the doctor authentication probe command, if one exists."""
        if self.build_auth_command is None:
            return None
        return self.build_auth_command(model)


def _codex_prompt_command(
    repo_dir: Path,
    prompt: str,
    model: str | None,
) -> tuple[str, ...]:
    return build_codex_command(repo_dir, prompt, model=model)


def _claude_prompt_command(
    _repo_dir: Path,
    prompt: str,
    model: str | None,
) -> tuple[str, ...]:
    return build_claude_print_command(prompt, model=model)


def _gemini_prompt_command(
    _repo_dir: Path,
    prompt: str,
    model: str | None,
) -> tuple[str, ...]:
    return build_gemini_command(prompt, model=model)


def _opencode_prompt_command(
    _repo_dir: Path,
    prompt: str,
    model: str | None,
) -> tuple[str, ...]:
    return build_opencode_command(prompt, model=model)


def _cursor_agent_prompt_command(
    _repo_dir: Path,
    prompt: str,
    model: str | None,
) -> tuple[str, ...]:
    return build_cursor_agent_command(prompt, model=model)


def _copilot_prompt_command(
    _repo_dir: Path,
    prompt: str,
    model: str | None,
) -> tuple[str, ...]:
    return build_copilot_command(prompt, model=model)


def _qwen_prompt_command(
    _repo_dir: Path,
    prompt: str,
    model: str | None,
) -> tuple[str, ...]:
    return build_qwen_command(prompt, model=model)


def _aider_prompt_command(
    _repo_dir: Path,
    prompt: str,
    model: str | None,
) -> tuple[str, ...]:
    return build_aider_command(prompt, model=model)


def _coderabbit_review_command(
    base_branch: str,
    _model: str | None,
) -> tuple[str, ...]:
    return build_coderabbit_review_command(base_branch)


def _claude_interactive_review_command(
    pr_number: str,
    model: str | None,
) -> tuple[str, ...]:
    return build_claude_interactive_review_command(pr_number, model=model)


def _codex_auth_command(_model: str | None) -> tuple[str, ...]:
    return ("codex", "login", "status")


def _claude_auth_command(_model: str | None) -> tuple[str, ...]:
    return ("claude", "auth", "status", "--text")


def _gemini_auth_command(model: str | None) -> tuple[str, ...]:
    model_args = () if model is None else ("-m", model)
    return ("gemini", *model_args, "-p", _DOCTOR_GEMINI_PROMPT, "--skip-trust")


def _coderabbit_auth_command(_model: str | None) -> tuple[str, ...]:
    return ("coderabbit", "auth", "status", "--agent")


def _opencode_auth_command(_model: str | None) -> tuple[str, ...]:
    return ("opencode", "auth", "list")


def _cursor_agent_auth_command(_model: str | None) -> tuple[str, ...]:
    return ("cursor-agent", "status")


_CODE_WRITER_CAPABILITIES: frozenset[AgentCapability] = frozenset(
    {
        "implementation",
        "comparison_fixer",
        "review_judge",
        "review_fixer",
    },
)

BUILTIN_AGENT_ADAPTERS: Mapping[str, AgentAdapter] = {
    "codex": AgentAdapter(
        name="codex",
        executable="codex",
        capabilities=_CODE_WRITER_CAPABILITIES,
        build_prompt_command=_codex_prompt_command,
        build_auth_command=_codex_auth_command,
    ),
    "claude": AgentAdapter(
        name="claude",
        executable="claude",
        capabilities=frozenset({*_CODE_WRITER_CAPABILITIES, "final_reviewer"}),
        build_prompt_command=_claude_prompt_command,
        build_interactive_review_command=_claude_interactive_review_command,
        build_auth_command=_claude_auth_command,
    ),
    "gemini": AgentAdapter(
        name="gemini",
        executable="gemini",
        capabilities=frozenset({"comparison_judge"}),
        build_prompt_command=_gemini_prompt_command,
        build_auth_command=_gemini_auth_command,
    ),
    "coderabbit": AgentAdapter(
        name="coderabbit",
        executable="coderabbit",
        capabilities=frozenset({"review_provider"}),
        build_review_command=_coderabbit_review_command,
        build_auth_command=_coderabbit_auth_command,
    ),
    "opencode": AgentAdapter(
        name="opencode",
        executable="opencode",
        capabilities=_CODE_WRITER_CAPABILITIES,
        build_prompt_command=_opencode_prompt_command,
        build_auth_command=_opencode_auth_command,
    ),
    "cursor-agent": AgentAdapter(
        name="cursor-agent",
        executable="cursor-agent",
        capabilities=_CODE_WRITER_CAPABILITIES,
        build_prompt_command=_cursor_agent_prompt_command,
        build_auth_command=_cursor_agent_auth_command,
    ),
    "copilot": AgentAdapter(
        name="copilot",
        executable="copilot",
        capabilities=_CODE_WRITER_CAPABILITIES,
        build_prompt_command=_copilot_prompt_command,
    ),
    "qwen": AgentAdapter(
        name="qwen",
        executable="qwen",
        capabilities=_CODE_WRITER_CAPABILITIES,
        build_prompt_command=_qwen_prompt_command,
    ),
    "aider": AgentAdapter(
        name="aider",
        executable="aider",
        capabilities=_CODE_WRITER_CAPABILITIES,
        build_prompt_command=_aider_prompt_command,
    ),
}

_plugin_adapter_cache: dict[str, AgentAdapter] | None = None


def adapter_names() -> frozenset[str]:
    """Return names of built-in and plugin adapters."""
    return frozenset(BUILTIN_AGENT_ADAPTERS) | frozenset(plugin_adapters())


def resolve_adapter(adapter_name: str) -> AgentAdapter:
    """Return a built-in or plugin adapter by name."""
    if builtin_adapter := BUILTIN_AGENT_ADAPTERS.get(adapter_name):
        return builtin_adapter
    if plugin_adapter := plugin_adapters().get(adapter_name):
        return plugin_adapter
    raise DiamondDevError(f"Unknown agent adapter: {adapter_name}")


def plugin_adapters() -> Mapping[str, AgentAdapter]:
    """Return adapters registered via `diamond_dev.agents` entry points."""
    global _plugin_adapter_cache  # noqa: PLW0603 # pylint: disable=global-statement
    if _plugin_adapter_cache is None:
        _plugin_adapter_cache = _load_plugin_adapters()
    return _plugin_adapter_cache


def clear_plugin_adapter_cache() -> None:
    """Forget loaded plugin adapters so the next lookup reloads entry points."""
    global _plugin_adapter_cache  # noqa: PLW0603 # pylint: disable=global-statement
    _plugin_adapter_cache = None


def _load_plugin_adapters() -> dict[str, AgentAdapter]:
    adapters: dict[str, AgentAdapter] = {}
    for entry_point in metadata.entry_points(group=PLUGIN_ENTRY_POINT_GROUP):
        # pylint: disable-next=broad-exception-caught
        try:
            loaded = entry_point.load()
            for adapter in _adapters_from_entry_point(loaded):
                _register_plugin_adapter(adapters, adapter, entry_point.name)
        except (Exception,) as error:  # noqa: BLE001
            logger.warning(
                "Skipping agent adapter plugin `{}`: {}",
                entry_point.name,
                error,
            )
    return adapters


def _adapters_from_entry_point(loaded: object) -> tuple[AgentAdapter, ...]:
    if callable(loaded) and not isinstance(loaded, AgentAdapter):
        loaded = loaded()
    if isinstance(loaded, AgentAdapter):
        return (loaded,)
    if isinstance(loaded, Iterable) and not isinstance(loaded, str | bytes):
        candidates = tuple(loaded)
        if all(isinstance(candidate, AgentAdapter) for candidate in candidates):
            return candidates
    raise DiamondDevError(
        "Plugin entry point must provide an AgentAdapter, an iterable of "
        "AgentAdapter, or a callable returning either",
    )


def _register_plugin_adapter(
    adapters: dict[str, AgentAdapter],
    adapter: AgentAdapter,
    entry_point_name: str,
) -> None:
    if _ADAPTER_NAME_PATTERN.fullmatch(adapter.name) is None:
        logger.warning(
            "Skipping plugin adapter `{}` from `{}`: adapter names must contain "
            "only lowercase letters, numbers, and single hyphen separators",
            adapter.name,
            entry_point_name,
        )
        return
    if not adapter.capabilities <= _CAPABILITY_NAMES:
        unknown = ", ".join(sorted(adapter.capabilities - _CAPABILITY_NAMES))
        logger.warning(
            "Skipping plugin adapter `{}` from `{}`: unknown capabilities {}",
            adapter.name,
            entry_point_name,
            unknown,
        )
        return
    if adapter.name in BUILTIN_AGENT_ADAPTERS:
        logger.warning(
            "Skipping plugin adapter `{}` from `{}`: cannot override a "
            "built-in adapter",
            adapter.name,
            entry_point_name,
        )
        return
    if adapter.name in adapters:
        logger.warning(
            "Skipping duplicate plugin adapter `{}` from `{}`",
            adapter.name,
            entry_point_name,
        )
        return
    adapters[adapter.name] = adapter
