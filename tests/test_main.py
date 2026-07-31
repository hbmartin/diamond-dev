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


def test_parse_args_accepts_doctor_command() -> None:
    args = parse_args(["doctor"])

    assert args.command == "doctor"
    assert args.config is None
    assert args.plan_path is None
    assert args.commit_args is None


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


def test_parse_args_rejects_doctor_positional_args() -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_args(["doctor", "plan.md"])

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


def test_package_version_falls_back_to_pyproject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "9.8.7"\n',
        encoding="utf-8",
    )

    def missing_package_version(_package_name: str) -> str:
        raise main_module.metadata.PackageNotFoundError

    monkeypatch.setattr(main_module.metadata, "version", missing_package_version)
    monkeypatch.setattr(main_module, "PROJECT_ROOT", tmp_path)

    assert main_module._package_version() == "9.8.7"  # noqa: SLF001


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


def test_main_dispatches_doctor(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path | None] = []

    class FakeOrchestrator:
        def __init__(self, *, config_path: Path | None = None) -> None:
            calls.append(config_path)

        def doctor(self) -> int:
            return 0

    monkeypatch.setattr(main_module, "DiamondDevOrchestrator", FakeOrchestrator)

    assert main_module.main(["--config", "custom.toml", "doctor"]) == 0
    assert calls == [Path("custom.toml")]


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


def test_main_returns_interrupt_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeOrchestrator:
        def __init__(self, *, config_path: Path | None = None) -> None:
            assert config_path is None

        def run(self, plan_path: Path) -> int:
            assert plan_path == Path("plan.md")
            raise KeyboardInterrupt

    monkeypatch.setattr(main_module, "DiamondDevOrchestrator", FakeOrchestrator)

    assert main_module.main(["plan.md"]) == 130


def test_parse_args_accepts_validate_command() -> None:
    args = main_module.parse_args(["validate"])

    assert args.command == "validate"
    assert args.plan_path is None


def test_parse_args_accepts_status_and_clean_targets() -> None:
    status_args = main_module.parse_args(["status", "My Plan.md"])
    clean_args = main_module.parse_args(["clean", "my-plan", "--force"])

    assert status_args.command == "status"
    assert status_args.target == "My Plan.md"
    assert clean_args.command == "clean"
    assert clean_args.target == "my-plan"
    assert clean_args.force


def test_parse_args_rejects_status_without_target() -> None:
    with pytest.raises(SystemExit):
        main_module.parse_args(["status"])


def test_parse_args_accepts_dry_run_for_plans_only() -> None:
    args = main_module.parse_args(["--dry-run", "plan.md"])

    assert args.command == "run"
    assert args.dry_run

    with pytest.raises(SystemExit):
        main_module.parse_args(["--dry-run", "doctor"])


def test_parse_args_rejects_tui_for_utility_commands() -> None:
    args = main_module.parse_args(["--tui", "plan.md"])

    assert args.tui

    with pytest.raises(SystemExit):
        main_module.parse_args(["--tui", "validate"])


def test_main_dispatches_validate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validated: list[tuple[Path, Path | None]] = []

    def fake_run_validate(cwd: Path, config_path: Path | None = None) -> int:
        validated.append((cwd, config_path))
        return 0

    monkeypatch.setattr(main_module, "run_validate", fake_run_validate)
    monkeypatch.chdir(tmp_path)

    assert main_module.main(["validate"]) == 0
    assert validated == [(tmp_path, None)]


def test_main_dispatches_status_and_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, bool | None]] = []

    def fake_run_status(*, cwd, runner, plan_or_slug, config_path=None):
        del cwd, runner, config_path
        calls.append(("status", plan_or_slug, None))

    def fake_run_clean(*, cwd, runner, plan_or_slug, config_path=None, force=False):
        del cwd, runner, config_path
        calls.append(("clean", plan_or_slug, force))
        return ()

    monkeypatch.setattr(main_module, "run_status", fake_run_status)
    monkeypatch.setattr(main_module, "run_clean", fake_run_clean)
    monkeypatch.chdir(tmp_path)

    assert main_module.main(["status", "my-plan"]) == 0
    assert main_module.main(["clean", "my-plan", "--force"]) == 0
    assert calls == [
        ("status", "my-plan", None),
        ("clean", "my-plan", True),
    ]


def test_main_dispatches_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dry_runs: list[Path] = []

    def fake_run_dry_run(*, cwd, plan_path, config_path=None) -> int:
        del cwd, config_path
        dry_runs.append(plan_path)
        return 0

    monkeypatch.setattr(main_module, "run_dry_run", fake_run_dry_run)
    monkeypatch.chdir(tmp_path)

    assert main_module.main(["--dry-run", "plan.md"]) == 0
    assert dry_runs == [Path("plan.md")]
