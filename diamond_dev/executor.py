"""Streaming command execution with Loguru-backed logs."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import IO, Protocol

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


@dataclass(frozen=True, slots=True)
class CommandLogRecord:
    """Log metadata for a command started by the runner."""

    label: str
    command: tuple[str, ...]
    cwd: Path
    log_path: Path


class StartedCommand(Protocol):
    """Started command object that can be waited on."""

    def wait(self) -> CommandResult:
        """Wait for command completion."""
        ...


class CommandExecutor(Protocol):
    """Synchronous command runner boundary."""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        """Run a command and return its result."""
        ...


class FileCommandExecutor(Protocol):
    """Command runner boundary for commands that write output to a file."""

    def run_to_file(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        output_path: Path,
        check: bool = True,
    ) -> CommandResult:
        """Run a command and write captured output to a file."""
        ...


class AsyncCommandExecutor(Protocol):
    """Command runner boundary for background commands."""

    def start(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
    ) -> StartedCommand:
        """Start a command and return a waitable process."""
        ...


class InteractiveCommandExecutor(Protocol):
    """Command runner boundary for interactive commands."""

    def run_interactive(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        log_name: str,
        check: bool = True,
    ) -> CommandResult:
        """Run an interactive command."""
        ...


class CommandRunnerLike(
    CommandExecutor,
    FileCommandExecutor,
    AsyncCommandExecutor,
    InteractiveCommandExecutor,
    Protocol,
):
    """Complete command runner boundary used by orchestration."""


@dataclass(frozen=True, slots=True)
class ProcessMetadata:
    """Immutable identifying information for a managed process."""

    command: tuple[str, ...]
    cwd: Path
    label: str
    log_path: Path


@dataclass(slots=True)
class ProcessResources:
    """Open resources owned by a managed process."""

    process: subprocess.Popen[str]
    output_stream: IO[str]
    log_file: IO[str]
    output_thread: Thread


@dataclass(slots=True)
class ManagedProcess:
    """Started command whose output is streamed on a background thread."""

    metadata: ProcessMetadata
    resources: ProcessResources

    def wait(self) -> CommandResult:
        """Wait for the process to exit and return its result."""
        returncode = self.resources.process.wait()
        self.resources.output_thread.join()
        self.resources.output_stream.close()
        self.resources.log_file.close()

        result = CommandResult(
            command=self.metadata.command,
            cwd=self.metadata.cwd,
            returncode=returncode,
            log_path=self.metadata.log_path,
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
        self.command_logs: list[CommandLogRecord] = []
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
    ) -> StartedCommand:
        """Start a command and begin streaming output asynchronously."""
        command_tuple = tuple(command)
        log_path = self._log_path(log_name)
        self._record_command_log(
            label=log_name,
            command=command_tuple,
            cwd=cwd,
            log_path=log_path,
        )
        log_file = log_path.open("w", encoding="utf-8")
        logger.info("Starting {}: {}", log_name, shlex.join(command_tuple))
        try:
            # pylint: disable-next=consider-using-with
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
            metadata=ProcessMetadata(
                command=command_tuple,
                cwd=cwd,
                label=log_name,
                log_path=log_path,
            ),
            resources=ProcessResources(
                process=process,
                output_stream=process.stdout,
                log_file=log_file,
                output_thread=output_thread,
            ),
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
        self._record_command_log(
            label=log_name,
            command=command_tuple,
            cwd=cwd,
            log_path=log_path,
        )
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
        self._record_command_log(
            label=log_name,
            command=command,
            cwd=cwd,
            log_path=log_path,
        )
        logger.info("Running {}: {}", log_name, shlex.join(command))
        captured_output: list[str] | None = [] if output_path is None else None

        with ExitStack() as stack:
            log_file = stack.enter_context(log_path.open("w", encoding="utf-8"))
            output_file = None
            if output_path is not None:
                output_file = stack.enter_context(
                    output_path.open("w", encoding="utf-8"),
                )
            try:
                process = stack.enter_context(
                    subprocess.Popen(  # noqa: S603
                        command,
                        cwd=cwd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    ),
                )
            except (OSError, ValueError) as error:
                raise CommandFailureError(
                    command=shlex.join(command),
                    cwd=str(cwd),
                    returncode=127,
                    log_path=str(log_path),
                ) from error

            if process.stdout is None:
                raise CommandFailureError(
                    command=shlex.join(command),
                    cwd=str(cwd),
                    returncode=127,
                    log_path=str(log_path),
                )

            _stream_command_output(
                output_stream=process.stdout,
                log_file=log_file,
                output_file=output_file,
                captured_output=captured_output,
                label=log_name,
            )

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

    def _record_command_log(
        self,
        *,
        label: str,
        command: tuple[str, ...],
        cwd: Path,
        log_path: Path,
    ) -> None:
        self.command_logs.append(
            CommandLogRecord(
                label=label,
                command=command,
                cwd=cwd,
                log_path=log_path,
            ),
        )


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


def _stream_command_output(
    *,
    output_stream: IO[str],
    log_file: IO[str],
    output_file: IO[str] | None,
    captured_output: list[str] | None,
    label: str,
) -> None:
    try:
        for line in output_stream:
            if captured_output is not None:
                captured_output.append(line)
            log_file.write(line)
            log_file.flush()
            if output_file is not None:
                output_file.write(line)
                output_file.flush()
            logger.info("[{}] {}", label, line.rstrip("\n"))
    finally:
        output_stream.close()


def _command_failure(result: CommandResult) -> CommandFailureError:
    return CommandFailureError(
        command=shlex.join(result.command),
        cwd=str(result.cwd),
        returncode=result.returncode,
        log_path=str(result.log_path),
    )
