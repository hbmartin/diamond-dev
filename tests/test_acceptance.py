"""Tests for comparison acceptance markers."""

from __future__ import annotations

import pytest

from diamond_dev.acceptance import (
    ACCEPTANCE_CHECKBOX,
    AgentChoice,
    acceptance_wait_delays,
    append_acceptance_checkbox,
    parse_acceptance,
)
from diamond_dev.errors import MalformedAcceptanceError


def test_append_acceptance_checkbox_adds_exact_line() -> None:
    markdown = append_acceptance_checkbox("# Comparison")

    assert markdown == f"# Comparison\n{ACCEPTANCE_CHECKBOX}\n"


def test_parse_acceptance_waiting_state() -> None:
    assert parse_acceptance(ACCEPTANCE_CHECKBOX) is None


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("- [x] Accept: codex", "codex"),
        ("- [x] Accept: claude", "claude"),
    ],
)
def test_parse_acceptance_checked_values(
    markdown: str,
    expected: AgentChoice,
) -> None:
    assert parse_acceptance(markdown) == expected


def test_parse_acceptance_ignores_non_checkbox_accept_mentions() -> None:
    markdown = 'Please update the "Accept: codex" checkbox below.'

    assert parse_acceptance(markdown) is None


@pytest.mark.parametrize(
    "markdown",
    [
        "- [X] Accept: codex",
        "- [x] Accept: both",
        "- [x] Accept: codex\n- [x] Accept: claude",
    ],
)
def test_parse_acceptance_rejects_malformed_values(markdown: str) -> None:
    with pytest.raises(MalformedAcceptanceError):
        parse_acceptance(markdown)


def test_acceptance_wait_delays_are_deterministic() -> None:
    assert acceptance_wait_delays() == (
        120,
        180,
        240,
        300,
        360,
        420,
        480,
        540,
        600,
        660,
        720,
    )
