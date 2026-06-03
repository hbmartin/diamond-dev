"""Tests for best-effort notifications."""

from __future__ import annotations

from http.client import HTTPException
from typing import NoReturn
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

    notify.notify_url("ftp://example.test/hook", label="hook")
