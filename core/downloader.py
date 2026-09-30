"""Live stream downloader built on yt-dlp."""

import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yt_dlp
import yt_dlp.utils
from pathvalidate import sanitize_filename
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.constants import (
    DEFAULT_DOWNLOAD_RETRIES,
    DEFAULT_DOWNLOAD_SOCKET_TIMEOUT,
    DEFAULT_DOWNLOAD_TASK_RETRY_BACKOFF_FACTOR,
    DEFAULT_FRAGMENT_RETRIES,
    DEFAULT_HTTP_HEADERS,
    DEFAULT_MAX_RETRIES,
)
from core.logger import bind, is_ytdlp_internal_logging_enabled, setup_logger
from core.utils import (
    MAX_FILENAME_COMPONENT_BYTES,
    fit_filename_component_bytes,
    format_file_size,
)
from models.download import (
    RawDownloadAuthFailed,
    RawDownloadCompleted,
    RawDownloadFailed,
)

__all__ = [
    "StreamDownloader",
]

# Playlist URLs embed key2. Mask before any yt-dlp message reaches the logger.
_KEY2_QUERY_RE = re.compile(r"key2=[^&\s\"']+")
# yt-dlp appends this suffix while downloading raw output.
_DOWNLOAD_FILENAME_MAX_BYTES = MAX_FILENAME_COMPONENT_BYTES - len(
    ".part".encode("utf-8")
)


class _RetryableDownloadTaskError(Exception):
    """Internal exception used to retry a full yt-dlp task."""

    pass


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
        masked = _KEY2_QUERY_RE.sub("key2=REDACTED", normalized)
        self._downloader.log.debug(f"yt-dlp: {masked}")

    def debug(self, message: Any) -> None:
        self._emit(message)

    def info(self, message: Any) -> None:
        self._emit(message)

    def warning(self, message: Any) -> None:
        self._emit(message)

    def error(self, message: Any) -> None:
        self._emit(message)


