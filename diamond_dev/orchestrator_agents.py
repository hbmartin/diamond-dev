"""Shared agent command helpers for orchestration phases."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from diamond_dev.agents import AgentAdapter, AgentCapability, resolve_adapter

if TYPE_CHECKING:
    from diamond_dev.workflow import ImplementationBranch, RunContext


def initial_agent_command(
    context: RunContext,
    agent_branch: ImplementationBranch,
    prompt: str,
) -> tuple[str, ...]:
    """Build the implementation command for one agent branch."""
    return prompt_agent_command(
        context=context,
        agent_name=agent_branch.agent_name,
        repo_dir=agent_branch.repo_dir,
        prompt=prompt,
        capability="implementation",
    )


def prompt_agent_command(
    context: RunContext,
    agent_name: str,
    repo_dir: Path,
    prompt: str,
    *,
    capability: AgentCapability,
) -> tuple[str, ...]:
    """Build a prompt command for a configured workflow agent."""
    adapter = _agent_adapter(context, agent_name)
    return adapter.prompt_command(
        repo_dir,
        prompt,
        model=_agent_model(context, agent_name),
        capability=capability,
    )


def review_provider_command(context: RunContext) -> tuple[str, ...]:
    """Build the configured review provider command."""
    agent_name = context.config.workflow.review_provider
    adapter = _agent_adapter(context, agent_name)
    return adapter.review_command(
        context.implementation.base_branch,
        model=_agent_model(context, agent_name),
    )


def final_review_command(context: RunContext, pr_number: str) -> tuple[str, ...]:
    """Build the configured final interactive review command."""
    agent_name = context.config.workflow.final_reviewer
    adapter = _agent_adapter(context, agent_name)
    return adapter.interactive_review_command(
        pr_number,
        model=_agent_model(context, agent_name),
    )


def agent_label(agent_name: str) -> str:
    """Return a display label for a configured agent."""
    return {
        "codex": "Codex",
        "claude": "Claude",
        "coderabbit": "CodeRabbit",
        "gemini": "Gemini",
    }.get(agent_name, agent_name)


def _agent_adapter(context: RunContext, agent_name: str) -> AgentAdapter:
    adapter_name = context.config.agent_adapter_name(agent_name)
    return resolve_adapter(adapter_name)


def _agent_model(context: RunContext, agent_name: str) -> str | None:
    return context.config.agent_config(agent_name).model
