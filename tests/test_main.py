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
    assert args.commit_args is None


def test_parse_args_accepts_two_commit_refs() -> None:
    args = parse_args(["abc123", "def456"])

    assert args.command == "compare-commits"
    assert args.plan_path is None
    assert args.commit_args == ("abc123", "def456")


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


def test_parse_args_rejects_invalid_positional_arity() -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_args(["one", "two", "three"])

    assert exit_info.value.code == 2


def test_parse_args_no_args_mentions_commit_refs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_args([])

    assert exit_info.value.code == 2
    assert "two commit-ish refs" in capsys.readouterr().err


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


def test_main_dispatches_commit_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeOrchestrator:
        def __init__(self, *, config_path: Path | None = None) -> None:
            assert config_path == Path("custom.toml")

        def run_commits(self, commit_args: tuple[str, str]) -> int:
            calls.append(commit_args)
            return 0

    monkeypatch.setattr(main_module, "DiamondDevOrchestrator", FakeOrchestrator)

    assert main_module.main(["--config", "custom.toml", "abc123", "def456"]) == 0
    assert calls == [("abc123", "def456")]