class StreamDownloader:
    """Downloads one creator's live streams with yt-dlp."""

    ARCHIVE_DIR = "archive"

    DEFAULT_FORMAT = "bestvideo+bestaudio/best"

    MAX_DUPLICATE_FILES = 1000

    # Verified against the live service: paid streams return 404 from the moment
    # they go live, so an early 404 means blocked, not CDN warmup. Do not retry
    # 404 longer.
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
        filename_prefix: str = "",
        on_download_complete: Optional[Callable[[RawDownloadCompleted], None]] = None,
        on_download_failure: Optional[Callable[[RawDownloadFailed], None]] = None,
    ) -> None:
        """on_download_error is called from the download thread."""
        self.creator_name = creator_name
        self.logger = setup_logger("Downloader")
        self.log = bind(self.logger, creator_name)
        self.download_thread: Optional[threading.Thread] = None
        self._stop_requested = threading.Event()
        self._current_output_path: Optional[Path] = None
        self._download_start_time: Optional[datetime] = None
        self._on_download_error = on_download_error
        self._on_download_auth_error = on_download_auth_error
        self.session_key = session_key
        self.output_dir = output_dir
        self.output_extension = output_extension
        self.filename_prefix = filename_prefix
        self._on_download_complete = on_download_complete
        self._on_download_failure = on_download_failure
        self._last_download_attempt = 0
        self._yt_dlp_logger = _YtDlpLoggerBridge(
            self,
            enabled=is_ytdlp_internal_logging_enabled(),
        )

    def download(self, stream_url: str, live_title: str) -> None:
        """Start downloading in a background thread."""
        safe_title = sanitize_filename(live_title, replacement_text="_")
        if not safe_title:
            safe_title = "untitled"

        output_path = self._build_output_path(safe_title)

        output_path = self.get_unique_path(
            output_path,
            max_bytes=_DOWNLOAD_FILENAME_MAX_BYTES,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        self._current_output_path = output_path
        self._download_start_time = datetime.now()

        ydl_opts = self._build_ydl_options(output_path)

        self.log.debug(
            f"session_key={self.session_key or 'none'}, output_path={output_path}",
        )

        self.download_thread = threading.Thread(
            target=self._download_worker,
            args=(stream_url, ydl_opts, output_path),
            name=f"download-{self.creator_name}",
            daemon=True,
        )
        self.download_thread.start()

        # Sole confirmation that a recording began, so log it only after
        # Thread.start() succeeds. The title is omitted because the monitor already
        # logged it. The session prefix ties this log to a file on disk.
        session_prefix = (self.filename_prefix or "").rstrip("_")
        self.log.info(
            (
                f"📥 Recording started (session {session_prefix})"
                if session_prefix
                else "📥 Recording started"
            ),
        )

    def is_alive(self) -> bool:
        return self.download_thread is not None and self.download_thread.is_alive()

    def request_stop(self) -> None:
        """
        Wind down instead of retrying.

        Shutdown kills the recording ffmpeg, which looks like a transient failure.
        Without this flag the task burns its retry budget and its terminal event
        arrives after the merge executor has closed.
        """
        self._stop_requested.set()

    def _build_output_path(self, safe_title: str) -> Path:
        date_str = datetime.today().strftime("%Y-%m-%d")
        filename = f"{self.filename_prefix}#{self.creator_name} {date_str} {safe_title}{self.output_extension}"

        if self.output_dir is not None:
            return self.output_dir / filename

        return Path.cwd() / self.ARCHIVE_DIR / self.creator_name / filename

    def _build_ydl_options(self, output_path: Path) -> Dict[str, Any]:
        options = {
            "format": self.DEFAULT_FORMAT,
            "outtmpl": str(output_path),
            # Honored under FFmpegFD too: yt-dlp serializes these to ffmpeg -headers.
            "http_headers": DEFAULT_HTTP_HEADERS.copy(),
            "logger": self._yt_dlp_logger,
            "quiet": True,
            "no_progress": True,
            "no_warnings": True,
            # Inert for live HLS (FFmpegFD fetches). Only apply on native paths.
            "retries": DEFAULT_DOWNLOAD_RETRIES,
            "fragment_retries": DEFAULT_FRAGMENT_RETRIES,
            "socket_timeout": DEFAULT_DOWNLOAD_SOCKET_TIMEOUT,
            "continuedl": True,
            # The real mechanism for live: ffmpeg input args. -reconnect_at_eof is
            # omitted on purpose because HLS segment reads hit EOF by design.
            "external_downloader_args": {
                "ffmpeg_i": [
                    "-rw_timeout",
                    "30000000",
                    "-reconnect",
                    "1",
                    "-reconnect_streamed",
                    "1",
                    "-reconnect_on_network_error",
                    "1",
                    "-reconnect_on_http_error",
                    "429,5xx",
                    "-reconnect_delay_max",
                    "30",
                    "-seg_max_retry",
                    "20",
                ],
            },
        }

        if self.output_extension == ".mp4":
            options["merge_output_format"] = "mp4"

        return options

    @classmethod
    def get_unique_path(
        cls,
        base_path: Path,
        max_bytes: int = MAX_FILENAME_COMPONENT_BYTES,
    ) -> Path:
        """
        Return base_path, or the first free `_N` variant of it.

        Raises RuntimeError past MAX_DUPLICATE_FILES.
        """
        base_path = fit_filename_component_bytes(base_path, max_bytes=max_bytes)
        # ``Path.exists()`` follows symlinks and returns False for a dangling
        # link. Treat that link as occupied: the no-overwrite install path may
        # race with it, and repeatedly selecting it would spin on FileExistsError.
        if not os.path.lexists(base_path):
            return base_path

        counter = 1

        while True:
            new_path = fit_filename_component_bytes(
                base_path,
                f"_{counter}",
                max_bytes=max_bytes,
            )
            if not os.path.lexists(new_path):
                return new_path
            counter += 1
            if counter > cls.MAX_DUPLICATE_FILES:
                raise RuntimeError(f"Too many duplicate files for {base_path.stem}")

    @staticmethod
    def _has_sibling_fragment_outputs(output_path: Path) -> bool:
        fragment_pattern = f"{output_path.stem}_*{output_path.suffix}"
        return any(output_path.parent.glob(fragment_pattern))

    def _adopt_part_output(self, output_path: Path) -> bool:
        """Rename a dead recording's .ts.part onto its raw output name."""
        # Only .ts: a truncated MPEG-TS is still demuxable, which is what makes
        # concat safe on it, while a truncated mp4 has no moov atom yet and
        # would be unplayable.
        if output_path.suffix != ".ts":
            return False

        part_path = Path(f"{output_path}.part")
        try:
            if not part_path.is_file() or part_path.stat().st_size == 0:
                return False

            part_path.rename(output_path)
        except OSError as exc:
            # Must not escape: this runs inside the shutdown error handler, and
            # raising here would leave the session without a terminal event.
            self.log.warning(
                f"⚠️ Could not adopt partial download {part_path.name}: {exc}",
            )
            return False

        return True

    @staticmethod
    def _extract_short_error_reason(error_text: str) -> str:
        """Prefer an HTTP Error token, else first line, truncated to ~120 chars."""
        match = re.search(r"HTTP Error \d+[^;,)]*", error_text)
        if match:
            return match.group(0).strip()
        first_line = (error_text.splitlines() or ["unknown error"])[0].strip()
        if len(first_line) > 120:
            return f"{first_line[:117]}..."
        return first_line or "unknown error"

    def _inspect_output_state(self, output_path: Path) -> Dict[str, Any]:
        part_path = Path(f"{output_path}.part")
        output_exists = output_path.exists()
        part_exists = part_path.exists()
        sibling_fragments = self._has_sibling_fragment_outputs(output_path)
        output_size = (
            format_file_size(output_path.stat().st_size) if output_exists else "0 B"
        )
        part_size = format_file_size(part_path.stat().st_size) if part_exists else "0 B"
        return {
            "output_path": output_path,
            "part_path": part_path,
            "output_exists": output_exists,
            "part_exists": part_exists,
            "sibling_fragments": sibling_fragments,
            "output_size": output_size,
            "part_size": part_size,
        }

    def _build_output_state_details(self, output_path: Path) -> str:
        """Full diagnostic dump for DEBUG logs (includes paths and session fields)."""
        state = self._inspect_output_state(output_path)
        return (
            f"output_path={state['output_path']}, "
            f"output_exists={state['output_exists']}, output_size={state['output_size']}, "
            f"part_path={state['part_path']}, part_exists={state['part_exists']}, "
            f"part_size={state['part_size']}, "
            f"sibling_fragments={state['sibling_fragments']}"
        )

    def _build_compact_output_state_summary(self, output_path: Path) -> str:
        """Path-free human summary for WARNING/ERROR logs."""
        state = self._inspect_output_state(output_path)
        output = (
            f"present ({state['output_size']})" if state["output_exists"] else "missing"
        )
        part = f"present ({state['part_size']})" if state["part_exists"] else "missing"
        fragments = "yes" if state["sibling_fragments"] else "no"
        return f"output={output}, part={part}, fragments={fragments}"

    def _log_output_state_debug(
        self,
        prefix: str,
        error_message: str,
        output_path: Optional[Path],
    ) -> None:
        if output_path is None:
            details = "output_path=unknown"
        else:
            details = self._build_output_state_details(output_path)
        self.log.debug(
            f"{prefix}{error_message}; "
            f"session_key={self.session_key or 'none'}, {details}",
        )

    def _download_worker(
        self,
        stream_url: str,
        ydl_opts: Dict[str, Any],
        output_path: Path,
    ) -> None:
        try:
            self._download_stream_with_retries(stream_url, ydl_opts, output_path)

            if self._download_start_time:
                duration = datetime.now() - self._download_start_time
                duration_str = str(duration).split(".")[0]
            else:
                duration_str = "unknown"

            if output_path.exists():
                file_size = output_path.stat().st_size
                size_str = format_file_size(file_size)
                self.log.info(
                    f"✅ Download completed: {output_path.name} "
                    f"({size_str}, {duration_str})",
                )
            else:
                compact = self._build_compact_output_state_summary(output_path)
                self.log.warning(
                    "⚠️  Download finished but file not found: "
                    f"{output_path.name}; {compact}",
                )
                self._log_output_state_debug(
                    "Download finished but file not found: ",
                    str(output_path),
                    output_path,
                )
                if not self._has_sibling_fragment_outputs(output_path):
                    # Without this the session never leaves RAW_RUNNING: no
                    # completion, no failure, and the creator's raw lock stays
                    # held until the process restarts.
                    self._notify_download_failure(
                        "download finished but produced no output file"
                    )
                    return

            self._notify_download_complete(output_path)

        except yt_dlp.utils.DownloadError as e:
            error_message = str(e)
            if self._stop_requested.is_set():
                # Shutdown killed the recording ffmpeg, so this failure is expected.
                # Classifying it as blocked or retryable would drop the session and
                # orphan the raw output already on disk.
                session_prefix = (self.filename_prefix or "").rstrip("_")
                if output_path.exists() or self._has_sibling_fragment_outputs(
                    output_path
                ):
                    self.log.info(
                        f"⏹️ Recording stopped for shutdown (session {session_prefix}); "
                        f"handing raw output to merge: {output_path.name}",
                    )
                    self._notify_download_complete(output_path)
                    return

                # The recording may sit in the .part yt-dlp abandoned. Adopting it
                # keeps the merge inside shutdown's budget.
                if self._adopt_part_output(output_path):
                    self.log.info(
                        f"⏹️ Recording stopped for shutdown (session {session_prefix}); "
                        f"adopted partial download as raw output: {output_path.name}",
                    )
                    self._notify_download_complete(output_path)
                    return

                short_reason = self._extract_short_error_reason(error_message)
                compact = self._build_compact_output_state_summary(output_path)
                self.log.warning(
                    f"⏹️ Recording stopped for shutdown (session {session_prefix}) with no "
                    f"finished raw output: {short_reason}; {compact}",
                )
                self._log_output_state_debug(
                    "Recording stopped for shutdown with no finished raw output: ",
                    error_message,
                    output_path,
                )
                self._notify_download_failure(error_message)
                return

            # .error, not .exception: this handler mostly sees classified 401/403/404
            # failures. The unexpected path below keeps the traceback.
            attempts = max(1, self._last_download_attempt)
            short_reason = self._extract_short_error_reason(error_message)
            compact = self._build_compact_output_state_summary(output_path)
            self.log.error(
                f"❌ Download failed after {attempts} attempts: "
                f"{short_reason}; {compact}",
            )
            self._log_output_state_debug(
                "Download failed: ", error_message, output_path
            )
            if self._is_auth_error(error_message):
                self._notify_auth_error(error_message)
            elif self._is_m3u8_access_error(error_message):
                self._notify_download_error(error_message)
            else:
                self._notify_download_failure(error_message)

        except Exception as e:
            attempts = max(1, self._last_download_attempt)
            short_reason = self._extract_short_error_reason(str(e))
            compact = self._build_compact_output_state_summary(output_path)
            self.log.exception(
                f"❌ Download failed after {attempts} attempts: "
                f"{short_reason}; {compact}",
            )
            self._log_output_state_debug(
                "Unexpected download error: ",
                str(e),
                output_path,
            )
            self._notify_download_failure(str(e))

        finally:
            self._current_output_path = None
            self._download_start_time = None

    def _is_m3u8_access_error(self, error_message: str) -> bool:
        """True for 403/404 errors, which mean blocked access (e.g. paid content)."""
        return any(
            pattern.lower() in error_message.lower()
            for pattern in self.ACCESS_ERROR_PATTERNS
        )

    def _is_auth_error(self, error_message: str) -> bool:
        return any(
            pattern.lower() in error_message.lower()
            for pattern in self.AUTH_ERROR_PATTERNS
        )

    def _is_retryable_access_error(self, error_message: str) -> bool:
        return any(
            pattern.lower() in error_message.lower()
            for pattern in self.RETRYABLE_ACCESS_ERROR_PATTERNS
        )

    def _build_download_retrying(self) -> Retrying:
        return Retrying(
            reraise=True,
            stop=stop_after_attempt(max(1, self.DOWNLOAD_TASK_RETRY_ATTEMPTS)),
            wait=wait_exponential(multiplier=self.DOWNLOAD_TASK_RETRY_BACKOFF_FACTOR),
            retry=self._should_retry_download,
            sleep=time.sleep,
            before_sleep=self._log_before_retry,
        )

    def _should_retry_download(self, retry_state) -> bool:
        """Retry transient yt-dlp failures unless shutdown asked this task to stop."""
        if self._stop_requested.is_set():
            return False
        return retry_if_exception_type(_RetryableDownloadTaskError)(retry_state)

    def _log_before_retry(self, retry_state) -> None:
        exception = retry_state.outcome.exception()
        wait_seconds = 0.0
        if retry_state.next_action is not None:
            wait_seconds = retry_state.next_action.sleep
        short_reason = self._extract_short_error_reason(str(exception))
        self.log.warning(
            f"⚠️ Attempt {retry_state.attempt_number}/"
            f"{self.DOWNLOAD_TASK_RETRY_ATTEMPTS} failed ({short_reason}); "
            f"retrying in {wait_seconds:.1f}s",
        )
        self._log_output_state_debug(
            f"Attempt {retry_state.attempt_number}/"
            f"{self.DOWNLOAD_TASK_RETRY_ATTEMPTS} failed: ",
            str(exception),
            self._current_output_path,
        )

    def _download_stream_with_retries(
        self,
        stream_url: str,
        ydl_opts: Dict[str, Any],
        output_path: Path,
    ) -> None:
        attempt_number = 0
        self._last_download_attempt = 0

        try:
            for attempt in self._build_download_retrying():
                with attempt:
                    attempt_number = attempt.retry_state.attempt_number
                    self._last_download_attempt = attempt_number
                    if self._stop_requested.is_set():
                        # Set during the previous backoff sleep. A fresh yt-dlp run
                        # would only be killed again.
                        raise yt_dlp.utils.DownloadError(
                            "recording stopped for shutdown"
                        )
                    # The first attempt is implied by "Recording started".
                    if attempt_number > 1:
                        self.log.info(
                            f"🔁 Attempt {attempt_number}/{self.DOWNLOAD_TASK_RETRY_ATTEMPTS}",
                        )
                    if self.logger.isEnabledFor(logging.DEBUG):
                        self.log.debug(
                            f"attempt {attempt_number}/{self.DOWNLOAD_TASK_RETRY_ATTEMPTS}, "
                            f"session_key={self.session_key or 'none'}, "
                            f"output={output_path.name}, "
                            f"{self._build_output_state_details(output_path)}",
                        )
                    try:
                        # yt-dlp accepts these, but its stubs are narrower.
                        with yt_dlp.YoutubeDL(
                            ydl_opts,  # pyright: ignore[reportArgumentType]
                        ) as ydl:
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
            self.log.info(
                f"✅ Download succeeded on attempt "
                f"{attempt_number}/{self.DOWNLOAD_TASK_RETRY_ATTEMPTS}",
            )

    def _notify_download_error(self, error_message: str) -> None:
        """Invoke the callback only for M3U8 access errors."""
        if self._on_download_error and self._is_m3u8_access_error(error_message):
            try:
                self._on_download_error(error_message)
            except Exception as e:
                self.log.exception(f"Error in download error callback: {e}")

    def _notify_auth_error(self, error_message: str) -> None:
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
            self.log.exception(f"Error in download auth callback: {e}")

    def _notify_download_complete(self, output_path: Path) -> None:
        if not self._on_download_complete or not self.session_key:
            return

        try:
            self._on_download_complete(
                RawDownloadCompleted(
                    session_key=self.session_key,
                    output_dir=output_path.parent,
                )
            )
        except Exception as e:
            self.log.exception(f"Error in download complete callback: {e}")

    def _notify_download_failure(self, error_message: str) -> None:
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
            self.log.exception(f"Error in download failure callback: {e}")
