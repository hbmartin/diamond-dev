"""Commit-ish resolution for two-commit comparison workflows."""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from diamond_dev.errors import DiamondDevError
from diamond_dev.naming import slugify
from diamond_dev.workflow import CommitMetadata

if TYPE_CHECKING:
    from diamond_dev.executor import CommandExecutor

ORIGIN_REMOTE_PREFIX: Final = "origin/"


@dataclass(frozen=True, slots=True)
class ResolvedCommitInput(CommitMetadata):
    """Commit metadata resolved before final workflow clone names are known."""

    explicit_branch: str | None
    source: str


def resolve_commit_pair_inputs(
    *,
    cwd: Path,
    repository_url: str,
    runner: CommandExecutor,
    commit_args: tuple[str, str],
) -> tuple[ResolvedCommitInput, ResolvedCommitInput]:
    """Resolve two commit-ish arguments through remote then trusted local fallback."""
    with tempfile.TemporaryDirectory(
        prefix=".diamond-dev-resolver-",
        dir=cwd,
    ) as temp_dir:
        resolver_dir = Path(temp_dir) / "repo"
        runner.run(
            ("git", "clone", repository_url, str(resolver_dir)),
            cwd=cwd,
            log_name="commit-resolver-clone",
        )
        resolved = tuple(
            _resolve_one_input(
                cwd=cwd,
                resolver_dir=resolver_dir,
                repository_url=repository_url,
                runner=runner,
                commit_arg=commit_arg,
                index=index,
            )
            for index, commit_arg in enumerate(commit_args, start=1)
        )

    left, right = resolved
    if left.sha == right.sha:
        raise DiamondDevError(
            "Commit comparison requires two distinct commits; both arguments "
            f"resolved to {left.sha}",
        )
    return left, right


def _resolve_one_input(  # noqa: PLR0913
    *,
    cwd: Path,
    resolver_dir: Path,
    repository_url: str,
    runner: CommandExecutor,
    commit_arg: str,
    index: int,
) -> ResolvedCommitInput:
    if resolved := _resolve_in_resolver(
        runner=runner,
        resolver_dir=resolver_dir,
        commit_arg=commit_arg,
        index=index,
    ):
        return resolved

    if not _cwd_origin_matches(
        cwd=cwd,
        repository_url=repository_url,
        runner=runner,
    ):
        raise DiamondDevError(
            "Commit not reachable from configured remote and invocation "
            f"repository origin does not match `repository_url`: {commit_arg}",
        )

    if resolved := _resolve_from_local_fallback(
        cwd=cwd,
        resolver_dir=resolver_dir,
        runner=runner,
        commit_arg=commit_arg,
        index=index,
    ):
        return resolved
    raise DiamondDevError(
        "Commit not reachable from configured remote or trusted local fallback: "
        f"{commit_arg}",
    )


def _resolve_in_resolver(
    *,
    runner: CommandExecutor,
    resolver_dir: Path,
    commit_arg: str,
    index: int,
) -> ResolvedCommitInput | None:
    sha = _resolve_commitish(
        runner=runner,
        repo_dir=resolver_dir,
        commit_arg=commit_arg,
        log_prefix=f"commit-{index}-remote",
    )
    if sha is None:
        return None
    return _commit_metadata(
        runner=runner,
        repo_dir=resolver_dir,
        commit_arg=commit_arg,
        sha=sha,
        source="remote",
        log_prefix=f"commit-{index}-remote",
        local_ref_names=(),
    )


def _resolve_from_local_fallback(
    *,
    cwd: Path,
    resolver_dir: Path,
    runner: CommandExecutor,
    commit_arg: str,
    index: int,
) -> ResolvedCommitInput | None:
    fetch_result = runner.run(
        ("git", "fetch", str(cwd), commit_arg),
        cwd=resolver_dir,
        log_name=f"commit-{index}-local-fetch",
        check=False,
    )
    if fetch_result.returncode != 0:
        return None
    sha = _resolve_ref(
        runner=runner,
        repo_dir=resolver_dir,
        ref="FETCH_HEAD",
        log_name=f"commit-{index}-local-fetch-head",
    )
    if sha is None:
        return None
    local_ref_names = _local_ref_names(
        cwd=cwd,
        runner=runner,
        sha=sha,
        log_prefix=f"commit-{index}-local",
    )
    return _commit_metadata(
        runner=runner,
        repo_dir=resolver_dir,
        commit_arg=commit_arg,
        sha=sha,
        source="local",
        log_prefix=f"commit-{index}-local",
        local_ref_names=local_ref_names,
    )


