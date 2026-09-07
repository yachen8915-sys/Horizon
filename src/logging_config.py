"""Shared logging configuration for Horizon entry points."""

import logging

from rich.console import Console
from rich.logging import RichHandler


def configure_logging(console: Console, level: int | str = logging.WARNING) -> None:
    """Route application logging through the entry point's Rich console."""
    # HTTPX INFO request logs contain complete URLs, including API query keys.
    # Keep application INFO/DEBUG while disabling these transport request logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
        force=True,
    )
