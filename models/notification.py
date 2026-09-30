"""Notification data stays independent from Discord message presentation."""

from dataclasses import dataclass

EVENT_KINDS = frozenset(
    {
        "live",
        "blocked",
        "auth_failed",
        "download_failed",
        "merge_failed",
        "disk_warning",
        "disk_critical",
        "disk_recovered",
    }
)
DEFAULT_EVENTS = "live,blocked,auth_failed,download_failed,merge_failed,disk_warning,disk_critical,disk_recovered"


@dataclass(frozen=True)
class Notification:
    kind: str
    creator: str = ""
    title: str = ""
    started_at: str = ""
    detail: str = ""
