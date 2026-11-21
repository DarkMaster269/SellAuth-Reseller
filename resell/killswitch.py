"""Killswitch control and state persistence for emergency store shutdown."""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resell.config import Settings

logger = logging.getLogger(__name__)

DEFAULT_KILLSWITCH_FILENAME = "resell.killswitch"


def get_killswitch_path(settings: Settings | None = None) -> Path:
    """Determine the file path representing the killswitch lock."""
    if settings is not None and getattr(settings, "SQLITE_PATH", None):
        return Path(settings.SQLITE_PATH).parent / DEFAULT_KILLSWITCH_FILENAME
    return Path(DEFAULT_KILLSWITCH_FILENAME)


def is_killswitch_active(settings: Settings | None = None) -> bool:
    """Check if emergency killswitch is currently engaged."""
    path = get_killswitch_path(settings)
    return path.is_file()


def activate_killswitch(
    settings: Settings | None = None,
    reason: str = "Manual CLI trigger",
) -> Path:
    """Engage emergency killswitch by writing persistent lock file."""
    path = get_killswitch_path(settings)
    now_utc = datetime.datetime.now(datetime.UTC).isoformat()
    content = f"ENGAGED_AT={now_utc}\nREASON={reason}\n"
    path.write_text(content, encoding="utf-8")
    logger.critical("EMERGENCY KILLSWITCH ENGAGED: %s", path)
    return path


def deactivate_killswitch(settings: Settings | None = None) -> bool:
    """Disengage emergency killswitch by unlinking lock file."""
    path = get_killswitch_path(settings)
    if path.is_file():
        path.unlink(missing_ok=True)
        logger.info("Killswitch disengaged: %s removed", path)
        return True
    return False
