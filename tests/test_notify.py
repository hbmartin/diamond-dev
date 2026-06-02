"""Tests for best-effort notifications."""

from __future__ import annotations

from urllib.error import URLError

from diamond_dev import notify


def test_notify_url_ignores_missing_url() -> None:
    notify.notify_url(None, label="missing")


def test_notify_url_ignores_request_failure(monkeypatch) -> None:
    def fail_urlopen(url, timeout):
        raise URLError("offline")

    monkeypatch.setattr(notify, "urlopen", fail_urlopen)

    notify.notify_url("https://example.test/hook", label="hook")
