"""Acceptance marker helpers for comparison notes."""

from __future__ import annotations

from typing import Final, Literal

from diamond_dev.errors import MalformedAcceptanceError

type AgentChoice = Literal["codex", "claude"]

ACCEPTANCE_CHECKBOX: Final = "- [ ] Accept: (codex/claude)"
CODEX_ACCEPTED_LINE: Final = "- [x] Accept: codex"
CLAUDE_ACCEPTED_LINE: Final = "- [x] Accept: claude"


def append_acceptance_checkbox(markdown: str) -> str:
    """Append the deterministic acceptance checkbox to markdown content."""
    separator = "" if markdown.endswith("\n") else "\n"
    return f"{markdown}{separator}{ACCEPTANCE_CHECKBOX}\n"


def parse_acceptance(markdown: str) -> AgentChoice | None:
    """Parse the comparison acceptance marker."""
    acceptance_lines = [
        line.strip() for line in markdown.splitlines() if "Accept:" in line
    ]
    if not acceptance_lines:
        return None
    if len(acceptance_lines) > 1:
        raise MalformedAcceptanceError("Comparison file has multiple accept markers")

    match acceptance_lines[0]:
        case "- [ ] Accept: (codex/claude)":
            return None
        case "- [x] Accept: codex":
            return "codex"
        case "- [x] Accept: claude":
            return "claude"
        case invalid_line:
            raise MalformedAcceptanceError(
                f"Invalid acceptance marker: {invalid_line}",
            )


def acceptance_wait_delays() -> tuple[int, ...]:
    """Return acceptance polling waits in seconds."""
    return (120, *(minutes * 60 for minutes in range(3, 13)))
