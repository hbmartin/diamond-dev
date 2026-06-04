"""Tests for the command-line interface."""

from __future__ import annotations

from pathlib import Path

import pytest

from diamond_dev import main as main_module
from diamond_dev.main import parse_args


def test_parse_args_accepts_config_path() -> None:
    args = parse_args(["--config", "custom.toml", "plan.md"])

    assert args.command == "run"
    assert args.config == Path("custom.toml")
    assert args.plan_path == Path("plan.md")


def test_parse_args_accepts_init_command() -> None:
    args = parse_args(["init"])

    assert args.command == "init"
    assert args.config is None
    assert args.plan_path is None
    assert not args.force


def test_parse_args_accepts_init_config_after_command() -> None:
    args = parse_args(["init", "--config", "custom.toml"])

    assert args.command == "init"
    assert args.config == Path("custom.toml")


def test_parse_args_accepts_init_config_before_command() -> None:
    args = parse_args(["--config", "custom.toml", "init"])

    assert args.command == "init"
    assert args.config == Path("custom.toml")


def test_parse_args_accepts_init_force() -> None:
    args = parse_args(["init", "--force"])

    assert args.command == "init"
    assert args.force


def test_parse_args_rejects_force_for_run() -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_args(["--force", "plan.md"])

    assert exit_info.value.code == 2


def test_parse_args_supports_version_flag() -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_args(["--version"])

    assert exit_info.value.code == 0


def test_main_dispatches_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Path, Path | None, bool]] = []

    def fake_run_config_init(
        cwd: Path,
        config_path: Path | None,
        *,
        force: bool,
    ) -> Path:
        calls.append((cwd, config_path, force))
        return cwd / "custom.toml"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_module, "run_config_init", fake_run_config_init)

    assert main_module.main(["init", "--config", "custom.toml", "--force"]) == 0
    assert calls == [(tmp_path, Path("custom.toml"), True)]
