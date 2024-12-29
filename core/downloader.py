import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import yt_dlp

from core.logger import setup_logger


class StreamDownloader:
    """
    Handles downloading of live streams using yt-dlp.

    This class manages downloading streams for a specific creator, including:
    - Setting up logging for download operations
    - Managing download threads
    - Handling file paths and naming
    - Executing the actual download process
    """

    def __init__(self, creator_name: str):
        """
        Initialize a new stream downloader for a creator.

        Args:
            creator_name (str): Name of the content creator
        """
        self.creator_name = creator_name
        # Setup logging using the centralized logger configuration
        self.logger = setup_logger(self.creator_name)
        # Initialize download thread reference
        self.download_thread: Optional[threading.Thread] = None

    def download(self, stream_url: str, live_title: str) -> None:
        """
        Initiate a new download operation in a separate thread.

        Args:
            stream_url (str): URL of the stream m3u8 to download
            live_title (str): Title of the live stream for the output filename
        """
        # Sanitize filename by removing invalid characters
        safe_title = re.sub(r'[\\/:*?"<>|]', "", live_title)

        # Construct output path with format: archive/creator_name/YYYY-MM-DD_title.mp4
        output_path = (
            Path.cwd()
            / "archive"
            / self.creator_name
            / f"#{self.creator_name} {datetime.today():%Y-%m-%d} {safe_title}.mp4"
        )

        # Ensure unique file path and create directories
        output_path = self.__get_unique_path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Configure yt-dlp options
        ydl_opts = {
            "format": "best",
            "outtmpl": str(output_path),
            "http_headers": {
                "Referer": "https://rplay.live",
                "Origin": "https://rplay.live",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
                ),
            },
            "logger": self.logger,
            "quiet": True,
            "no_progress": True,
            "no_warnings": True,
        }

        self.logger.info(
            f"Starting download: {safe_title} time: {datetime.today()}, "
            f"saving to: {output_path}"
        )

        # Start download in a separate thread
        self.download_thread = threading.Thread(
            target=self.__download_worker, args=(stream_url, ydl_opts, output_path)
        )
        self.download_thread.start()

    def is_alive(self) -> bool:
        """
        Check if the current download operation is still active.

        Returns:
            bool: True if download is in progress, False otherwise
        """
        return self.download_thread is not None and self.download_thread.is_alive()

    def __get_unique_path(self, base_path: Path) -> Path:
        """
        Generate a unique file path by appending a counter if file exists.

        Args:
            base_path (Path): Initial desired file path

        Returns:
            Path: Unique file path that doesn't exist
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

    def __download_worker(
        self, stream_url: str, ydl_opts: dict, output_path: Path
    ) -> None:
        """
        Worker function that performs the actual download operation.

        Args:
            stream_url (str): URL of the stream to download
            ydl_opts (dict): Options for yt-dlp downloader
            output_path (Path): Path where the stream will be saved
        """
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([stream_url])
                self.logger.info(f"Download completed: {output_path}")
        except Exception as e:
            self.logger.error(f"Download failed: {str(e)})")
