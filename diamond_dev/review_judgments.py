"""Structured review judgment sidecar helpers."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from diamond_dev.errors import DiamondDevError

SCHEMA_VERSION = 1
SECTION_START = "<!-- diamond-dev structured-review-judgments:start -->"
SECTION_END = "<!-- diamond-dev structured-review-judgments:end -->"

type ReviewDecision = Literal["fix", "decline", "needs_input"]
type ReviewJudgmentStatusName = Literal["missing", "valid", "invalid"]

_DECISIONS: frozenset[ReviewDecision] = frozenset(
    {"fix", "decline", "needs_input"},
)


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    """One structured review finding judgment."""

    id: str
    decision: ReviewDecision
    confidence: float
    rationale: str


@dataclass(frozen=True, slots=True)
class ReviewJudgments:
    """Validated structured review judgment sidecar."""

    review_file: str
    review_provider: str
    review_judge: str
    findings: tuple[ReviewFinding, ...]


@dataclass(frozen=True, slots=True)
class ReviewJudgmentStatus:
    """Status from reading a structured review judgment sidecar."""

    path: Path
    status: ReviewJudgmentStatusName
    judgments: ReviewJudgments | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewJudgmentSummary:
    """Compact summary of structured review judgments."""

    fix: int
    decline: int
    needs_input: int
    needs_input_ids: tuple[str, ...]


def read_review_judgments_status(path: Path) -> ReviewJudgmentStatus:
    """Read and validate a review judgment sidecar."""
    if not path.is_file():
        return ReviewJudgmentStatus(path=path, status="missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        judgments = parse_review_judgments(payload)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        DiamondDevError,
    ) as error:
        return ReviewJudgmentStatus(path=path, status="invalid", error=str(error))
    return ReviewJudgmentStatus(path=path, status="valid", judgments=judgments)


def parse_review_judgments(payload: object) -> ReviewJudgments:
    """Validate a decoded review judgment payload."""
    if not isinstance(payload, dict):
        raise DiamondDevError("Review judgment sidecar must be a JSON object")
    payload_dict = cast("dict[str, object]", payload)
    schema_version = payload_dict.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise DiamondDevError(
            f"Review judgment sidecar schema_version must be {SCHEMA_VERSION}",
        )

    return ReviewJudgments(
        review_file=_required_string(payload_dict, "review_file"),
        review_provider=_required_string(payload_dict, "review_provider"),
        review_judge=_required_string(payload_dict, "review_judge"),
        findings=_findings(payload_dict.get("findings")),
    )


def canonical_review_judgments_json(judgments: ReviewJudgments) -> str:
    """Return deterministic JSON text for validated judgments."""
    return f"{json.dumps(_payload(judgments), indent=2, sort_keys=True)}\n"


def canonical_review_judgments_payload(judgments: ReviewJudgments) -> dict[str, Any]:
    """Return a deterministic dictionary for equality checks."""
    return _payload(judgments)


def upsert_structured_judgments_section(
    markdown: str,
    judgments: ReviewJudgments,
) -> str:
    """Insert or replace the structured review judgment markdown section."""
    section = render_structured_judgments_section(judgments)
    if SECTION_START in markdown and SECTION_END in markdown:
        before, _start, rest = markdown.partition(SECTION_START)
        _old_section, _end, after = rest.partition(SECTION_END)
        return f"{before.rstrip()}\n\n{section}\n{after.lstrip()}"
    return f"{markdown.rstrip()}\n\n{section}\n"


def render_structured_judgments_section(judgments: ReviewJudgments) -> str:
    """Render structured judgments into deterministic markdown."""
    summary = summarize_review_judgments(judgments)
    lines = [
        SECTION_START,
        "## Structured review judgments",
        "",
        f"- Review provider: {judgments.review_provider}",
        f"- Review judge: {judgments.review_judge}",
        (
            "- Decisions: "
            f"fix={summary.fix}, decline={summary.decline}, "
            f"needs_input={summary.needs_input}"
        ),
    ]
    if summary.needs_input_ids:
        lines.append(f"- Needs input IDs: {', '.join(summary.needs_input_ids)}")
    lines.extend(
        (
            "",
            "| ID | Decision | Confidence | Rationale |",
            "| --- | --- | ---: | --- |",
        ),
    )
    lines.extend(
        (
            "| "
            f"{_markdown_cell(finding.id)} | "
            f"{finding.decision} | "
            f"{finding.confidence:.2f} | "
            f"{_markdown_cell(finding.rationale)} |"
        )
        for finding in judgments.findings
    )
    lines.append(SECTION_END)
    return "\n".join(lines)


def summarize_review_judgments(judgments: ReviewJudgments) -> ReviewJudgmentSummary:
    """Return compact decision counts and input-needed IDs."""
    counts = Counter(finding.decision for finding in judgments.findings)
    return ReviewJudgmentSummary(
        fix=counts["fix"],
        decline=counts["decline"],
        needs_input=counts["needs_input"],
        needs_input_ids=tuple(
            finding.id
            for finding in judgments.findings
            if finding.decision == "needs_input"
        ),
    )


def _payload(judgments: ReviewJudgments) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "review_file": judgments.review_file,
        "review_provider": judgments.review_provider,
        "review_judge": judgments.review_judge,
        "findings": [
            {
                "id": finding.id,
                "decision": finding.decision,
                "confidence": finding.confidence,
                "rationale": finding.rationale,
            }
            for finding in judgments.findings
        ],
    }


def _findings(value: object) -> tuple[ReviewFinding, ...]:
    if not isinstance(value, list):
        raise DiamondDevError("Review judgment sidecar `findings` must be an array")
    return tuple(_finding(item, index) for index, item in enumerate(value))


def _finding(value: object, index: int) -> ReviewFinding:
    if not isinstance(value, dict):
        raise DiamondDevError(
            f"Review judgment sidecar finding {index} must be an object",
        )
    value_dict = cast("dict[str, object]", value)
    decision = _required_string(value_dict, "decision")
    if decision not in _DECISIONS:
        raise DiamondDevError(
            f"Review judgment sidecar finding {index} has invalid decision",
        )
    review_decision = cast("ReviewDecision", decision)
    confidence = value_dict.get("confidence")
    if (
        not isinstance(confidence, int | float)
        or isinstance(confidence, bool)
        or confidence < 0
        or confidence > 1
    ):
        raise DiamondDevError(
            f"Review judgment sidecar finding {index} confidence must be 0..1",
        )
    return ReviewFinding(
        id=_required_string(value_dict, "id"),
        decision=review_decision,
        confidence=float(confidence),
        rationale=_required_string(value_dict, "rationale"),
    )


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise DiamondDevError(f"Review judgment sidecar `{key}` must be a string")


def _markdown_cell(value: str) -> str:
    return value.replace("\n", " ").replace("|", "\\|")
