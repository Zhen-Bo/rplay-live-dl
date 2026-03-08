"""
Stream downloader module.

Provides functionality to download live streams using yt-dlp,
with support for concurrent downloads and automatic file management.
"""

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yt_dlp
from pathvalidate import sanitize_filename
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from core.constants import (
    DEFAULT_DOWNLOAD_RETRIES,
    DEFAULT_DOWNLOAD_SOCKET_TIMEOUT,
    DEFAULT_DOWNLOAD_TASK_RETRY_BACKOFF_FACTOR,
    DEFAULT_FRAGMENT_RETRIES,
    DEFAULT_HTTP_HEADERS,
    DEFAULT_MAX_RETRIES,
)
from core.logger import setup_logger
from core.utils import format_file_size
from models.download import (
    RawDownloadAuthFailed,
    RawDownloadCompleted,
    RawDownloadFailed,
)

__all__ = [
    "StreamDownloader",
]


class _RetryableDownloadTaskError(Exception):
    """Internal exception used to retry a full yt-dlp task."""

    pass


def _read_bool_env(var_name: str, default: bool = False) -> bool:
    """Parse a boolean environment flag with common truthy values."""
    value = os.getenv(var_name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class _YtDlpLoggerBridge:
    """Route optional yt-dlp internal logs through the downloader logger."""

    def __init__(self, downloader: "StreamDownloader", enabled: bool = False) -> None:
        self._downloader = downloader
        self._enabled = enabled

    def _emit(self, message: Any) -> None:
        if not self._enabled:
            return
        normalized = str(message).strip()
        if not normalized:
            return
        self._downloader._log("debug", f"yt-dlp: {normalized}")

    def debug(self, message: Any) -> None:
        self._emit(message)

    def info(self, message: Any) -> None:
        self._emit(message)

    def warning(self, message: Any) -> None:
        self._emit(message)

    def error(self, message: Any) -> None:
        self._emit(message)


class StreamDownloader:
    """
    Handles downloading of live streams using yt-dlp.

    This class manages downloading streams for a specific creator, including:
    - Setting up logging for download operations
    - Managing download threads
    - Handling file paths and naming
    - Executing the actual download process

    Attributes:
        creator_name: Name of the content creator
        logger: Logger instance for this downloader
        download_thread: Reference to the active download thread
    """

    # Default archive directory
    ARCHIVE_DIR = "archive"

    # yt-dlp configuration
    DEFAULT_FORMAT = "bestvideo+bestaudio/best"

    # Maximum number of duplicate files before raising an error
    MAX_DUPLICATE_FILES = 1000

    # Error message patterns indicating non-retriable access failure.
    ACCESS_ERROR_PATTERNS = [
        "HTTP Error 403",
        "HTTP Error 404",
    ]
    RETRYABLE_ACCESS_ERROR_PATTERNS = ["HTTP Error 404"]
    AUTH_ERROR_PATTERNS = ["HTTP Error 401"]
    DOWNLOAD_TASK_RETRY_ATTEMPTS = DEFAULT_MAX_RETRIES
    DOWNLOAD_TASK_RETRY_BACKOFF_FACTOR = DEFAULT_DOWNLOAD_TASK_RETRY_BACKOFF_FACTOR

    def __init__(
        self,
        creator_name: str,
        on_download_error: Optional[Callable[[str], None]] = None,
        on_download_auth_error: Optional[Callable[[Any], None]] = None,
        session_key: Optional[str] = None,
        output_dir: Optional[Path] = None,
        output_extension: str = ".mp4",
        on_download_complete: Optional[Callable[[RawDownloadCompleted], None]] = None,
        on_download_failure: Optional[Callable[[RawDownloadFailed], None]] = None,
    ) -> None:
        """
        Initialize a new stream downloader for a creator.

        Args:
            creator_name: Name of the content creator
            on_download_error: Optional callback invoked with error message
                when download fails due to M3U8 access issues (e.g., paid content).
                Called from the download thread.
        """
        self.creator_name = creator_name
        self._log_prefix = f"[{creator_name}]"
        self.logger = setup_logger("Downloader")
        self.download_thread: Optional[threading.Thread] = None
        self._current_output_path: Optional[Path] = None
        self._download_start_time: Optional[datetime] = None
        self._on_download_error = on_download_error
        self._on_download_auth_error = on_download_auth_error
        self.session_key = session_key
        self.output_dir = output_dir
        self.output_extension = output_extension
        self._on_download_complete = on_download_complete
        self._on_download_failure = on_download_failure
        self._yt_dlp_logger = _YtDlpLoggerBridge(
            self,
            enabled=_read_bool_env("LOG_YTDLP_INTERNAL", default=False),
        )

    def _log(self, level: str, message: str) -> None:
        """Log a message with creator name prefix."""
        full_message = f"{self._log_prefix} {message}"
        getattr(self.logger, level)(full_message)

    def download(self, stream_url: str, live_title: str) -> None:
        """
        Initiate a new download operation in a separate thread.

        Args:
            stream_url: URL of the stream m3u8 to download
            live_title: Title of the live stream for the output filename
        """
        # Sanitize filename using pathvalidate
        safe_title = sanitize_filename(live_title, replacement_text="_")
        if not safe_title:
            safe_title = "untitled"

        # Construct output path
        output_path = self._build_output_path(safe_title)

        # Ensure unique file path and create directories
        output_path = self._get_unique_path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Store current download info
        self._current_output_path = output_path
        self._download_start_time = datetime.now()

        # Configure yt-dlp options
        ydl_opts = self._build_ydl_options(output_path)

        self._log("info", f"📥 Starting download: \"{safe_title}\"")
        self._log("info", f"   Output: {output_path.name}")
        self._log(
            "debug",
            f"   Context: session_key={self.session_key or 'none'}, output_path={output_path}",
        )

        # Start download in a separate thread
        self.download_thread = threading.Thread(
            target=self._download_worker,
            args=(stream_url, ydl_opts, output_path),
            name=f"download-{self.creator_name}",
            daemon=True,
        )
        self.download_thread.start()

    def is_alive(self) -> bool:
        """
        Check if the current download operation is still active.

        Returns:
            True if download is in progress, False otherwise
        """
        return self.download_thread is not None and self.download_thread.is_alive()

    def _build_output_path(self, safe_title: str) -> Path:
        """
        Construct the output file path.

        Args:
            safe_title: Sanitized stream title

        Returns:
            Path object for the output file
        """
        date_str = datetime.today().strftime("%Y-%m-%d")
        filename = f"#{self.creator_name} {date_str} {safe_title}{self.output_extension}"

        if self.output_dir is not None:
            return self.output_dir / filename

        return Path.cwd() / self.ARCHIVE_DIR / self.creator_name / filename

    def _build_ydl_options(self, output_path: Path) -> Dict[str, Any]:
        """
        Build yt-dlp options dictionary.

        Args:
            output_path: Path for the output file

        Returns:
            Dictionary of yt-dlp options
        """
        options = {
            "format": self.DEFAULT_FORMAT,
            "outtmpl": str(output_path),
            "http_headers": DEFAULT_HTTP_HEADERS.copy(),
            "logger": self._yt_dlp_logger,
            "quiet": True,
            "no_progress": True,
            "no_warnings": True,
            # Retry settings for reliability
            "retries": DEFAULT_DOWNLOAD_RETRIES,
            "fragment_retries": DEFAULT_FRAGMENT_RETRIES,
            "socket_timeout": DEFAULT_DOWNLOAD_SOCKET_TIMEOUT,
            # Continue partial downloads
            "continuedl": True,
        }

        if self.output_extension == ".mp4":
            options["merge_output_format"] = "mp4"

        return options

    @classmethod
    def _get_unique_path(cls, base_path: Path) -> Path:
        """
        Generate a unique file path by appending a counter if file exists.

        Args:
            base_path: Initial desired file path

        Returns:
            Unique file path that doesn't exist

        Raises:
            RuntimeError: If more than MAX_DUPLICATE_FILES duplicates exist
        """
        if not base_path.exists():
            return base_path

        directory = base_path.parent
        stem = base_path.stem
        suffix = base_path.suffix
        counter = 1

        while True:
            new_path = directory / f"{stem}_{counter}{suffix}"
            if not new_path.exists():
                return new_path
            counter += 1
            # Safety limit to prevent infinite loop
            if counter > cls.MAX_DUPLICATE_FILES:
                raise RuntimeError(f"Too many duplicate files for {stem}")

    @staticmethod
    def _has_sibling_fragment_outputs(output_path: Path) -> bool:
        """Return True when yt-dlp left numbered sibling fragments for this output."""
        fragment_pattern = f"{output_path.stem}_*{output_path.suffix}"
        return any(output_path.parent.glob(fragment_pattern))

    def _build_output_state_details(self, output_path: Path) -> str:
        """Build a compact output-state summary for downloader logs."""
        part_path = Path(f"{output_path}.part")
        output_exists = output_path.exists()
        part_exists = part_path.exists()
        sibling_fragments = self._has_sibling_fragment_outputs(output_path)
        output_size = (
            format_file_size(output_path.stat().st_size) if output_exists else "0 B"
        )
        part_size = format_file_size(part_path.stat().st_size) if part_exists else "0 B"
        return (
            f"output_path={output_path}, "
            f"output_exists={output_exists}, output_size={output_size}, "
            f"part_path={part_path}, part_exists={part_exists}, part_size={part_size}, "
            f"sibling_fragments={sibling_fragments}"
        )

    def _download_worker(
        self,
        stream_url: str,
        ydl_opts: Dict[str, Any],
        output_path: Path,
    ) -> None:
        """
        Worker function that performs the actual download operation.

        This runs in a separate thread to avoid blocking the main thread.

        Args:
            stream_url: URL of the stream to download
            ydl_opts: Options for yt-dlp downloader
            output_path: Path where the stream will be saved
        """
        try:
            self._download_stream_with_retries(stream_url, ydl_opts, output_path)

            # Calculate download duration
            if self._download_start_time:
                duration = datetime.now() - self._download_start_time
                duration_str = str(duration).split(".")[0]  # Remove microseconds
            else:
                duration_str = "unknown"

            # Check file size
            if output_path.exists():
                file_size = output_path.stat().st_size
                size_str = format_file_size(file_size)
                self._log(
                    "info",
                    f"✅ Download completed: {output_path.name} "
                    f"({size_str}, {duration_str})",
                )
            else:
                self._log(
                    "warning",
                    "⚠️  Download finished but file not found: "
                    f"{output_path}; {self._build_output_state_details(output_path)}",
                )
                if not self._has_sibling_fragment_outputs(output_path):
                    return

            self._notify_download_complete(output_path)

        except yt_dlp.utils.DownloadError as e:
            error_message = str(e)
            self._log(
                "error",
                "❌ Download error: "
                f"{error_message}; session_key={self.session_key or 'none'}, "
                f"{self._build_output_state_details(output_path)}",
            )
            if self._is_auth_error(error_message):
                self._notify_auth_error(error_message)
            elif self._is_m3u8_access_error(error_message):
                self._notify_download_error(error_message)
            else:
                self._notify_download_failure(error_message)

        except Exception as e:
            self._log(
                "error",
                "❌ Unexpected download error: "
                f"{e}; session_key={self.session_key or 'none'}, "
                f"{self._build_output_state_details(output_path)}",
            )
            self._notify_download_failure(str(e))

        finally:
            self._current_output_path = None
            self._download_start_time = None

    def _is_m3u8_access_error(self, error_message: str) -> bool:
        """
        Check if the error message indicates an M3U8 access failure.

        Args:
            error_message: Error message from yt-dlp

        Returns:
            True if error indicates M3U8 access issues (e.g., paid content)
        """
        return any(
            pattern.lower() in error_message.lower()
            for pattern in self.ACCESS_ERROR_PATTERNS
        )

    def _is_auth_error(self, error_message: str) -> bool:
        """Check if the error message indicates an authentication failure."""
        return any(
            pattern.lower() in error_message.lower()
            for pattern in self.AUTH_ERROR_PATTERNS
        )

    def _is_retryable_access_error(self, error_message: str) -> bool:
        """Check if an access error should retry before the stream is blocked."""
        return any(
            pattern.lower() in error_message.lower()
            for pattern in self.RETRYABLE_ACCESS_ERROR_PATTERNS
        )

    def _build_download_retrying(self) -> Retrying:
        """Build a tenacity retry controller for full-task yt-dlp retries."""
        return Retrying(
            reraise=True,
            stop=stop_after_attempt(max(1, self.DOWNLOAD_TASK_RETRY_ATTEMPTS)),
            wait=wait_exponential(multiplier=self.DOWNLOAD_TASK_RETRY_BACKOFF_FACTOR),
            retry=retry_if_exception_type(_RetryableDownloadTaskError),
            sleep=time.sleep,
            before_sleep=self._log_before_retry,
        )

    def _log_before_retry(self, retry_state) -> None:
        """Log one retry attempt before sleeping."""
        exception = retry_state.outcome.exception()
        wait_seconds = 0.0
        if retry_state.next_action is not None:
            wait_seconds = retry_state.next_action.sleep
        output_state = "output_path=unknown"
        if self._current_output_path is not None:
            output_state = self._build_output_state_details(self._current_output_path)
        self._log(
            "warning",
            f"⚠️ Download attempt {retry_state.attempt_number}/"
            f"{self.DOWNLOAD_TASK_RETRY_ATTEMPTS} failed; retrying in "
            f"{wait_seconds:.1f}s: {exception}; "
            f"session_key={self.session_key or 'none'}, {output_state}",
        )

    def _download_stream_with_retries(
        self,
        stream_url: str,
        ydl_opts: Dict[str, Any],
        output_path: Path,
    ) -> None:
        """Run yt-dlp and retry the full task for transient download failures."""
        attempt_number = 0

        try:
            for attempt in self._build_download_retrying():
                with attempt:
                    attempt_number = attempt.retry_state.attempt_number
                    self._log(
                        "info",
                        f"?? Download attempt {attempt_number}/{self.DOWNLOAD_TASK_RETRY_ATTEMPTS} started: "
                        f"session_key={self.session_key or 'none'}, output={output_path.name}",
                    )
                    if self.logger.isEnabledFor(logging.DEBUG):
                        self._log(
                            "debug",
                            f"   Attempt context: {self._build_output_state_details(output_path)}",
                        )
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([stream_url])
                    except yt_dlp.utils.DownloadError as exc:
                        error_message = str(exc)
                        if self._is_auth_error(error_message):
                            raise
                        if self._is_retryable_access_error(error_message):
                            raise _RetryableDownloadTaskError(error_message) from exc
                        if self._is_m3u8_access_error(error_message):
                            raise
                        raise _RetryableDownloadTaskError(error_message) from exc
        except _RetryableDownloadTaskError as exc:
            raise yt_dlp.utils.DownloadError(str(exc)) from exc

        if attempt_number > 1:
            self._log(
                "info",
                f"✅ Download succeeded on retry attempt "
                f"{attempt_number}/{self.DOWNLOAD_TASK_RETRY_ATTEMPTS}",
            )

    def _notify_download_error(self, error_message: str) -> None:
        """
        Notify via callback if the download error indicates M3U8 access failure.

        Only invokes the callback for M3U8-related errors (e.g., paid content
        returning 404 on media playlists).

        Args:
            error_message: Error message from yt-dlp
        """
        if self._on_download_error and self._is_m3u8_access_error(error_message):
            try:
                self._on_download_error(error_message)
            except Exception as e:
                self.logger.error(f"Error in download error callback: {e}")

    def _notify_auth_error(self, error_message: str) -> None:
        """Notify listeners that credentials appear invalid for this download."""
        if not self._on_download_auth_error or not self._is_auth_error(error_message):
            return

        try:
            if self.session_key:
                self._on_download_auth_error(
                    RawDownloadAuthFailed(
                        session_key=self.session_key,
                        error_message=error_message,
                    )
                )
                return

            self._on_download_auth_error(error_message)
        except Exception as e:
            self.logger.error(f"Error in download auth callback: {e}")

    def _notify_download_complete(self, output_path: Path) -> None:
        """Notify listeners that a raw download finished successfully."""
        if not self._on_download_complete or not self.session_key:
            return

        try:
            self._on_download_complete(
                RawDownloadCompleted(
                    session_key=self.session_key,
                    staging_dir=output_path.parent,
                )
            )
        except Exception as e:
            self.logger.error(f"Error in download complete callback: {e}")

    def _notify_download_failure(self, error_message: str) -> None:
        """Notify listeners that a raw download failed for a non-blocked reason."""
        if not self._on_download_failure or not self.session_key:
            return

        try:
            self._on_download_failure(
                RawDownloadFailed(
                    session_key=self.session_key,
                    error_message=error_message,
                )
            )
        except Exception as e:
            self.logger.error(f"Error in download failure callback: {e}")

    @property
    def current_output_path(self) -> Optional[Path]:
        """Get the current download output path, if any."""
        return self._current_output_path

    @property
    def download_duration(self) -> Optional[float]:
        """Get the current download duration in seconds, if downloading."""
        if self._download_start_time and self.is_alive():
            return (datetime.now() - self._download_start_time).total_seconds()
        return None

