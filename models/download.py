"""Session-aware download models."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class SessionState(str, Enum):
    """Lifecycle states for a monitored download session."""

    RAW_RUNNING = "raw_running"
    RAW_FAILED = "raw_failed"
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


@dataclass
class DownloadResult:
    """Completion payload emitted by the raw downloader."""

    session_key: str
    staging_dir: Path
    base_output_stem: str
    creator_name: str
    title: str
    stream_start_time: datetime

