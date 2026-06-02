"""Tests for the command-line interface."""

from __future__ import annotations

from pathlib import Path

import pytest

from diamond_dev.main import parse_args


def test_parse_args_accepts_config_path() -> None:
    args = parse_args(["--config", "custom.toml", "plan.md"])

    assert args.config == Path("custom.toml")
    assert args.plan_path == Path("plan.md")


def test_parse_args_supports_version_flag() -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_args(["--version"])

    assert exit_info.value.code == 0
