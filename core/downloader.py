"""
Stream downloader module.

Provides functionality to download live streams using yt-dlp,
with support for concurrent downloads and automatic file management.
"""

import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import yt_dlp

from core.constants import (
    DEFAULT_DOWNLOAD_RETRIES,
    DEFAULT_FRAGMENT_RETRIES,
    DEFAULT_HTTP_HEADERS,
)
from core.logger import setup_logger
from core.utils import format_file_size

__all__ = [
    "StreamDownloader",
    "INVALID_FILENAME_CHARS",
]

# Characters that are invalid in filenames across different operating systems
INVALID_FILENAME_CHARS = r'[\\/:*?"<>|]'


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

    def __init__(self, creator_name: str) -> None:
        """
        Initialize a new stream downloader for a creator.

        Args:
            creator_name: Name of the content creator
        """
        self.creator_name = creator_name
        self.logger = setup_logger(f"Downloader-{creator_name}")
        self.download_thread: Optional[threading.Thread] = None
        self._current_output_path: Optional[Path] = None
        self._download_start_time: Optional[datetime] = None

    def download(self, stream_url: str, live_title: str) -> None:
        """
        Initiate a new download operation in a separate thread.

        Args:
            stream_url: URL of the stream m3u8 to download
            live_title: Title of the live stream for the output filename
        """
        # Sanitize filename by removing invalid characters
        safe_title = self._sanitize_filename(live_title)

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

        self.logger.info(f"📥 Starting download: \"{safe_title}\"")
        self.logger.info(f"   Output: {output_path.name}")

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

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """
        Remove invalid characters from a filename.

        Args:
            filename: Original filename

        Returns:
            Sanitized filename safe for filesystem use
        """
        # Remove invalid characters
        sanitized = re.sub(INVALID_FILENAME_CHARS, "", filename)
        # Remove leading/trailing whitespace
        sanitized = sanitized.strip()
        # Replace multiple spaces with single space
        sanitized = re.sub(r"\s+", " ", sanitized)
        return sanitized or "untitled"

    def _build_output_path(self, safe_title: str) -> Path:
        """
        Construct the output file path.

        Args:
            safe_title: Sanitized stream title

        Returns:
            Path object for the output file
        """
        date_str = datetime.today().strftime("%Y-%m-%d")
        filename = f"#{self.creator_name} {date_str} {safe_title}.mp4"

        return Path.cwd() / self.ARCHIVE_DIR / self.creator_name / filename

    def _build_ydl_options(self, output_path: Path) -> Dict[str, Any]:
        """
        Build yt-dlp options dictionary.

        Args:
            output_path: Path for the output file

        Returns:
            Dictionary of yt-dlp options
        """
        return {
            "format": self.DEFAULT_FORMAT,
            "outtmpl": str(output_path),
            "http_headers": DEFAULT_HTTP_HEADERS.copy(),
            "logger": self.logger,
            "quiet": True,
            "no_progress": True,
            "no_warnings": True,
            # Retry settings for reliability
            "retries": DEFAULT_DOWNLOAD_RETRIES,
            "fragment_retries": DEFAULT_FRAGMENT_RETRIES,
            # Continue partial downloads
            "continuedl": True,
        }

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
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([stream_url])

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
                self.logger.info(
                    f"✅ Download completed: {output_path.name} "
                    f"({size_str}, {duration_str})"
                )
            else:
                self.logger.warning(
                    f"⚠️  Download finished but file not found: {output_path}"
                )

        except yt_dlp.utils.DownloadError as e:
            self.logger.error(f"❌ Download error: {e}")

        except Exception as e:
            self.logger.error(f"❌ Unexpected download error: {e}")

        finally:
            self._current_output_path = None
            self._download_start_time = None

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
