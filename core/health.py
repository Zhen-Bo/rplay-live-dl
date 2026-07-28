"""
Heartbeat health probe for Docker HEALTHCHECK.

The monitor touches a heartbeat file once per poll cycle. This module checks
that the file exists and its mtime is within 3 × INTERVAL seconds.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from core.constants import (
    DEFAULT_INTERVAL,
    HEARTBEAT_FILE_PATH,
    HEARTBEAT_STALE_MULTIPLIER,
)

__all__ = [
    "touch_heartbeat",
    "check_heartbeat",
    "main",
]


def touch_heartbeat(path: Optional[str] = None) -> None:
    """Create or update the heartbeat file mtime."""
    Path(path if path is not None else HEARTBEAT_FILE_PATH).touch()


def _interval_seconds() -> int:
    # ponytail: raw getenv only; wrong value skews the threshold, not app config
    raw = os.getenv("INTERVAL", str(DEFAULT_INTERVAL))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL
    return value if value > 0 else DEFAULT_INTERVAL


def _max_age_seconds() -> int:
    return HEARTBEAT_STALE_MULTIPLIER * _interval_seconds()


def check_heartbeat(
    path: Optional[str] = None,
    *,
    now: Optional[float] = None,
) -> Tuple[bool, str]:
    """
    Return (healthy, reason). reason is empty when healthy.

    Args:
        path: Heartbeat file path (defaults to HEARTBEAT_FILE_PATH)
        now: Optional epoch seconds for deterministic tests
    """
    heartbeat_path = Path(path if path is not None else HEARTBEAT_FILE_PATH)
    if not heartbeat_path.exists():
        return False, f"heartbeat missing: {heartbeat_path}"

    try:
        mtime = heartbeat_path.stat().st_mtime
    except OSError as exc:
        return False, f"heartbeat unreadable: {exc}"

    age = (time.time() if now is None else now) - mtime
    max_age = _max_age_seconds()
    if age > max_age:
        return False, f"heartbeat stale: age={age:.0f}s max={max_age}s"
    return True, ""


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry: exit 0 if healthy, non-zero with a one-line reason otherwise."""
    del argv  # reserved for future flags; probe takes no args today
    ok, reason = check_heartbeat()
    if ok:
        return 0
    print(reason, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
