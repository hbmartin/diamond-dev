"""Tests for best-effort notifications."""

from __future__ import annotations

import json
from http.client import HTTPException
from typing import NoReturn, Self
from urllib.error import URLError

from diamond_dev import notify


def test_notify_url_ignores_missing_url() -> None:
    notify.notify_url(None, label="missing")


def test_notify_url_ignores_request_failure(monkeypatch) -> None:
    def fail_urlopen(url: str, timeout: float) -> NoReturn:
        del url, timeout
        raise URLError("offline")

    monkeypatch.setattr(notify, "urlopen", fail_urlopen)

    notify.notify_url("https://example.test/hook", label="hook")


def test_notify_url_ignores_malformed_request_url(monkeypatch) -> None:
    def fail_urlopen(url: str, timeout: float) -> NoReturn:
        del url, timeout
        raise ValueError("bad URL")

    monkeypatch.setattr(notify, "urlopen", fail_urlopen)

    notify.notify_url("https://example.test/hook", label="hook")


def test_notify_url_ignores_malformed_parse_url(monkeypatch) -> None:
    def fail_urlparse(url: str) -> NoReturn:
        del url
        raise ValueError("bad URL")

    def unexpected_urlopen(url: str, timeout: float) -> NoReturn:
        del url, timeout
        raise AssertionError("urlopen should not be called")

    monkeypatch.setattr(notify, "urlparse", fail_urlparse)
    monkeypatch.setattr(notify, "urlopen", unexpected_urlopen)

    notify.notify_url("https://example.test/hook", label="hook")


def test_notify_url_ignores_unexpected_http_failure(monkeypatch) -> None:
    def fail_urlopen(url: str, timeout: float) -> NoReturn:
        del url, timeout
        raise HTTPException("bad status")

    monkeypatch.setattr(notify, "urlopen", fail_urlopen)

    notify.notify_url("https://example.test/hook", label="hook")


def test_notify_url_skips_unsupported_scheme(monkeypatch) -> None:
    def unexpected_urlopen(url: str, timeout: float) -> NoReturn:
        del url, timeout
        raise AssertionError("urlopen should not be called")

    monkeypatch.setattr(notify, "urlopen", unexpected_urlopen)

    notify.notify_url("mailto:user@example.test", label="hook")


def test_notify_url_logs_successful_response(monkeypatch) -> None:
    calls: list[tuple[str, float]] = []

    class Response:
        status = 204

        def __enter__(self) -> Self:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            del exc_type, exc, traceback

    def successful_urlopen(url: str, timeout: float) -> Response:
        calls.append((url, timeout))
        return Response()

    monkeypatch.setattr(notify, "urlopen", successful_urlopen)

    notify.notify_url("https://example.test/hook", label="hook", timeout=2.5)

    assert calls == [("https://example.test/hook", 2.5)]


class _FakeResponse:
    status = 204

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        return False


def _capture_posts(monkeypatch) -> list:
    requests: list = []

    def fake_urlopen(request, timeout: float) -> _FakeResponse:
        del timeout
        requests.append(request)
        return _FakeResponse()

    monkeypatch.setattr(notify, "urlopen", fake_urlopen)
    return requests


def test_notify_url_posts_json_payload(monkeypatch) -> None:
    requests = _capture_posts(monkeypatch)

    notify.notify_url(
        "https://example.test/hook",
        label="comparison",
        notification_format="json",
        slug="my-plan",
        link="https://github.com/owner/repo/wiki/my-plan-comparison",
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["source"] == "diamond-dev"
    assert payload["event"] == "comparison"
    assert payload["slug"] == "my-plan"
    assert payload["url"] == "https://github.com/owner/repo/wiki/my-plan-comparison"


def test_notify_url_posts_slack_and_discord_text(monkeypatch) -> None:
    requests = _capture_posts(monkeypatch)

    notify.notify_url(
        "https://hooks.slack.test/x",
        label="open pr",
        notification_format="slack",
        slug="my-plan",
        link="https://github.com/owner/repo/pull/1",
    )
    notify.notify_url(
        "https://discord.test/webhook",
        label="open pr",
        notification_format="discord",
        slug="my-plan",
    )

    slack_payload = json.loads(requests[0].data.decode("utf-8"))
    discord_payload = json.loads(requests[1].data.decode("utf-8"))
    assert slack_payload == {
        "text": "diamond-dev my-plan: open pr — https://github.com/owner/repo/pull/1",
    }
    assert discord_payload == {"content": "diamond-dev my-plan: open pr"}


def test_notify_url_posts_ntfy_plain_text(monkeypatch) -> None:
    requests = _capture_posts(monkeypatch)

    notify.notify_url(
        "https://ntfy.test/diamond",
        label="review input needed",
        notification_format="ntfy",
        slug="my-plan",
    )

    request = requests[0]
    assert request.get_header("Title") == "diamond-dev my-plan"
    assert request.data == b"diamond-dev my-plan: review input needed"


def test_notify_url_get_format_does_not_post(monkeypatch) -> None:
    calls: list = []

    def fake_urlopen(url, timeout: float) -> _FakeResponse:
        del timeout
        calls.append(url)
        return _FakeResponse()

    monkeypatch.setattr(notify, "urlopen", fake_urlopen)

    notify.notify_url("https://example.test/hook", label="hook")

    assert calls == ["https://example.test/hook"]
