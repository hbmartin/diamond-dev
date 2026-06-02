"""Tests for command execution helpers."""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from typing import NoReturn

import pytest

from diamond_dev import executor
from diamond_dev.errors import CommandFailureError
from diamond_dev.executor import CommandRunner


def test_run_to_file_streams_without_returning_full_output(tmp_path: Path) -> None:
    runner = CommandRunner(tmp_path / "logs")
    output_path = tmp_path / "output.txt"

    result = runner.run_to_file(
        (sys.executable, "-c", "import sys; sys.stdout.write('hello\\n')"),
        cwd=tmp_path,
        log_name="write-output",
        output_path=output_path,
    )

    assert output_path.read_text(encoding="utf-8").replace("\r\n", "\n") == "hello\n"
    assert result.output == ""


def test_run_to_file_wraps_popen_value_error(tmp_path: Path, monkeypatch) -> None:
    runner = CommandRunner(tmp_path / "logs")

    def fail_popen(*_args: object, **_kwargs: object) -> NoReturn:
        raise ValueError("invalid command")

    monkeypatch.setattr(executor.subprocess, "Popen", fail_popen)

    with pytest.raises(CommandFailureError):
        runner.run_to_file(
            ("bad-command",),
            cwd=tmp_path,
            log_name="bad-command",
            output_path=tmp_path / "output.txt",
        )


def test_run_to_file_closes_output_file_on_launch_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CommandRunner(tmp_path / "logs")
    output_path = tmp_path / "output.txt"
    output_writer = StringIO()
    original_open = Path.open

    def tracking_open(self: Path, *args: object, **kwargs: object):
        if self == output_path:
            return output_writer
        return original_open(self, *args, **kwargs)

    def fail_popen(*_args: object, **_kwargs: object) -> NoReturn:
        raise OSError("launch failed")

    monkeypatch.setattr(Path, "open", tracking_open)
    monkeypatch.setattr(executor.subprocess, "Popen", fail_popen)

    with pytest.raises(CommandFailureError):
        runner.run_to_file(
            ("bad-command",),
            cwd=tmp_path,
            log_name="bad-command",
            output_path=output_path,
        )

    assert output_writer.closed


def test_started_process_wait_closes_streams(tmp_path: Path) -> None:
    runner = CommandRunner(tmp_path / "logs")

    managed_process = runner.start(
        (sys.executable, "-c", "import sys; sys.stdout.write('hello\\n')"),
        cwd=tmp_path,
        log_name="async-output",
    )
    result = managed_process.wait()

    assert result.returncode == 0
    assert managed_process.resources.output_stream.closed
    assert managed_process.resources.log_file.closed


def test_run_interactive_wraps_launch_failures(tmp_path: Path) -> None:
    runner = CommandRunner(tmp_path / "logs")

    with pytest.raises(CommandFailureError):
        runner.run_interactive(
            ("diamond-dev-command-that-does-not-exist",),
            cwd=tmp_path,
            log_name="missing-command",
        )
