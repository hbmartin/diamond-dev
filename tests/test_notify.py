"""Tests for best-effort notifications."""

from __future__ import annotations

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
