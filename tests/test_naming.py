"""Tests for slug and wiki URL helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from diamond_dev.errors import UrlDerivationError
from diamond_dev.naming import (
    derive_notes_repository_url,
    notes_directory_name,
    repository_name_from_url,
    slug_for_plan,
)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("My Plan.md", "my-plan"),
        ("release_v2.plan.md", "release-v2-plan"),
        ("Example 2026!.md", "example-2026"),
    ],
)
def test_slug_for_plan(filename, expected) -> None:
    assert slug_for_plan(Path(filename)) == expected


def test_slug_for_plan_rejects_symbol_only_stem() -> None:
    with pytest.raises(UrlDerivationError):
        slug_for_plan(Path("###.md"))


@pytest.mark.parametrize(
    ("repository_url", "expected"),
    [
        ("git@github.com:owner/repo.git", "repo"),
        ("https://github.com/owner/repo", "repo"),
        ("ssh://git@github.com/owner/repo.git", "repo"),
    ],
)
def test_repository_name_from_url(repository_url, expected) -> None:
    assert repository_name_from_url(repository_url) == expected


def test_notes_directory_name_uses_repository_name() -> None:
    assert notes_directory_name("git@github.com:owner/repo.git") == "repo.wiki"


@pytest.mark.parametrize(
    ("repository_url", "expected"),
    [
        ("git@github.com:owner/repo.git", "git@github.com:owner/repo.wiki.git"),
        (
            "https://github.com/owner/repo.git",
            "https://github.com/owner/repo.wiki.git",
        ),
        (
            "ssh://git@github.com/owner/repo.git",
            "ssh://git@github.com/owner/repo.wiki.git",
        ),
    ],
)
def test_derive_notes_repository_url(repository_url, expected) -> None:
    assert derive_notes_repository_url(repository_url) == expected


def test_derive_notes_repository_url_rejects_non_github_remote() -> None:
    with pytest.raises(UrlDerivationError):
        derive_notes_repository_url("git@example.com:owner/repo.git")
