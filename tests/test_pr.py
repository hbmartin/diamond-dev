"""Tests for pull request helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from diamond_dev.config import DiamondDevConfig
from diamond_dev.errors import DiamondDevError
from diamond_dev.pr import build_pr_body, parse_existing_pull_request
from diamond_dev.workflow import (
    ImplementationBranch,
    ImplementationContext,
    PlanContext,
    RunContext,
    SelectedImplementation,
    WikiContext,
)


def test_parse_existing_pull_request_returns_none_for_empty_list() -> None:
    assert parse_existing_pull_request("[]") is None


def test_parse_existing_pull_request_reads_first_item() -> None:
    existing_pr = parse_existing_pull_request(
        (
            '[{"number": 12, "state": "OPEN", '
            '"url": "https://github.com/o/r/pull/12"}]'
        ),
    )

    assert existing_pr is not None
    assert existing_pr.number == 12
    assert existing_pr.state == "OPEN"
    assert existing_pr.url == "https://github.com/o/r/pull/12"


def test_parse_existing_pull_request_rejects_bad_json() -> None:
    with pytest.raises(DiamondDevError, match="Could not parse PR list JSON"):
        parse_existing_pull_request("not json")


def test_parse_existing_pull_request_rejects_non_array() -> None:
    with pytest.raises(DiamondDevError, match="Expected PR list JSON array"):
        parse_existing_pull_request("{}")


def test_parse_existing_pull_request_rejects_non_object_item() -> None:
    with pytest.raises(DiamondDevError, match="Expected PR list item object"):
        parse_existing_pull_request("[1]")


def test_parse_existing_pull_request_rejects_missing_fields() -> None:
    with pytest.raises(DiamondDevError, match="missing number, state, or url"):
        parse_existing_pull_request('[{"number": 12, "state": "OPEN"}]')


def test_build_pr_body_includes_structured_review_summary(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.wiki.directory.mkdir()
    context.wiki.review_judgments_file.write_text(
        (
            "{\n"
            '  "schema_version": 1,\n'
            '  "review_file": "my-plan-review.md",\n'
            '  "review_provider": "coderabbit",\n'
            '  "review_judge": "codex",\n'
            '  "findings": [\n'
            "    {\n"
            '      "id": "CR-1",\n'
            '      "decision": "fix",\n'
            '      "confidence": 0.8,\n'
            '      "rationale": "Fix this."\n'
            "    },\n"
            "    {\n"
            '      "id": "CR-2",\n'
            '      "decision": "needs_input",\n'
            '      "confidence": 0.6,\n'
            '      "rationale": "Ask first."\n'
            "    }\n"
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )
    selected = SelectedImplementation(
        accepted_agent="codex",
        comparison_fixer="claude",
        branch="codex/my-plan",
        repo_dir=tmp_path / "codex-my-plan",
    )

    body = build_pr_body(context, selected)

    assert "Structured review judgments:" in body
    assert "- Decisions: fix=1, decline=0, needs_input=1" in body
    assert "- Sidecar: my-plan-review-judgments.json" in body
    assert "- Needs input IDs: CR-2" in body
    assert "Fix this." not in body


def _context(tmp_path: Path) -> RunContext:
    return RunContext(
        cwd=tmp_path,
        config=DiamondDevConfig(
            config_path=tmp_path / ".diamond-dev.toml",
            repository_url="git@github.com:owner/repo.git",
        ),
        plan=PlanContext(
            path=tmp_path / "My Plan.md",
            slug="my-plan",
        ),
        wiki=WikiContext(
            url="git@github.com:owner/repo.wiki.git",
            directory=tmp_path / "repo.wiki",
            comparison_file=tmp_path / "repo.wiki" / "my-plan-comparison.md",
            comparison_bundle_file=tmp_path
            / "repo.wiki"
            / "my-plan-comparison-bundle.md",
            review_file=tmp_path / "repo.wiki" / "my-plan-review.md",
            review_judgments_file=tmp_path
            / "repo.wiki"
            / "my-plan-review-judgments.json",
        ),
        implementation=ImplementationContext(
            branches=(
                ImplementationBranch(
                    agent_name="codex",
                    repo_dir=tmp_path / "codex-my-plan",
                    branch="codex/my-plan",
                    log_prefix="codex",
                ),
            ),
            base_branch="main",
        ),
    )
