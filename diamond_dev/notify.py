"""Best-effort notification requests."""

from __future__ import annotations

from typing import Final
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from loguru import logger

_ALLOWED_NOTIFICATION_SCHEMES: Final = frozenset({"http", "https"})


def notify_url(url: str | None, *, label: str, timeout: float = 10.0) -> None:
    """Send a best-effort GET request to a configured notification URL."""
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
        with urlopen(url, timeout=timeout) as response:  # noqa: S310
            logger.info(
                "Notification {} completed with status {}",
                label,
                response.status,
            )
    except (OSError, TimeoutError, URLError, ValueError) as error:
        logger.opt(exception=error).warning("Notification {} failed: {}", label, error)
