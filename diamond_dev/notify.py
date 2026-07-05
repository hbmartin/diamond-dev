"""Best-effort notification requests."""

from __future__ import annotations

import json
from typing import Final, Literal, get_args
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from loguru import logger

type NotificationFormat = Literal["get", "json", "slack", "discord", "ntfy"]

NOTIFICATION_FORMATS: Final = frozenset(get_args(NotificationFormat.__value__))
DEFAULT_NOTIFICATION_FORMAT: Final = "get"
_ALLOWED_NOTIFICATION_SCHEMES: Final = frozenset({"http", "https"})


def notify_url(  # noqa: PLR0913
    url: str | None,
    *,
    label: str,
    timeout: float = 10.0,
    notification_format: NotificationFormat = DEFAULT_NOTIFICATION_FORMAT,
    slug: str | None = None,
    link: str | None = None,
) -> None:
    """Send a best-effort request to a configured notification URL."""
    if url is None:
        return

    try:
        parsed_url = urlparse(url)
    except (ValueError,) as error:
        logger.opt(exception=error).warning(
            "Notification {} skipped: malformed URL {}",
            label,
            error,
        )
        return

    if parsed_url.scheme.lower() not in _ALLOWED_NOTIFICATION_SCHEMES:
        logger.warning(
            "Notification {} skipped: unsupported URL scheme {}",
            label,
            parsed_url.scheme or "<missing>",
        )
        return

    try:
        if notification_format == "get":
            response_status = _send_get(url, timeout=timeout)
        else:
            response_status = _send_post(
                url,
                timeout=timeout,
                notification_format=notification_format,
                label=label,
                slug=slug,
                link=link,
            )
        logger.info(
            "Notification {} completed with status {}",
            label,
            response_status,
        )
    # pylint: disable-next=broad-exception-caught
    except (Exception,) as error:  # noqa: BLE001
        logger.opt(exception=error).warning("Notification {} failed: {}", label, error)


def notification_message(
    *,
    label: str,
    slug: str | None,
    link: str | None,
) -> str:
    """Return the human-readable notification message body."""
    subject = f"diamond-dev {slug}" if slug else "diamond-dev"
    message = f"{subject}: {label}"
    if link:
        message = f"{message} — {link}"
    return message


def _send_get(url: str, *, timeout: float) -> int:
    with urlopen(url, timeout=timeout) as response:  # noqa: S310
        return response.status


def _send_post(  # noqa: PLR0913
    url: str,
    *,
    timeout: float,
    notification_format: NotificationFormat,
    label: str,
    slug: str | None,
    link: str | None,
) -> int:
    body, headers = _post_payload(
        notification_format=notification_format,
        label=label,
        slug=slug,
        link=link,
    )
    request = Request(  # noqa: S310
        url,
        data=body,
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.status


def _post_payload(
    *,
    notification_format: NotificationFormat,
    label: str,
    slug: str | None,
    link: str | None,
) -> tuple[bytes, dict[str, str]]:
    message = notification_message(label=label, slug=slug, link=link)
    json_headers = {"Content-Type": "application/json"}
    match notification_format:
        case "json":
            payload: dict[str, str | None] = {
                "source": "diamond-dev",
                "event": label,
                "slug": slug,
                "url": link,
                "message": message,
            }
            return json.dumps(payload, sort_keys=True).encode("utf-8"), json_headers
        case "slack":
            return json.dumps({"text": message}).encode("utf-8"), json_headers
        case "discord":
            return json.dumps({"content": message}).encode("utf-8"), json_headers
        case "ntfy":
            headers = {
                "Content-Type": "text/plain; charset=utf-8",
                "Title": f"diamond-dev {slug}" if slug else "diamond-dev",
            }
            return message.encode("utf-8"), headers
        case _:
            raise ValueError(f"Unknown notification format: {notification_format}")
