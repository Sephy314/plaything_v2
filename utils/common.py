"""Project metadata — version, feature constants, etc."""

from __future__ import annotations

__version__ = "0.1.0"


def clean_text(text: str) -> str:
    """Strip surrounding whitespace while preserving inner content.

    Args:
        text: The input string.

    Returns:
        The trimmed string.
    """
    return text.strip()
