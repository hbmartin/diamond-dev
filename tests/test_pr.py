"""Tests for pull request helpers."""

from __future__ import annotations

import pytest

from diamond_dev.errors import DiamondDevError
from diamond_dev.pr import parse_existing_pull_request


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
