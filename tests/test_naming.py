"""Tests for slug and wiki URL helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from diamond_dev.errors import UrlDerivationError
from diamond_dev.naming import (
    derive_notes_repository_url,
    is_git_remote_url,
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
def test_slug_for_plan(filename: str, expected: str) -> None:
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
def test_repository_name_from_url(repository_url: str, expected: str) -> None:
    assert repository_name_from_url(repository_url) == expected


@pytest.mark.parametrize(
    "repository_url",
    [
        "git@github.com:owner/repo.git",
        "git@example.com:owner/repo.git",
        "https://github.com/owner/repo",
        "ssh://git@github.com/owner/repo.git",
        "git://example.com/owner/repo.git",
        "file:///tmp/repo.git",
        "file:///repo",
        "file://localhost/repo",
    ],
)
def test_is_git_remote_url_accepts_git_url_formats(repository_url: str) -> None:
    assert is_git_remote_url(repository_url)


@pytest.mark.parametrize(
    "repository_url",
    [
        "",
        "owner/repo",
        "https://",
        "https://github.com",
        "https://github.com/owner/repo name",
        "git@github.com",
        "git@github.com:owner",
        "ftp://github.com/owner/repo.git",
        "file://",
        "file://localhost",
    ],
)
def test_is_git_remote_url_rejects_malformed_urls(repository_url: str) -> None:
    assert not is_git_remote_url(repository_url)


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
def test_derive_notes_repository_url(repository_url: str, expected: str) -> None:
    assert derive_notes_repository_url(repository_url) == expected


def test_derive_notes_repository_url_rejects_non_github_remote() -> None:
    with pytest.raises(UrlDerivationError):
        derive_notes_repository_url("git@example.com:owner/repo.git")


def test_derive_notes_repository_url_rejects_extra_github_path() -> None:
    with pytest.raises(UrlDerivationError):
        derive_notes_repository_url("https://github.com/owner/repo/issues")
