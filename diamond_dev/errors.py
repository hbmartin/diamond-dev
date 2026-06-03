"""Shared exceptions for Diamond Dev."""

from __future__ import annotations


class DiamondDevError(Exception):
    """Base class for user-facing workflow errors."""


class ConfigError(DiamondDevError):
    """Raised when `.diamond-dev.toml` is missing or invalid."""


class UrlDerivationError(DiamondDevError):
    """Raised when a GitHub wiki URL cannot be derived."""


class MalformedAcceptanceError(DiamondDevError):
    """Raised when a comparison file has an invalid acceptance marker."""


class CommandFailureError(DiamondDevError):
    """Raised when an external command exits unsuccessfully."""

    def __init__(
        self,
        *,
        command: str,
        cwd: str,
        returncode: int,
        log_path: str,
    ) -> None:
        """Build a command failure with execution context."""
        self.command = command
        self.cwd = cwd
        self.returncode = returncode
        self.log_path = log_path
        super().__init__(
            f"Command failed with exit code {returncode}: {command} "
            f"(cwd: {cwd}, log: {log_path})",
        )
