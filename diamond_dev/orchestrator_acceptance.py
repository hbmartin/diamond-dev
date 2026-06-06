"""Acceptance polling phase for Diamond Dev orchestration."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from loguru import logger

from diamond_dev.acceptance import acceptance_wait_delays, parse_acceptance
from diamond_dev.errors import DiamondDevError

if TYPE_CHECKING:
    from diamond_dev.providers import GitHubWorkflowProvider
    from diamond_dev.workflow import RunContext


class AcceptancePollingMixin:
    """Poll the wiki comparison page for a selected implementation."""

    sleep: Callable[[float], None]
    workflow_provider: GitHubWorkflowProvider

    def _poll_acceptance(self, context: RunContext) -> str:
        if accepted_agent := self._check_acceptance_once(context):
            return accepted_agent

        delays = acceptance_wait_delays(
            poll_interval_seconds=context.config.acceptance.poll_interval_seconds,
            max_wait_seconds=context.config.acceptance.max_wait_seconds,
        )
        for attempt_number, delay_seconds in enumerate(delays, start=1):
            logger.info(
                "Waiting {} seconds before acceptance check {}",
                delay_seconds,
                attempt_number,
            )
            self.sleep(delay_seconds)
            if accepted_agent := self._check_acceptance_once(context):
                return accepted_agent

        raise DiamondDevError("No valid acceptance found after polling window")

    def _check_acceptance_once(self, context: RunContext) -> str | None:
        self.workflow_provider.sync_wiki(context.wiki.directory)
        if not context.wiki.comparison_file.is_file():
            logger.warning(
                "Comparison file {} not found in wiki repository",
                context.wiki.comparison_file,
            )
            return None

        comparison_markdown = context.wiki.comparison_file.read_text(encoding="utf-8")
        if accepted_agent := parse_acceptance(
            comparison_markdown,
            context.implementation.implementer_names,
        ):
            logger.info("Accepted implementation: {}", accepted_agent)
            return accepted_agent
        logger.info("No accepted implementation found yet")
        return None
