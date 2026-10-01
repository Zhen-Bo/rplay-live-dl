"""Disk alerts and conservative preflight for merges (never delete recordings)."""

import logging
import math
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from core.constants import (
    DEFAULT_DISK_CRITICAL_GB,
    DEFAULT_DISK_RECOVERY_MARGIN_GB,
    DEFAULT_DISK_REMINDER_SECONDS,
    DEFAULT_DISK_WARNING_GB,
    DEFAULT_MERGE_MIN_FREE_DISK_GB,
    DEFAULT_MERGE_SPACE_MULTIPLIER,
)

GIB = 1024**3


class InsufficientMergeSpaceError(OSError):
    """The merge was skipped before FFmpeg ran; every raw input is untouched."""


def existing_parent(path: Path) -> Path:
    return next(candidate for candidate in (path, *path.parents) if candidate.exists())


@dataclass(frozen=True)
class DiskAlert:
    level: str
    free_bytes: int
    path: Path


class DiskSpaceMonitor:
    """Emit transitions and bounded reminders, with hysteresis on recovery."""

    def __init__(
        self,
        warning_gb: float = DEFAULT_DISK_WARNING_GB,
        critical_gb: float = DEFAULT_DISK_CRITICAL_GB,
        recovery_margin_gb: float = DEFAULT_DISK_RECOVERY_MARGIN_GB,
        reminder_seconds: int = DEFAULT_DISK_REMINDER_SECONDS,
    ) -> None:
        self.warning = int(warning_gb * GIB)
        self.critical = int(critical_gb * GIB)
        self.margin = int(recovery_margin_gb * GIB)
        self.reminder_seconds = reminder_seconds
        self.level = "ok"
        self.last_sent = 0.0
        self._read_failed = False

    def check(self, path: Path, logger: logging.Logger) -> Optional[DiskAlert]:
        try:
            free = shutil.disk_usage(existing_parent(path)).free
        except OSError:
            if not self._read_failed:
                logger.warning(
                    "Could not check archive free space; disk alerts unavailable"
                )
            self._read_failed = True
            return None
        self._read_failed = False
        now = time.monotonic()
        if free < self.critical:
            level = "critical"
        elif self.level == "critical" and free < self.critical + self.margin:
            level = "critical"
        elif free < self.warning:
            level = "warning"
        elif self.level != "ok" and free < self.warning + self.margin:
            level = "warning"
        else:
            level = "ok"
        changed = level != self.level
        self.level = level
        if not changed and (
            level == "ok" or now - self.last_sent < self.reminder_seconds
        ):
            return None
        self.last_sent = now
        label = "recovered" if level == "ok" else level
        logger.log(
            logging.INFO if level == "ok" else logging.WARNING,
            "Disk space %s: %.2f GiB free at %s (warning %.2f, critical %.2f GiB)",
            label,
            free / GIB,
            path,
            self.warning / GIB,
            self.critical / GIB,
        )
        return DiskAlert(label, free, path)


def ensure_merge_space(
    ts_files: List[Path],
    output_path: Path,
    reserve_gb: float = DEFAULT_MERGE_MIN_FREE_DISK_GB,
    multiplier: float = DEFAULT_MERGE_SPACE_MULTIPLIER,
) -> None:
    """Budget the FFmpeg temp output; this is an estimate, not a reservation."""
    input_bytes = sum(path.stat().st_size for path in ts_files)
    required = math.ceil(input_bytes * multiplier + reserve_gb * GIB)
    free = shutil.disk_usage(existing_parent(output_path.parent)).free
    if free < required:
        raise InsufficientMergeSpaceError(
            f"Insufficient merge space: {free / GIB:.2f} GiB free, "
            f"estimated requirement {required / GIB:.2f} GiB "
            f"({multiplier:g} x input + {reserve_gb:g} GiB reserve); "
            "free space and restart to retry"
        )