def _commit_metadata(  # noqa: PLR0913
    *,
    runner: CommandExecutor,
    repo_dir: Path,
    commit_arg: str,
    sha: str,
    source: str,
    log_prefix: str,
    local_ref_names: tuple[str, ...],
) -> ResolvedCommitInput:
    return ResolvedCommitInput(
        original_arg=commit_arg,
        sha=sha,
        short_sha=_short_sha(runner, repo_dir, sha, log_name=f"{log_prefix}-short-sha"),
        message=_commit_message(
            runner,
            repo_dir,
            sha,
            log_name=f"{log_prefix}-message",
        ),
        ref_names=tuple(
            dict.fromkeys(
                (
                    *_containing_ref_names(
                        runner,
                        repo_dir,
                        sha,
                        log_name=f"{log_prefix}-containing-refs",
                    ),
                    *local_ref_names,
                ),
            ),
        ),
        explicit_branch=_explicit_branch_for_arg(
            runner=runner,
            repo_dir=repo_dir,
            commit_arg=commit_arg,
            log_prefix=log_prefix,
        ),
        source=source,
    )


def _resolve_commitish(
    *,
    runner: CommandExecutor,
    repo_dir: Path,
    commit_arg: str,
    log_prefix: str,
) -> str | None:
    for ref in _candidate_refs(commit_arg):
        if sha := _resolve_ref(
            runner=runner,
            repo_dir=repo_dir,
            ref=ref,
            log_name=f"{log_prefix}-resolve-{slugify(ref) or 'ref'}",
        ):
            return sha
    return None


def _resolve_ref(
    *,
    runner: CommandExecutor,
    repo_dir: Path,
    ref: str,
    log_name: str,
) -> str | None:
    result = runner.run(
        ("git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"),
        cwd=repo_dir,
        log_name=log_name,
        check=False,
    )
    if result.returncode == 0:
        lines = result.output.strip().splitlines()
        return lines[-1] if lines else None
    return None


def _candidate_refs(commit_arg: str) -> tuple[str, ...]:
    candidates = [commit_arg]
    if not commit_arg.startswith((ORIGIN_REMOTE_PREFIX, "refs/")):
        candidates.append(f"{ORIGIN_REMOTE_PREFIX}{commit_arg}")
    return tuple(dict.fromkeys(candidates))


def _short_sha(
    runner: CommandExecutor,
    repo_dir: Path,
    sha: str,
    *,
    log_name: str,
) -> str:
    result = runner.run(
        ("git", "rev-parse", "--short=12", sha),
        cwd=repo_dir,
        log_name=log_name,
    )
    lines = result.output.strip().splitlines()
    if not lines:
        raise DiamondDevError(f"Failed to get short SHA for {sha}")
    return lines[-1]


def _commit_message(
    runner: CommandExecutor,
    repo_dir: Path,
    sha: str,
    *,
    log_name: str,
) -> str:
    result = runner.run(
        ("git", "log", "-1", "--format=%B", sha),
        cwd=repo_dir,
        log_name=log_name,
    )
    return result.output.strip()


def _containing_ref_names(
    runner: CommandExecutor,
    repo_dir: Path,
    sha: str,
    *,
    log_name: str,
) -> tuple[str, ...]:
    result = runner.run(
        ("git", "branch", "-a", "--contains", sha, "--format=%(refname:short)"),
        cwd=repo_dir,
        log_name=log_name,
        check=False,
    )
    if result.returncode != 0:
        return ()
    return _normalize_branch_names(result.output.splitlines())


