"""Markdown file helpers."""

from __future__ import annotations

from pathlib import Path


def read_normalized_markdown(path: Path) -> str:
    """Read markdown text with platform-specific line endings normalized."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
