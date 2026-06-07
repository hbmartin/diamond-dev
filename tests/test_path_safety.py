"""Tests for filesystem path safety helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from diamond_dev.errors import DiamondDevError
from diamond_dev.path_safety import (
    copy_child_file,
    read_child_text,
    safe_child_path,
    safe_generated_child_path,
    write_child_text,
)


def test_safe_child_path_returns_child_under_parent(tmp_path: Path) -> None:
    assert safe_child_path(tmp_path, "artifact.md") == tmp_path / "artifact.md"


@pytest.mark.parametrize(
    "child_name",
    [
        "auth (draft).md",
        "plan#2.md",
        "feature+login.md",
        "计划.md",
    ],
)
def test_safe_child_path_allows_direct_user_filenames(
    tmp_path: Path,
    child_name: str,
) -> None:
    assert safe_child_path(tmp_path, child_name) == tmp_path / child_name


@pytest.mark.parametrize(
    "child_name",
    [
        "",
        ".",
        "..",
        "../artifact.md",
        "nested/artifact.md",
        "nested\\artifact.md",
        "-draft.md",
        "artifact\x00.md",
        "artifact\x7f.md",
        "artifact\n.md",
        "artifact?.md",
        "artifact*.md",
        "artifact.",
        "artifact ",
        "artifact .",
        "CON",
        "nul.txt",
        "COM1.md",
        "lpt9",
    ],
)
def test_safe_child_path_rejects_unsafe_names(
    tmp_path: Path,
    child_name: str,
) -> None:
    with pytest.raises(DiamondDevError, match="Unsafe child path"):
        safe_child_path(tmp_path, child_name)


def test_safe_child_path_rejects_absolute_names(tmp_path: Path) -> None:
    with pytest.raises(DiamondDevError, match="Unsafe child path"):
        safe_child_path(tmp_path, str(tmp_path / "artifact.md"))


def test_safe_child_path_rejects_windows_absolute_names(tmp_path: Path) -> None:
    with pytest.raises(DiamondDevError, match="Unsafe child path"):
        safe_child_path(tmp_path, "C:\\artifact.md")


def test_safe_child_path_rejects_symlink_escape(tmp_path: Path) -> None:
    parent_dir = tmp_path / "parent"
    outside_dir = tmp_path / "outside"
    parent_dir.mkdir()
    outside_dir.mkdir()
    try:
        (parent_dir / "escape").symlink_to(outside_dir, target_is_directory=True)
    except (OSError,) as error:
        pytest.skip(f"symlink creation failed: {error}")

    with pytest.raises(DiamondDevError, match="escapes parent directory"):
        safe_child_path(parent_dir, "escape")


def test_safe_generated_child_path_returns_child_under_parent(tmp_path: Path) -> None:
    assert safe_generated_child_path(tmp_path, "artifact.md") == tmp_path / "artifact.md"


@pytest.mark.parametrize(
    "child_name",
    [
        "auth (draft).md",
        "plan#2.md",
        "feature+login.md",
        "计划.md",
    ],
)
def test_safe_generated_child_path_rejects_non_generated_names(
    tmp_path: Path,
    child_name: str,
) -> None:
    with pytest.raises(DiamondDevError, match="Unsafe generated child path"):
        safe_generated_child_path(tmp_path, child_name)


@pytest.mark.parametrize(
    "child_name",
    [
        "-artifact.md",
        "artifact?.md",
        "artifact.",
        "artifact ",
    ],
)
def test_safe_generated_child_path_rejects_unsafe_direct_names(
    tmp_path: Path,
    child_name: str,
) -> None:
    with pytest.raises(DiamondDevError, match="Unsafe child path"):
        safe_generated_child_path(tmp_path, child_name)


def test_child_text_helpers_round_trip_validated_child(tmp_path: Path) -> None:
    written_path = write_child_text(tmp_path, "plan#2.md", "content\n")

    assert written_path == tmp_path / "plan#2.md"
    assert read_child_text(tmp_path, "plan#2.md") == "content\n"


@pytest.mark.parametrize(
    "child_name",
    [
        "",
        ".",
        "..",
        "../artifact.md",
        "nested/artifact.md",
        "nested\\artifact.md",
        "-draft.md",
        "artifact\x00.md",
        "artifact.",
        "artifact ",
    ],
)
def test_write_child_text_rejects_unsafe_names(
    tmp_path: Path,
    child_name: str,
) -> None:
    with pytest.raises(DiamondDevError, match="Unsafe child path"):
        write_child_text(tmp_path, child_name, "content\n")


def test_write_child_text_rejects_absolute_names(tmp_path: Path) -> None:
    with pytest.raises(DiamondDevError, match="Unsafe child path"):
        write_child_text(tmp_path, str(tmp_path / "artifact.md"), "content\n")


def test_write_child_text_rejects_symlink_escape(tmp_path: Path) -> None:
    parent_dir = tmp_path / "parent"
    outside_dir = tmp_path / "outside"
    parent_dir.mkdir()
    outside_dir.mkdir()
    try:
        (parent_dir / "escape").symlink_to(outside_dir / "artifact.md")
    except (OSError,) as error:
        pytest.skip(f"symlink creation failed: {error}")

    with pytest.raises(DiamondDevError, match="escapes parent directory"):
        write_child_text(parent_dir, "escape", "content\n")


def test_copy_child_file_validates_source_and_destination(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    destination_dir = tmp_path / "destination"
    source_dir.mkdir()
    destination_dir.mkdir()
    write_child_text(source_dir, "artifact.md", "content\n")

    copied_path = copy_child_file(
        source_dir=source_dir,
        source_name="artifact.md",
        destination_dir=destination_dir,
        destination_name="artifact.md",
    )

    assert copied_path == destination_dir / "artifact.md"
    assert read_child_text(destination_dir, "artifact.md") == "content\n"