def _local_ref_names(
    *,
    cwd: Path,
    runner: CommandExecutor,
    sha: str,
    log_prefix: str,
) -> tuple[str, ...]:
    result = runner.run(
        ("git", "branch", "--contains", sha, "--format=%(refname:short)"),
        cwd=cwd,
        log_name=f"{log_prefix}-containing-local-refs",
        check=False,
    )
    if result.returncode != 0:
        return ()
    return _normalize_branch_names(result.output.splitlines())


def _normalize_branch_names(ref_names: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for ref_name in ref_names:
        clean_ref = ref_name.strip().lstrip("*").strip()
        clean_ref = clean_ref.removeprefix("remotes/")
        if clean_ref == f"{ORIGIN_REMOTE_PREFIX}HEAD" or " -> " in clean_ref:
            continue
        clean_ref = clean_ref.removeprefix(ORIGIN_REMOTE_PREFIX)
        if clean_ref and clean_ref not in normalized:
            normalized.append(clean_ref)
    return tuple(normalized)


def _explicit_branch_for_arg(
    *,
    runner: CommandExecutor,
    repo_dir: Path,
    commit_arg: str,
    log_prefix: str,
) -> str | None:
    normalized_arg = _normalize_explicit_branch_name(commit_arg)
    if _branch_ref_exists(
        runner=runner,
        repo_dir=repo_dir,
        branch=normalized_arg,
        log_name=f"{log_prefix}-explicit-branch-{slugify(normalized_arg) or 'arg'}",
    ):
        return normalized_arg
    return None


def _normalize_explicit_branch_name(commit_arg: str) -> str:
    return (
        commit_arg.removeprefix("refs/heads/")
        .removeprefix("refs/remotes/origin/")
        .removeprefix(ORIGIN_REMOTE_PREFIX)
    )


def _branch_ref_exists(
    *,
    runner: CommandExecutor,
    repo_dir: Path,
    branch: str,
    log_name: str,
) -> bool:
    for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        result = runner.run(
            ("git", "show-ref", "--verify", "--quiet", ref),
            cwd=repo_dir,
            log_name=f"{log_name}-{slugify(ref) or 'ref'}",
            check=False,
        )
        if result.returncode == 0:
            return True
    return False


def _cwd_origin_matches(
    *,
    cwd: Path,
    repository_url: str,
    runner: CommandExecutor,
) -> bool:
    if not shutil.which("git"):
        return False
    result = runner.run(
        ("git", "remote", "get-url", "origin"),
        cwd=cwd,
        log_name="commit-local-origin-url",
        check=False,
    )
    return result.returncode == 0 and _normalized_repository_url(
        result.output,
    ) == _normalized_repository_url(repository_url)


def _normalized_repository_url(url: str) -> str:
    clean_url = url.strip().rstrip("/")
    if not clean_url:
        return ""

    if "://" in clean_url:
        parsed = urlsplit(clean_url)
        if parsed.scheme == "file":
            return _strip_repository_url_suffix(parsed.path)

        host = (parsed.hostname or "").lower()
        if not host:
            return _strip_repository_url_suffix(clean_url)

        port = _normalized_url_port(parsed.scheme, parsed.port)
        path = parsed.path.strip("/")
        return _strip_repository_url_suffix(f"{host}{port}/{path}")

    if not re.match(r"^[a-zA-Z]:[\\/]", clean_url) and (
        scp_match := re.match(
            r"^(?:[^/@:]+@)?(?P<host>[^/:]+):(?P<path>.+)$",
            clean_url,
        )
    ):
        host = scp_match.group("host").lower()
        path = scp_match.group("path")
        return _strip_repository_url_suffix(f"{host}/{path.strip('/')}")

    return _strip_repository_url_suffix(clean_url)


def _normalized_url_port(scheme: str, port: int | None) -> str:
    if port is None:
        return ""
    default_ports = {
        "git": 9418,
        "http": 80,
        "https": 443,
        "ssh": 22,
    }
    if default_ports.get(scheme) == port:
        return ""
    return f":{port}"


def _strip_repository_url_suffix(url: str) -> str:
    return url.rstrip("/").removesuffix(".git")
