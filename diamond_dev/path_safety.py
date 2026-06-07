"""Filesystem path helpers for workflow artifacts."""

from __future__ import annotations

import re
import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final

from diamond_dev.errors import DiamondDevError

_SAFE_GENERATED_CHILD_NAME_PATTERN: Final = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9._ -]*[A-Za-z0-9_-])?",
)
_ASCII_CONTROL_CHARACTER_LIMIT: Final = 32
_UNSAFE_DIRECT_CHILD_NAMES: Final[frozenset[str]] = frozenset((".", ".."))
_WINDOWS_RESERVED_CHILD_NAME_CHARACTERS: Final[frozenset[str]] = frozenset(
    '<>:"|?*',
)
_WINDOWS_RESERVED_DEVICE_NAMES: Final[frozenset[str]] = frozenset(
    (
        "AUX",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "CON",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
        "NUL",
        "PRN",
    ),
)


def safe_child_path(directory: Path, child_name: str) -> Path:
    """Return a resolved child path that cannot escape its parent directory."""
    return _resolved_child_path(directory, child_name, unsafe_name=child_name)


def safe_generated_child_path(directory: Path, child_name: str) -> Path:
    """Return a resolved child path for an internally generated artifact name."""
    valid_child_name = _validated_generated_child_name(child_name)
    return _resolved_child_path(directory, valid_child_name, unsafe_name=child_name)


def _resolved_child_path(
    directory: Path,
    child_name: str,
    *,
    unsafe_name: str,
) -> Path:
    valid_child_name = _validated_direct_child_name(child_name)
    resolved_directory = directory.resolve(strict=False)
    resolved_child = resolved_directory.joinpath(valid_child_name).resolve(  # NOSONAR
        strict=False,
    )
    try:
        resolved_child.relative_to(resolved_directory)
    except (ValueError,) as error:
        raise DiamondDevError(
            f"Child path escapes parent directory: {unsafe_name!r}",
        ) from error
    return resolved_child


def _validated_direct_child_name(child_name: str) -> str:
    """Return a direct child name safe for filesystem joins."""
    if _is_unsafe_direct_child_name(child_name):
        raise DiamondDevError(f"Unsafe child path: {child_name!r}")
    if _has_path_root_or_separator(child_name):
        raise DiamondDevError(f"Unsafe child path: {child_name!r}")
    return child_name


def _validated_generated_child_name(child_name: str) -> str:
    """Return a generated child name that matches Diamond Dev artifact policy."""
    _validated_direct_child_name(child_name)
    if _SAFE_GENERATED_CHILD_NAME_PATTERN.fullmatch(child_name) is None:
        raise DiamondDevError(f"Unsafe generated child path: {child_name!r}")
    return child_name


def _has_path_root_or_separator(child_name: str) -> bool:
    child_path = Path(child_name)
    posix_path = PurePosixPath(child_name)
    windows_path = PureWindowsPath(child_name)
    return (
        child_path.is_absolute()
        or child_path.name != child_name
        or posix_path.is_absolute()
        or posix_path.name != child_name
        or windows_path.is_absolute()
        or windows_path.drive != ""
        or windows_path.root != ""
        or windows_path.name != child_name
    )


def _is_unsafe_direct_child_name(child_name: str) -> bool:
    return (
        not child_name
        or child_name in _UNSAFE_DIRECT_CHILD_NAMES
        or child_name.startswith("-")
        or child_name.endswith((" ", "."))
        or any(
            ord(character) < _ASCII_CONTROL_CHARACTER_LIMIT
            or character in _WINDOWS_RESERVED_CHILD_NAME_CHARACTERS
            for character in child_name
        )
        or _is_windows_reserved_device_name(child_name)
    )


def _is_windows_reserved_device_name(child_name: str) -> bool:
    device_name = child_name.split(".", maxsplit=1)[0]
    return device_name.upper() in _WINDOWS_RESERVED_DEVICE_NAMES


def read_child_text(directory: Path, child_name: str) -> str:
    """Read a traversal-safe direct child text file."""
    return safe_child_path(directory, child_name).read_text(encoding="utf-8")


def write_child_text(directory: Path, child_name: str, text: str) -> Path:
    """Write a traversal-safe direct child text file and return its resolved path."""
    child_path = safe_child_path(directory, child_name)
    child_path.write_text(text, encoding="utf-8")
    return child_path


def copy_child_file(
    *,
    source_dir: Path,
    source_name: str,
    destination_dir: Path,
    destination_name: str,
) -> Path:
    """Copy one traversal-safe direct child file to another direct child path."""
    source_path = safe_child_path(source_dir, source_name)
    destination_path = safe_child_path(destination_dir, destination_name)
    shutil.copy2(source_path, destination_path)
    return destination_path


def read_generated_child_text(directory: Path, child_name: str) -> str:
    """Read an internally generated child text file."""
    return safe_generated_child_path(directory, child_name).read_text(encoding="utf-8")


def write_generated_child_text(directory: Path, child_name: str, text: str) -> Path:
    """Write an internally generated child text file and return its resolved path."""
    child_path = safe_generated_child_path(directory, child_name)
    child_path.write_text(text, encoding="utf-8")
    return child_path


def copy_generated_child_file(
    *,
    source_dir: Path,
    source_name: str,
    destination_dir: Path,
    destination_name: str,
) -> Path:
    """Copy one internally generated child file to another generated child path."""
    source_path = safe_generated_child_path(source_dir, source_name)
    destination_path = safe_generated_child_path(destination_dir, destination_name)
    shutil.copy2(source_path, destination_path)
    return destination_path
