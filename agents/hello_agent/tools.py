"""Tools for the hello agent."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent_base import tool


@tool
def current_time(timezone: str = "UTC") -> str:
    """Get the current date and time in a timezone.

    Args:
        timezone: IANA timezone name, such as "Africa/Cairo" or "UTC".
    """
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown timezone '{timezone}'") from None
    return datetime.now(zone).strftime("%A %Y-%m-%d %H:%M %Z")


@tool
def word_count(text: str) -> dict[str, int]:
    """Count the words, lines and characters in a piece of text.

    Args:
        text: The text to measure.
    """
    return {
        "words": len(text.split()),
        "lines": len(text.splitlines()),
        "characters": len(text),
    }
