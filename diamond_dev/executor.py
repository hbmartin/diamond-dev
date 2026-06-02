"""Streaming command execution with Loguru-backed logs."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import IO

from loguru import logger

from diamond_dev.errors import CommandFailureError


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Result from a completed external command."""

    command: tuple[str, ...]
    cwd: Path
    returncode: int
    log_path: Path
    output: str


@dataclass(slots=True)
class ManagedProcess:
    """Started command whose output is streamed on a background thread."""

    command: tuple[str, ...]
    cwd: Path
    label: str
    process: subprocess.Popen[str]
    output_stream: IO[str]
    log_file: IO[str]
    log_path: Path
    output_thread: Thread

    def wait(self) -> CommandResult:
        """Wait for the process to exit and return its result."""
        returncode = self.process.wait()
        self.output_thread.join()
        self.output_stream.close()
        self.log_file.close()

        result = CommandResult(
            command=self.command,
            cwd=self.cwd,
            returncode=returncode,
            log_path=self.log_path,
            output="",
        )
        if returncode != 0:
            raise _command_failure(result)
        return result


class CommandRunner:
    """Run external commands while recording per-step logs."""

    def __init__(self, log_dir: Path) -> None:
        """Create a command runner using a log directory."""
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        """Run a command and stream output to console and a log file."""
        return self._run_streaming(
            tuple(command),
            cwd=cwd,
            log_name=log_name,
            output_path=None,
            check=check,
        )

    def run_to_file(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        output_path: Path,
        check: bool = True,
    ) -> CommandResult:
        """Run a command and write its output to a target file."""
        return self._run_streaming(
            tuple(command),
            cwd=cwd,
            log_name=log_name,
            output_path=output_path,
            check=check,
        )

    def start(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
    ) -> ManagedProcess:
        """Start a command and begin streaming output asynchronously."""
        command_tuple = tuple(command)
        log_path = self._log_path(log_name)
        log_file = log_path.open("w", encoding="utf-8")
        logger.info("Starting {}: {}", log_name, shlex.join(command_tuple))
        try:
            process = subprocess.Popen(  # noqa: S603
                command_tuple,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except (OSError, ValueError) as error:
            log_file.close()
            raise CommandFailureError(
                command=shlex.join(command_tuple),
                cwd=str(cwd),
                returncode=127,
                log_path=str(log_path),
            ) from error

        if process.stdout is None:
            log_file.close()
            raise CommandFailureError(
                command=shlex.join(command_tuple),
                cwd=str(cwd),
                returncode=127,
                log_path=str(log_path),
            )

        output_thread = Thread(
            target=_stream_output,
            args=(process.stdout, log_file, log_name),
            daemon=True,
        )
        output_thread.start()
        return ManagedProcess(
            command=command_tuple,
            cwd=cwd,
            label=log_name,
            process=process,
            output_stream=process.stdout,
            log_file=log_file,
            log_path=log_path,
            output_thread=output_thread,
        )

    def run_interactive(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        """Run an interactive command without capturing its terminal streams."""
        command_tuple = tuple(command)
        log_path = self._log_path(log_name)
        logger.info("Starting interactive {}: {}", log_name, shlex.join(command_tuple))
        with log_path.open("w", encoding="utf-8") as log_file:
            log_file.write(f"$ {shlex.join(command_tuple)}\n")
            try:
                completed = subprocess.run(  # noqa: S603
                    command_tuple,
                    cwd=cwd,
                    check=False,
                )
            except (OSError, ValueError) as error:
                raise CommandFailureError(
                    command=shlex.join(command_tuple),
                    cwd=str(cwd),
                    returncode=127,
                    log_path=str(log_path),
                ) from error
            log_file.write(f"exit_code={completed.returncode}\n")

        result = CommandResult(
            command=command_tuple,
            cwd=cwd,
            returncode=completed.returncode,
            log_path=log_path,
            output="",
        )
        if check and completed.returncode != 0:
            raise _command_failure(result)
        return result

    def _run_streaming(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        log_name: str,
        output_path: Path | None,
        check: bool,
    ) -> CommandResult:
        log_path = self._log_path(log_name)
        logger.info("Running {}: {}", log_name, shlex.join(command))
        captured_output: list[str] | None = [] if output_path is None else None

        with log_path.open("w", encoding="utf-8") as log_file:
            output_file = (
                output_path.open("w", encoding="utf-8") if output_path else None
            )
            try:
                process = subprocess.Popen(  # noqa: S603
                    command,
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except (OSError, ValueError) as error:
                if output_file is not None:
                    output_file.close()
                raise CommandFailureError(
                    command=shlex.join(command),
                    cwd=str(cwd),
                    returncode=127,
                    log_path=str(log_path),
                ) from error

            if process.stdout is None:
                if output_file is not None:
                    output_file.close()
                raise CommandFailureError(
                    command=shlex.join(command),
                    cwd=str(cwd),
                    returncode=127,
                    log_path=str(log_path),
                )

            try:
                for line in process.stdout:
                    if captured_output is not None:
                        captured_output.append(line)
                    log_file.write(line)
                    log_file.flush()
                    if output_file is not None:
                        output_file.write(line)
                        output_file.flush()
                    logger.info("[{}] {}", log_name, line.rstrip("\n"))
            finally:
                process.stdout.close()
                if output_file is not None:
                    output_file.close()

            returncode = process.wait()

        result = CommandResult(
            command=command,
            cwd=cwd,
            returncode=returncode,
            log_path=log_path,
            output="".join(captured_output) if captured_output is not None else "",
        )
        if check and returncode != 0:
            raise _command_failure(result)
        return result

    def _log_path(self, log_name: str) -> Path:
        safe_name = re_slug(log_name)
        return self.log_dir / f"{safe_name}.log"


def re_slug(value: str) -> str:
    """Return a filesystem-safe log filename stem."""
    return "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in value
    )


def _stream_output(output_stream: IO[str], log_file: IO[str], label: str) -> None:
    for line in output_stream:
        log_file.write(line)
        log_file.flush()
        logger.info("[{}] {}", label, line.rstrip("\n"))


def _command_failure(result: CommandResult) -> CommandFailureError:
    return CommandFailureError(
        command=shlex.join(result.command),
        cwd=str(result.cwd),
        returncode=result.returncode,
        log_path=str(result.log_path),
    )
