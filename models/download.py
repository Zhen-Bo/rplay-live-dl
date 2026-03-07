"""Session-aware download and monitor event models."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class SessionState(str, Enum):
    """Lifecycle states for a monitored download session."""

    RAW_RUNNING = "raw_running"
    BLOCKED = "blocked"
    MERGE_QUEUED = "merge_queued"
    MERGING = "merging"
    DONE = "done"
    MERGE_FAILED = "merge_failed"


@dataclass
class DownloadSession:
    """Tracked state for one live stream session."""

    session_key: str
    creator_oid: str
    creator_name: str
    title: str
    stream_start_time: datetime
    state: SessionState
    staging_dir: Path
    final_output_path: Optional[Path] = None
    last_error: Optional[str] = None


@dataclass(frozen=True)
class RawDownloadCompleted:
    """Event emitted when a raw yt-dlp session finishes successfully."""

    session_key: str
    staging_dir: Path


@dataclass(frozen=True)
class RawDownloadBlocked:
    """Event emitted when raw download fails due to blocked stream access."""

    session_key: str
    error_message: str


@dataclass(frozen=True)
class RawDownloadAuthFailed:
    """Event emitted when raw download fails due to invalid credentials."""

    session_key: str
    error_message: str


@dataclass(frozen=True)
class RawDownloadFailed:
    """Event emitted when a raw download fails for a retryable non-blocked reason."""

    session_key: str
    error_message: str


@dataclass(frozen=True)
class MergeJobSpec:
    """Immutable merge job inputs for one completed raw download session."""

    session_key: str
    creator_name: str
    title: str
    stream_start_time: datetime
    staging_dir: Path


@dataclass(frozen=True)
class MergeStarted:
    """Event emitted when merge work begins for a session."""

    session_key: str


@dataclass(frozen=True)
class MergeCompleted:
    """Event emitted when a session merge finishes successfully."""

    session_key: str
    output_path: Path


@dataclass(frozen=True)
class MergeFailed:
    """Event emitted when a session merge fails."""

    session_key: str
    error_message: str
    failed_staging_dir: Path
