"""Tests for CI workflow policy."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ACTION_USE_PATTERN = re.compile(r"^\s*uses:\s+(?P<reference>[^#\s]+)")
FULL_SHA_REFERENCE_PATTERN = re.compile(r".+@[0-9a-f]{40}$")
REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
CI_WORKFLOW = WORKFLOW_DIR / "ci.yml"


def _workflow_action_references() -> list[tuple[Path, int, str]]:
    references: list[tuple[Path, int, str]] = []
    for workflow_path in sorted(WORKFLOW_DIR.glob("*.yml")):
        lines = workflow_path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, start=1):
            if match := ACTION_USE_PATTERN.match(line):
                references.append((workflow_path, line_number, match["reference"]))
    return references


def _is_remote_action_reference(reference: str) -> bool:
    return (
        "/" in reference
        and not reference.startswith(("./", "../"))
        and not reference.startswith("docker://")
    )


def _load_ci_workflow() -> dict[str, object]:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _required_mapping(mapping: dict[str, object], key: str) -> dict[str, object]:
    value = mapping[key]
    assert isinstance(value, dict)
    return value


def _job_permissions(job: object) -> dict[str, object]:
    assert isinstance(job, dict)
    permissions = job.get("permissions", {})
    assert isinstance(permissions, dict)
    return permissions


def _assert_no_write_permissions(permissions: dict[str, object]) -> None:
    assert all(value != "write" for value in permissions.values())


def test_github_actions_are_pinned_to_full_commit_shas() -> None:
    mutable_references = [
        f"{path.relative_to(REPO_ROOT)}:{line_number}: {reference}"
        for path, line_number, reference in _workflow_action_references()
        if _is_remote_action_reference(reference)
        and not FULL_SHA_REFERENCE_PATTERN.fullmatch(reference)
    ]

    assert mutable_references == []


def test_ci_workflow_default_permissions_are_read_only() -> None:
    workflow = _load_ci_workflow()
    permissions = _required_mapping(workflow, "permissions")

    assert permissions["contents"] == "read"
    _assert_no_write_permissions(permissions)


def test_ci_lint_job_permissions_are_read_only() -> None:
    workflow = _load_ci_workflow()
    jobs = _required_mapping(workflow, "jobs")
    lint_job = _required_mapping(jobs, "lint-type-test")
    permissions = _required_mapping(lint_job, "permissions")

    assert permissions == {"contents": "read"}


def test_ci_pr_write_permission_is_isolated_to_pylint_comment_job() -> None:
    workflow = _load_ci_workflow()
    jobs = _required_mapping(workflow, "jobs")
    comment_job = _required_mapping(jobs, "pylint-pr-comment")
    comment_permissions = _required_mapping(comment_job, "permissions")

    assert "lint-type-test" in jobs
    assert comment_permissions["contents"] == "read"
    assert comment_permissions["pull-requests"] == "write"
    assert [
        scope
        for scope, access in comment_permissions.items()
        if scope != "pull-requests" and access == "write"
    ] == []
    assert [
        job_name
        for job_name, job in jobs.items()
        if job_name != "pylint-pr-comment"
        and _job_permissions(job).get("pull-requests") == "write"
    ] == []


def test_ci_pylint_pr_comment_uses_body_file() -> None:
    workflow_text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert 'gh pr comment "${PR_NUMBER}" --body-file pylint-pr-comment.md' in (
        workflow_text
    )
