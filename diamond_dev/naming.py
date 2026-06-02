"""Name and URL helpers for Diamond Dev runs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final
from urllib.parse import ParseResult, urlparse

from diamond_dev.errors import UrlDerivationError

_SLUG_PATTERN: Final = re.compile(r"[^a-z0-9]+")
_SCP_GITHUB_PATTERN: Final = re.compile(
    r"^(?P<prefix>(?:[^@]+@)?github\.com:)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
)
_SCP_REMOTE_PATTERN: Final = re.compile(
    r"^(?:[^@/\s:]+@)?(?P<host>[^@/\s:]+):(?P<path>[^\s:]+)$",
)
_GIT_URL_SCHEMES: Final = frozenset({"file", "git", "http", "https", "ssh"})


def slug_for_plan(plan_path: Path) -> str:
    """Return a stable slug from a markdown plan filename stem."""
    slug = _SLUG_PATTERN.sub("-", plan_path.stem.lower()).strip("-")
    if not slug:
        raise UrlDerivationError(f"Could not derive a slug from {plan_path.name}")
    return slug


def repository_name_from_url(repository_url: str) -> str:
    """Return the repository name from a common Git remote URL."""
    cleaned_url = repository_url.strip().rstrip("/")
    match = _SCP_GITHUB_PATTERN.match(cleaned_url)
    if match is not None:
        return _strip_git_suffix(match.group("repo"))

    parsed_url = urlparse(cleaned_url)
    path = parsed_url.path or cleaned_url
    repo_name = path.rsplit("/", maxsplit=1)[-1].rsplit(":", maxsplit=1)[-1]
    repo_name = _strip_git_suffix(repo_name)
    if not repo_name:
        raise UrlDerivationError(
            f"Could not derive repository name from {repository_url}",
        )
    return repo_name


def is_git_remote_url(repository_url: str) -> bool:
    """Return whether the value parses as a supported Git remote URL."""
    cleaned_url = repository_url.strip().rstrip("/")
    if not cleaned_url or any(character.isspace() for character in cleaned_url):
        return False

    scp_match = (
        None if "://" in cleaned_url else _SCP_REMOTE_PATTERN.match(cleaned_url)
    )
    if scp_match is not None and _has_repository_path(scp_match.group("path")):
        return True

    try:
        parsed_url = urlparse(cleaned_url)
    except (ValueError,):
        return False

    scheme = parsed_url.scheme.lower()
    if scheme not in _GIT_URL_SCHEMES:
        return False
    if scheme == "file":
        return bool(parsed_url.path.strip("/"))
    return _has_url_host(parsed_url) and _has_repository_path(parsed_url.path)


def notes_directory_name(repository_url: str) -> str:
    """Return the local notes wiki clone directory name."""
    return f"{repository_name_from_url(repository_url)}.wiki"


def derive_notes_repository_url(repository_url: str) -> str:
    """Derive the GitHub Gollum wiki remote for a GitHub repository URL."""
    cleaned_url = repository_url.strip().rstrip("/")
    match = _SCP_GITHUB_PATTERN.match(cleaned_url)
    if match is not None:
        repo_name = _strip_git_suffix(match.group("repo"))
        return f"{match.group('prefix')}{match.group('owner')}/{repo_name}.wiki.git"

    parsed_url = urlparse(cleaned_url)
    host = parsed_url.hostname
    path_parts = [part for part in parsed_url.path.split("/") if part]
    if host != "github.com" or len(path_parts) != 2:
        raise UrlDerivationError(
            f"Could not derive a GitHub wiki URL from {repository_url}",
        )

    owner = path_parts[0]
    repo_name = _strip_git_suffix(path_parts[1])
    if not owner or not repo_name:
        raise UrlDerivationError(
            f"Could not derive a GitHub wiki URL from {repository_url}",
        )

    return f"{parsed_url.scheme}://{parsed_url.netloc}/{owner}/{repo_name}.wiki.git"


def _strip_git_suffix(repo_name: str) -> str:
    if repo_name.endswith(".git"):
        return repo_name[:-4]
    return repo_name


def _has_repository_path(path: str) -> bool:
    path_parts = [part for part in path.rstrip("/").split("/") if part]
    if not path_parts:
        return False
    return len(path_parts) > 1 or path_parts[-1].endswith(".git")


def _has_url_host(parsed_url: ParseResult) -> bool:
    try:
        _ = parsed_url.port
    except (ValueError,):
        return False
    return parsed_url.hostname is not None
