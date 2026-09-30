"""Notification data stays independent from Discord message presentation."""

from dataclasses import dataclass

EVENT_KINDS = frozenset(
    {
        "live",
        "offline",
        "blocked",
        "auth_failed",
        "download_failed",
        "merge_failed",
        "merge_completed",
        "disk_warning",
        "disk_critical",
    }
)
DEFAULT_EVENTS = "live,offline,blocked,auth_failed,download_failed,merge_failed,merge_completed,disk_warning,disk_critical"


@dataclass(frozen=True)
class Notification:
    kind: str
    creator: str = ""
    title: str = ""
    started_at: str = ""
    detail: str = ""
    free_bytes: int | None = None
    warning_bytes: int | None = None
    critical_bytes: int | None = None
    recovery_bytes: int | None = None
    creator_oid: str = ""
    output_file: str = ""
