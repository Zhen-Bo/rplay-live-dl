"""Session-aware download and monitor event models."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class SessionState(str, Enum):
    RAW_RUNNING = "raw_running"
    BLOCKED = "blocked"
    MERGE_QUEUED = "merge_queued"
    MERGING = "merging"
    DONE = "done"
    MERGE_FAILED = "merge_failed"


@dataclass
class DownloadSession:
    session_key: str
    creator_oid: str
    creator_name: str
    title: str
    stream_start_time: datetime
    state: SessionState
    output_dir: Path
    session_prefix: str
    recording_started_at: Optional[datetime] = None
    stream_oid: Optional[str] = None


@dataclass(frozen=True)
class RawDownloadCompleted:
    session_key: str
    output_dir: Path


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
    session_key: str
    creator_name: str
    title: str
    stream_start_time: datetime
    output_dir: Path
    session_prefix: str
    creator_oid: Optional[str] = None
    stream_oid: Optional[str] = None
    recording_started_at: Optional[datetime] = None


@dataclass(frozen=True)
class MergeStarted:
    session_key: str


@dataclass(frozen=True)
class MergeCompleted:
    session_key: str
    output_path: Path


@dataclass(frozen=True)
class MergeFailed:
    session_key: str
    error_message: str
    insufficient_space: bool = False
