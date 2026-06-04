"""Tests for structured review judgment sidecars."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from diamond_dev.errors import DiamondDevError
from diamond_dev.review_judgments import (
    ReviewFinding,
    ReviewJudgments,
    canonical_review_judgments_json,
    canonical_review_judgments_payload,
    parse_review_judgments,
    read_review_judgments_status,
    summarize_review_judgments,
    upsert_structured_judgments_section,
)


def test_parse_review_judgments_accepts_valid_payload() -> None:
    judgments = parse_review_judgments(
        {
            "schema_version": 1,
            "review_file": "my-plan-review.md",
            "review_provider": "coderabbit",
            "review_judge": "codex",
            "findings": [
                {
                    "id": "CR-1",
                    "decision": "fix",
                    "confidence": 0.8,
                    "rationale": "The finding is valid.",
                },
                {
                    "id": "CR-2",
                    "decision": "needs_input",
                    "confidence": 1,
                    "rationale": "Product choice required.",
                },
            ],
        },
    )

    assert judgments.review_file == "my-plan-review.md"
    assert judgments.review_provider == "coderabbit"
    assert judgments.review_judge == "codex"
    assert judgments.findings == (
        ReviewFinding(
            id="CR-1",
            decision="fix",
            confidence=0.8,
            rationale="The finding is valid.",
        ),
        ReviewFinding(
            id="CR-2",
            decision="needs_input",
            confidence=1.0,
            rationale="Product choice required.",
        ),
    )


@pytest.mark.parametrize(
    "payload, message",
    [
        ([], "must be a JSON object"),
        ({"schema_version": 2}, "schema_version"),
        (
            {
                "schema_version": 1,
                "review_file": "review.md",
                "review_provider": "coderabbit",
                "review_judge": "codex",
                "findings": [{"id": "CR-1", "decision": "accept"}],
            },
            "invalid decision",
        ),
        (
            {
                "schema_version": 1,
                "review_file": "review.md",
                "review_provider": "coderabbit",
                "review_judge": "codex",
                "findings": [
                    {
                        "id": "CR-1",
                        "decision": "fix",
                        "confidence": 1.2,
                        "rationale": "Too sure.",
                    },
                ],
            },
            "confidence",
        ),
    ],
)
def test_parse_review_judgments_rejects_invalid_payloads(
    payload: object,
    message: str,
) -> None:
    with pytest.raises(DiamondDevError, match=message):
        parse_review_judgments(payload)


def test_read_review_judgments_status_is_warn_only_metadata(tmp_path: Path) -> None:
    missing = read_review_judgments_status(tmp_path / "missing.json")
    assert missing.status == "missing"
    assert missing.judgments is None

    sidecar = tmp_path / "judgments.json"
    sidecar.write_text("not json", encoding="utf-8")

    invalid = read_review_judgments_status(sidecar)
    assert invalid.status == "invalid"
    assert invalid.error is not None


def test_canonical_review_judgments_json_is_deterministic() -> None:
    judgments = ReviewJudgments(
        review_file="review.md",
        review_provider="coderabbit",
        review_judge="codex",
        findings=(
            ReviewFinding(
                id="CR-1",
                decision="decline",
                confidence=0.25,
                rationale="Not actionable.",
            ),
        ),
    )

    canonical_text = canonical_review_judgments_json(judgments)

    assert canonical_text.endswith("\n")
    assert json.loads(canonical_text) == canonical_review_judgments_payload(judgments)
    assert canonical_text == canonical_review_judgments_json(judgments)


def test_upsert_structured_judgments_section_inserts_and_replaces() -> None:
    first = ReviewJudgments(
        review_file="review.md",
        review_provider="coderabbit",
        review_judge="codex",
        findings=(
            ReviewFinding(
                id="CR-1",
                decision="fix",
                confidence=0.75,
                rationale="Use `x | y` without breaking the table.",
            ),
        ),
    )
    second = ReviewJudgments(
        review_file="review.md",
        review_provider="coderabbit",
        review_judge="codex",
        findings=(
            ReviewFinding(
                id="CR-2",
                decision="needs_input",
                confidence=0.5,
                rationale="Ask the user.",
            ),
        ),
    )

    markdown = upsert_structured_judgments_section("# Review\n\nExisting text.", first)
    replaced = upsert_structured_judgments_section(markdown, second)

    assert "## Structured review judgments" in markdown
    assert "CR-1" in markdown
    assert "Use `x \\| y`" in markdown
    assert "CR-1" not in replaced
    assert "CR-2" in replaced
    assert replaced.count("## Structured review judgments") == 1


def test_summarize_review_judgments_counts_decisions() -> None:
    summary = summarize_review_judgments(
        ReviewJudgments(
            review_file="review.md",
            review_provider="coderabbit",
            review_judge="codex",
            findings=(
                ReviewFinding(
                    id="CR-1",
                    decision="fix",
                    confidence=0.9,
                    rationale="Valid.",
                ),
                ReviewFinding(
                    id="CR-2",
                    decision="decline",
                    confidence=0.4,
                    rationale="False positive.",
                ),
                ReviewFinding(
                    id="CR-3",
                    decision="needs_input",
                    confidence=0.6,
                    rationale="Ambiguous.",
                ),
            ),
        ),
    )

    assert summary.fix == 1
    assert summary.decline == 1
    assert summary.needs_input == 1
    assert summary.needs_input_ids == ("CR-3",)
