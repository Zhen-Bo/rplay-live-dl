"""Notification data stays independent from Discord message presentation."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Optional


class NotificationKind(StrEnum):
    """Every supported event; declaration order is the default and preview order."""

    LIVE = "live"
    OFFLINE = "offline"
    BLOCKED = "blocked"
    AUTH_FAILED = "auth_failed"
    DOWNLOAD_FAILED = "download_failed"
    MERGE_FAILED = "merge_failed"
    MERGE_COMPLETED = "merge_completed"
    DISK_WARNING = "disk_warning"
    DISK_CRITICAL = "disk_critical"


DEFAULT_EVENTS = ",".join(NotificationKind)


@dataclass(frozen=True)
class Notification:
    kind: NotificationKind
    creator: str = ""
    title: str = ""
    started_at: str = ""
    detail: str = ""
    free_bytes: Optional[int] = None
    warning_bytes: Optional[int] = None
    critical_bytes: Optional[int] = None
    recovery_bytes: Optional[int] = None
    creator_oid: str = ""
    output_file: str = ""

    def __post_init__(self) -> None:
        # Reject unknown kinds at the source instead of at delivery time.
        object.__setattr__(self, "kind", NotificationKind(self.kind))
