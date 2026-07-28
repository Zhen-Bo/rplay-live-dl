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

from core.constants import DEFAULT_INTERVAL

__all__ = [
    "HEARTBEAT_FILE",
    "touch_heartbeat",
    "main",
]

HEARTBEAT_FILE = "/tmp/rplay-live-dl-heartbeat"


def touch_heartbeat() -> None:
    """Create or update the heartbeat file mtime."""
    Path(HEARTBEAT_FILE).touch()


def main() -> int:
    """CLI entry: exit 0 if healthy, non-zero with a one-line reason otherwise."""
    # ponytail: raw getenv only; wrong value skews the threshold, not app config
    raw = os.getenv("INTERVAL", str(DEFAULT_INTERVAL))
    try:
        interval = int(raw)
    except (TypeError, ValueError):
        interval = DEFAULT_INTERVAL
    if interval <= 0:
        interval = DEFAULT_INTERVAL
    max_age = 3 * interval

    path = Path(HEARTBEAT_FILE)
    if not path.exists():
        print(f"heartbeat missing: {path}", file=sys.stderr)
        return 1

    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        print(f"heartbeat unreadable: {exc}", file=sys.stderr)
        return 1

    age = time.time() - mtime
    if age < 0:
        print(f"heartbeat clock skew: mtime in the future by {-age:.0f}s", file=sys.stderr)
        return 1
    if age >= max_age:
        print(f"heartbeat stale: age={age:.0f}s max={max_age}s", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
