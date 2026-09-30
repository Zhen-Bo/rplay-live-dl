"""Disk alerts and conservative preflight for merges (never delete recordings)."""

import logging
import math
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

GIB = 1024**3


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
        warning_gb: float = 30,
        critical_gb: float = 10,
        recovery_margin_gb: float = 2,
        reminder_seconds: int = 3600,
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
    reserve_gb: float = 1,
    multiplier: float = 2.2,
) -> None:
    """Budget temp output plus copy fallback; this is an estimate, not a reservation."""
    input_bytes = sum(path.stat().st_size for path in ts_files)
    required = math.ceil(input_bytes * multiplier + reserve_gb * GIB)
    free = shutil.disk_usage(existing_parent(output_path.parent)).free
    if free < required:
        raise OSError(
            f"Insufficient merge space: {free / GIB:.2f} GiB free, "
            f"estimated requirement {required / GIB:.2f} GiB "
            f"({multiplier:g} x input + {reserve_gb:g} GiB reserve); "
            "raw recordings retained. Free space and restart to retry recovery."
        )
