"""Keep docs/diamond-dev.schema.json in sync with the config dataclasses."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from diamond_dev.config import (
    AcceptanceConfig,
    AgentConfig,
    ComparisonConfig,
    NotificationConfig,
    PromptConfig,
    WorkflowConfig,
)

SCHEMA_PATH = Path(__file__).parent.parent / "docs" / "diamond-dev.schema.json"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _section_properties(section: str) -> set[str]:
    return set(_schema()["properties"][section]["properties"])


def _field_names(dataclass_type: type) -> set[str]:
    return {field.name for field in fields(dataclass_type)}


def test_schema_top_level_matches_loader_expectations() -> None:
    schema = _schema()
    assert schema["required"] == ["repository_url"]
    top_level = set(schema["properties"])
    assert {
        "repository_url",
        "wiki_repository_url",
        "notifications",
        "prompts",
        "workflow",
        "comparison",
        "acceptance",
        "agents",
    } <= top_level


def test_schema_sections_match_config_dataclasses() -> None:
    for section, dataclass_type in (
        ("notifications", NotificationConfig),
        ("prompts", PromptConfig),
        ("workflow", WorkflowConfig),
        ("comparison", ComparisonConfig),
        ("acceptance", AcceptanceConfig),
    ):
        assert _section_properties(section) == _field_names(dataclass_type), (
            f"schema section `{section}` is out of sync with "
            f"{dataclass_type.__name__}"
        )


def test_schema_agent_entries_match_agent_config() -> None:
    schema = _schema()
    agent_properties = next(
        iter(schema["properties"]["agents"]["patternProperties"].values()),
    )
    assert set(agent_properties["properties"]) == _field_names(AgentConfig)


def test_schema_notification_formats_match_notify_module() -> None:
    from diamond_dev.notify import NOTIFICATION_FORMATS

    schema_formats = set(
        _schema()["properties"]["notifications"]["properties"]["format"]["enum"],
    )
    assert schema_formats == set(NOTIFICATION_FORMATS)
