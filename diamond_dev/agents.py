"""Built-in agent adapter registry for Diamond Dev."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from diamond_dev.commands import (
    build_claude_interactive_review_command,
    build_claude_print_command,
    build_coderabbit_review_command,
    build_codex_command,
    build_gemini_command,
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


@dataclass(frozen=True, slots=True)
class AgentAdapter:
    """Command adapter and capability metadata for one built-in agent CLI."""

    name: str
    executable: str
    capabilities: frozenset[AgentCapability]
    build_prompt_command: PromptCommandBuilder | None = None
    build_review_command: ReviewCommandBuilder | None = None
    build_interactive_review_command: InteractiveReviewCommandBuilder | None = None

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
    ),
    "claude": AgentAdapter(
        name="claude",
        executable="claude",
        capabilities=frozenset({*_CODE_WRITER_CAPABILITIES, "final_reviewer"}),
        build_prompt_command=_claude_prompt_command,
        build_interactive_review_command=_claude_interactive_review_command,
    ),
    "gemini": AgentAdapter(
        name="gemini",
        executable="gemini",
        capabilities=frozenset({"comparison_judge"}),
        build_prompt_command=_gemini_prompt_command,
    ),
    "coderabbit": AgentAdapter(
        name="coderabbit",
        executable="coderabbit",
        capabilities=frozenset({"review_provider"}),
        build_review_command=_coderabbit_review_command,
    ),
}


def adapter_names() -> frozenset[str]:
    """Return names of built-in adapters."""
    return frozenset(BUILTIN_AGENT_ADAPTERS)


def resolve_adapter(adapter_name: str) -> AgentAdapter:
    """Return a built-in adapter by name."""
    try:
        return BUILTIN_AGENT_ADAPTERS[adapter_name]
    except (KeyError,) as error:
        raise DiamondDevError(f"Unknown agent adapter: {adapter_name}") from error
