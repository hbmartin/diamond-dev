"""Best-effort notification requests."""

from __future__ import annotations

from urllib.error import URLError
from urllib.request import urlopen

from loguru import logger


def notify_url(url: str | None, *, label: str, timeout: float = 10.0) -> None:
    """Send a best-effort GET request to a configured notification URL."""
    if url is None:
        return

    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310
            logger.info(
                "Notification {} completed with status {}",
                label,
                response.status,
            )
    except (OSError, TimeoutError, URLError) as error:
        logger.warning("Notification {} failed: {}", label, error)
