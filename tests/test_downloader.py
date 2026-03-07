"""Tests for stream downloader module."""

import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yt_dlp
from freezegun import freeze_time

from core.downloader import StreamDownloader
from models.download import RawDownloadCompleted, RawDownloadFailed


@pytest.fixture
def mock_yt_dlp():
    """Create a mock yt-dlp YoutubeDL context manager."""
    with patch('core.downloader.yt_dlp.YoutubeDL') as mock_ydl_class:
        mock_ydl = MagicMock()
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)
        yield mock_ydl_class, mock_ydl


class TestStreamDownloaderInit:
    """Tests for StreamDownloader initialization."""

    def test_init_sets_creator_name(self):
        """Test that initialization sets creator name correctly."""
        downloader = StreamDownloader("TestCreator")
        assert downloader.creator_name == "TestCreator"

    def test_init_sets_log_prefix(self):
        """Test that log prefix is set with creator name."""
        downloader = StreamDownloader("TestCreator")
        assert downloader._log_prefix == "[TestCreator]"

    def test_init_creates_logger(self):
        """Test that a logger is created on initialization."""
        downloader = StreamDownloader("TestCreator")
        assert downloader.logger is not None

    def test_init_no_active_thread(self):
        """Test that no download thread exists on initialization."""
        downloader = StreamDownloader("TestCreator")
        assert downloader.download_thread is None

    def test_init_no_current_output_path(self):
        """Test that no output path is set on initialization."""
        downloader = StreamDownloader("TestCreator")
        assert downloader._current_output_path is None

    def test_init_no_download_start_time(self):
        """Test that no download start time is set on initialization."""
        downloader = StreamDownloader("TestCreator")
        assert downloader._download_start_time is None


class TestIsAlive:
    """Tests for is_alive method."""

    def test_is_alive_no_thread(self):
        """Test is_alive returns False when no thread exists."""
        downloader = StreamDownloader("TestCreator")
        assert downloader.is_alive() is False

    def test_is_alive_with_active_thread(self):
        """Test is_alive returns True when thread is running."""
        downloader = StreamDownloader("TestCreator")
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        downloader.download_thread = mock_thread
        assert downloader.is_alive() is True

    def test_is_alive_with_dead_thread(self):
        """Test is_alive returns False when thread has finished."""
        downloader = StreamDownloader("TestCreator")
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        downloader.download_thread = mock_thread
        assert downloader.is_alive() is False


class TestBuildOutputPath:
    """Tests for _build_output_path method."""

    @freeze_time("2026-01-17")
    def test_output_path_format(self):
        """Test output path has correct format."""
        downloader = StreamDownloader("TestCreator")
        path = downloader._build_output_path("Stream Title")
        assert path.name == "#TestCreator 2026-01-17 Stream Title.mp4"

    @freeze_time("2026-01-17")
    def test_output_path_in_archive_dir(self):
        """Test output path is under archive directory."""
        downloader = StreamDownloader("TestCreator")
        path = downloader._build_output_path("Test")
        assert "archive" in str(path)
        assert "TestCreator" in str(path)

    @freeze_time("2026-01-17")
    def test_output_path_with_special_title(self):
        """Test output path with already sanitized title."""
        downloader = StreamDownloader("Creator")
        path = downloader._build_output_path("My_Stream")
        assert path.suffix == ".mp4"
        assert "My_Stream" in path.name

    @freeze_time("2026-01-17")
    def test_output_path_uses_ts_extension_for_session_output_dir(self, tmp_path):
        """Test session-scoped downloads use ts files in the provided output dir."""
        downloader = StreamDownloader(
            "Creator",
            session_key="creator1:2026-03-06T12:00:00",
            output_dir=tmp_path,
            output_extension=".ts",
        )

        path = downloader._build_output_path("Test")

        assert path == tmp_path / "#Creator 2026-01-17 Test.ts"


class TestGetUniquePath:
    """Tests for _get_unique_path class method."""

    def test_no_conflict_returns_original(self, tmp_path):
        """Test returns original path when no file exists."""
        path = tmp_path / "test.mp4"
        result = StreamDownloader._get_unique_path(path)
        assert result == path

    def test_with_conflict_adds_counter(self, tmp_path):
        """Test adds _1 suffix when file exists."""
        path = tmp_path / "test.mp4"
        path.touch()
        result = StreamDownloader._get_unique_path(path)
        assert result == tmp_path / "test_1.mp4"

    def test_multiple_conflicts_increments_counter(self, tmp_path):
        """Test increments counter for multiple conflicts."""
        path = tmp_path / "test.mp4"
        path.touch()
        (tmp_path / "test_1.mp4").touch()
        (tmp_path / "test_2.mp4").touch()
        result = StreamDownloader._get_unique_path(path)
        assert result == tmp_path / "test_3.mp4"

    def test_max_duplicates_raises_error(self, tmp_path):
        """Test raises RuntimeError after MAX_DUPLICATE_FILES."""
        path = tmp_path / "test.mp4"
        path.touch()
        for i in range(1, 1001):
            (tmp_path / f"test_{i}.mp4").touch()
        with pytest.raises(RuntimeError, match="Too many duplicate files"):
            StreamDownloader._get_unique_path(path)


class TestBuildYdlOptions:
    """Tests for _build_ydl_options method."""

    def test_options_format(self, tmp_path):
        """Test ydl options has correct format setting."""
        downloader = StreamDownloader("TestCreator")
        path = tmp_path / "test.mp4"
        options = downloader._build_ydl_options(path)
        assert options["format"] == "bestvideo+bestaudio/best"

    def test_options_output_path(self, tmp_path):
        """Test ydl options has correct output template."""
        downloader = StreamDownloader("TestCreator")
        path = tmp_path / "test.mp4"
        options = downloader._build_ydl_options(path)
        assert options["outtmpl"] == str(path)

    def test_options_merge_format(self, tmp_path):
        """Test ydl options has mp4 merge format."""
        downloader = StreamDownloader("TestCreator")
        path = tmp_path / "test.mp4"
        options = downloader._build_ydl_options(path)
        assert options["merge_output_format"] == "mp4"

    def test_options_quiet_mode(self, tmp_path):
        """Test ydl options has quiet mode enabled."""
        downloader = StreamDownloader("TestCreator")
        path = tmp_path / "test.mp4"
        options = downloader._build_ydl_options(path)
        assert options["quiet"] is True
        assert options["no_progress"] is True
        assert options["no_warnings"] is True

    def test_options_retry_settings(self, tmp_path):
        """Test ydl options has retry settings."""
        downloader = StreamDownloader("TestCreator")
        path = tmp_path / "test.mp4"
        options = downloader._build_ydl_options(path)
        assert "retries" in options
        assert "fragment_retries" in options
        assert options["continuedl"] is True


class TestDownloadMethod:
    """Tests for download method."""

    @patch.object(StreamDownloader, '_download_worker')
    def test_download_sanitizes_title(self, mock_worker, tmp_path, monkeypatch):
        """Test download sanitizes special characters in title."""
        monkeypatch.chdir(tmp_path)
        downloader = StreamDownloader("TestCreator")
        downloader.download("http://example.com/stream.m3u8", "Test/Title:With*Special")
        assert downloader.download_thread is not None
        downloader.download_thread.join(timeout=1)

    @patch.object(StreamDownloader, '_download_worker')
    def test_download_empty_title_fallback(self, mock_worker, tmp_path, monkeypatch):
        """Test download uses 'untitled' for empty title."""
        monkeypatch.chdir(tmp_path)
        downloader = StreamDownloader("TestCreator")
        downloader.download("http://example.com/stream.m3u8", "")
        assert downloader._current_output_path is not None
        assert "untitled" in downloader._current_output_path.name

    @patch.object(StreamDownloader, '_download_worker')
    def test_download_starts_thread(self, mock_worker, tmp_path, monkeypatch):
        """Test download starts a new thread."""
        monkeypatch.chdir(tmp_path)
        downloader = StreamDownloader("TestCreator")
        downloader.download("http://example.com/stream.m3u8", "Test Stream")
        assert downloader.download_thread is not None
        assert downloader.download_thread.name == "download-TestCreator"

    @patch.object(StreamDownloader, '_download_worker')
    def test_download_sets_output_path(self, mock_worker, tmp_path, monkeypatch):
        """Test download sets current output path."""
        monkeypatch.chdir(tmp_path)
        downloader = StreamDownloader("TestCreator")
        downloader.download("http://example.com/stream.m3u8", "Test Stream")
        assert downloader._current_output_path is not None
        assert downloader._current_output_path.suffix == ".mp4"

    @patch.object(StreamDownloader, '_download_worker')
    def test_download_sets_start_time(self, mock_worker, tmp_path, monkeypatch):
        """Test download sets download start time."""
        monkeypatch.chdir(tmp_path)
        downloader = StreamDownloader("TestCreator")
        before = datetime.now()
        downloader.download("http://example.com/stream.m3u8", "Test")
        after = datetime.now()
        assert downloader._download_start_time is not None
        assert before <= downloader._download_start_time <= after

    @patch.object(StreamDownloader, '_download_worker')
    def test_download_logs_context(self, mock_worker, tmp_path, monkeypatch):
        """Test download logs session and output context before starting."""
        monkeypatch.chdir(tmp_path)
        downloader = StreamDownloader(
            "TestCreator",
            session_key="creator1:1772880472",
        )

        with patch.object(downloader, '_log') as mock_log:
            downloader.download("http://example.com/stream.m3u8", "Test Stream")
            assert downloader.download_thread is not None
            downloader.download_thread.join(timeout=1)

        assert any(
            call.args[0] == "debug"
            and "session_key=creator1:1772880472" in call.args[1]
            and "output_path=" in call.args[1]
            for call in mock_log.call_args_list
        )


class TestDownloadWorker:
    """Tests for _download_worker method."""

    def test_worker_success_logs_completion(self, mock_yt_dlp, tmp_path):
        """Test successful download logs completion with file size."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        downloader = StreamDownloader("TestCreator")
        output_path = tmp_path / "test.mp4"
        output_path.write_bytes(b"x" * 1024)
        downloader._download_start_time = datetime.now()
        downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)
        mock_ydl.download.assert_called_once()
        assert downloader._current_output_path is None
        assert downloader._download_start_time is None

    def test_worker_file_not_found_logs_warning(self, mock_yt_dlp, tmp_path):
        """Test warning logged when file not found after download."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        downloader = StreamDownloader("TestCreator")
        output_path = tmp_path / "nonexistent.mp4"
        downloader._download_start_time = datetime.now()
        downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)
        assert downloader._current_output_path is None

    def test_worker_download_error_handled(self, mock_yt_dlp, tmp_path):
        """Test DownloadError is caught and logged."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        downloader = StreamDownloader("TestCreator")
        output_path = tmp_path / "test.mp4"
        downloader._download_start_time = datetime.now()
        mock_ydl.download.side_effect = yt_dlp.utils.DownloadError("Network error")
        downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)
        assert downloader._current_output_path is None

    def test_worker_unexpected_error_handled(self, mock_yt_dlp, tmp_path):
        """Test unexpected exceptions are caught and logged."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        downloader = StreamDownloader("TestCreator")
        output_path = tmp_path / "test.mp4"
        downloader._download_start_time = datetime.now()
        mock_ydl.download.side_effect = RuntimeError("Unexpected error")
        downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)
        assert downloader._current_output_path is None

    def test_worker_notifies_on_download_complete(self, mock_yt_dlp, tmp_path):
        """Test successful raw download emits a completion payload."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        events = []
        downloader = StreamDownloader(
            "Creator",
            session_key="creator1:2026-03-06T12:00:00",
            output_dir=tmp_path,
            output_extension=".ts",
            on_download_complete=lambda result: events.append(result),
        )
        output_path = tmp_path / "#Creator 2026-03-06 Test.ts"
        output_path.write_bytes(b"x")
        downloader._download_start_time = datetime.now()

        downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)

        mock_ydl.download.assert_called_once()
        assert len(events) == 1
        assert isinstance(events[0], RawDownloadCompleted)
        assert events[0].session_key == "creator1:2026-03-06T12:00:00"

    def test_worker_missing_ts_output_without_fragments_does_not_notify_completion(
        self, mock_yt_dlp, tmp_path
    ):
        """Test missing ts output without any fragments does not emit completion."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        events = []
        downloader = StreamDownloader(
            "Creator",
            session_key="creator1:2026-03-06T12:00:00",
            output_dir=tmp_path,
            output_extension=".ts",
            on_download_complete=lambda result: events.append(result),
        )
        output_path = tmp_path / "#Creator 2026-03-06 Missing.ts"
        downloader._download_start_time = datetime.now()

        downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)

        mock_ydl.download.assert_called_once()
        assert events == []

    def test_worker_missing_primary_ts_output_with_fragments_still_notifies_completion(
        self, mock_yt_dlp, tmp_path
    ):
        """Test fragmented ts output still emits completion when sibling ts files exist."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        events = []
        downloader = StreamDownloader(
            "Creator",
            session_key="creator1:2026-03-06T12:00:00",
            output_dir=tmp_path,
            output_extension=".ts",
            on_download_complete=lambda result: events.append(result),
        )
        output_path = tmp_path / "#Creator 2026-03-06 Missing.ts"
        (tmp_path / "#Creator 2026-03-06 Missing_1.ts").write_bytes(b"x")
        downloader._download_start_time = datetime.now()

        downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)

        mock_ydl.download.assert_called_once()
        assert len(events) == 1


class TestDownloadErrorCallback:
    """Tests for on_download_error callback functionality."""

    def test_init_stores_callback(self):
        """Test that callback is stored on initialization."""
        callback = MagicMock()
        downloader = StreamDownloader("TestCreator", on_download_error=callback)
        assert downloader._on_download_error is callback

    def test_init_default_callback_is_none(self):
        """Test that callback defaults to None."""
        downloader = StreamDownloader("TestCreator")
        assert downloader._on_download_error is None

    def test_is_m3u8_access_error_detects_404(self):
        """Test detection of HTTP 404 errors in error messages."""
        downloader = StreamDownloader("TestCreator")
        assert downloader._is_m3u8_access_error("HTTP Error 404: Not Found") is True

    def test_is_m3u8_access_error_rejects_401(self):
        """Test 401 is not treated as blocked stream access."""
        downloader = StreamDownloader("TestCreator")
        assert downloader._is_m3u8_access_error("HTTP Error 401: Unauthorized") is False

    def test_is_m3u8_access_error_detects_403(self):
        """Test detection of HTTP 403 errors in error messages."""
        downloader = StreamDownloader("TestCreator")
        assert downloader._is_m3u8_access_error("HTTP Error 403: Forbidden") is True

    def test_is_m3u8_access_error_case_insensitive(self):
        """Test that error pattern matching is case-insensitive."""
        downloader = StreamDownloader("TestCreator")
        assert downloader._is_m3u8_access_error("http error 404") is True

    def test_is_m3u8_access_error_rejects_unrelated(self):
        """Test that unrelated errors are not matched."""
        downloader = StreamDownloader("TestCreator")
        assert downloader._is_m3u8_access_error("Network timeout") is False

    def test_is_m3u8_access_error_rejects_ffmpeg_exit_errors(self):
        """Test transient ffmpeg errors are not treated as blocked access."""
        downloader = StreamDownloader("TestCreator")
        assert downloader._is_m3u8_access_error("ERROR: ffmpeg exited with code 8") is False

    def test_notify_calls_callback_on_m3u8_error(self):
        """Test that callback is invoked for M3U8 access errors."""
        callback = MagicMock()
        downloader = StreamDownloader("TestCreator", on_download_error=callback)
        downloader._notify_download_error("HTTP Error 404: Not Found")
        callback.assert_called_once_with("HTTP Error 404: Not Found")

    def test_notify_auth_callback_on_401_error(self):
        """Test that 401 is routed to the auth callback."""
        callback = MagicMock()
        downloader = StreamDownloader("TestCreator", on_download_auth_error=callback)
        downloader._notify_auth_error("HTTP Error 401: Unauthorized")
        callback.assert_called_once_with("HTTP Error 401: Unauthorized")

    def test_notify_skips_callback_on_unrelated_error(self):
        """Test that callback is not invoked for unrelated errors."""
        callback = MagicMock()
        downloader = StreamDownloader("TestCreator", on_download_error=callback)
        downloader._notify_download_error("Network timeout")
        callback.assert_not_called()

    def test_notify_skips_when_no_callback(self):
        """Test that no error is raised when callback is None."""
        downloader = StreamDownloader("TestCreator")
        downloader._notify_download_error("HTTP Error 404: Not Found")

    def test_notify_handles_callback_exception(self):
        """Test that exceptions in callback are caught."""
        callback = MagicMock(side_effect=RuntimeError("callback error"))
        downloader = StreamDownloader("TestCreator", on_download_error=callback)
        downloader._notify_download_error("HTTP Error 404: Not Found")

    def test_worker_invokes_callback_on_download_error(self, mock_yt_dlp, tmp_path):
        """Test that _download_worker invokes callback on M3U8 DownloadError."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        callback = MagicMock()
        downloader = StreamDownloader("TestCreator", on_download_error=callback)
        output_path = tmp_path / "test.mp4"
        downloader._download_start_time = datetime.now()
        mock_ydl.download.side_effect = yt_dlp.utils.DownloadError(
            "HTTP Error 404: Not Found"
        )
        downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)
        callback.assert_called_once()

    def test_worker_no_callback_on_non_m3u8_error(self, mock_yt_dlp, tmp_path):
        """Test that _download_worker does not invoke callback for non-M3U8 errors."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        callback = MagicMock()
        downloader = StreamDownloader("TestCreator", on_download_error=callback)
        output_path = tmp_path / "test.mp4"
        downloader._download_start_time = datetime.now()
        mock_ydl.download.side_effect = yt_dlp.utils.DownloadError("Some other error")
        downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)
        callback.assert_not_called()

    def test_worker_retries_transient_download_errors_within_same_task(
        self, mock_yt_dlp, tmp_path
    ):
        """Test transient download errors retry immediately within the same task."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        completed_events = []
        failed_events = []
        downloader = StreamDownloader(
            "TestCreator",
            session_key="creator1:stream1",
            on_download_complete=lambda event: completed_events.append(event),
            on_download_failure=lambda event: failed_events.append(event),
        )
        output_path = tmp_path / "test.ts"
        output_path.write_bytes(b"x")
        downloader._download_start_time = datetime.now()
        mock_ydl.download.side_effect = [
            yt_dlp.utils.DownloadError("HTTP Error 500: Internal Server Error"),
            yt_dlp.utils.DownloadError("HTTP Error 500: Internal Server Error"),
            None,
        ]

        with patch("core.downloader.time.sleep") as mock_sleep:
            downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)

        assert mock_ydl.download.call_count == 3
        assert mock_sleep.call_count == 2
        assert len(completed_events) == 1
        assert failed_events == []

    def test_worker_does_not_retry_blocked_access_errors(self, mock_yt_dlp, tmp_path):
        """Test 403/404 access errors skip same-task retry and mark blocked."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        blocked_callback = MagicMock()
        failed_events = []
        downloader = StreamDownloader(
            "TestCreator",
            session_key="creator1:stream1",
            on_download_error=blocked_callback,
            on_download_failure=lambda event: failed_events.append(event),
        )
        output_path = tmp_path / "test.ts"
        downloader._download_start_time = datetime.now()
        mock_ydl.download.side_effect = yt_dlp.utils.DownloadError(
            "HTTP Error 404: Not Found"
        )

        with patch("core.downloader.time.sleep") as mock_sleep:
            downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)

        assert mock_ydl.download.call_count == 1
        mock_sleep.assert_not_called()
        blocked_callback.assert_called_once_with("HTTP Error 404: Not Found")
        assert failed_events == []

    def test_worker_routes_401_to_auth_failure(self, mock_yt_dlp, tmp_path):
        """Test 401 errors are surfaced as auth failures instead of blocked streams."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        auth_events = []
        blocked_callback = MagicMock()
        downloader = StreamDownloader(
            "TestCreator",
            session_key="creator1:stream1",
            on_download_error=blocked_callback,
            on_download_auth_error=lambda event: auth_events.append(event),
        )
        output_path = tmp_path / "test.ts"
        downloader._download_start_time = datetime.now()
        mock_ydl.download.side_effect = yt_dlp.utils.DownloadError(
            "HTTP Error 401: Unauthorized"
        )

        with patch("core.downloader.time.sleep") as mock_sleep:
            downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)

        assert mock_ydl.download.call_count == 1
        mock_sleep.assert_not_called()
        blocked_callback.assert_not_called()
        assert len(auth_events) == 1
        assert auth_events[0].session_key == "creator1:stream1"
        assert auth_events[0].error_message == "HTTP Error 401: Unauthorized"

    def test_worker_retries_ffmpeg_exit_errors_before_emitting_failure(
        self, mock_yt_dlp, tmp_path
    ):
        """Test ffmpeg exit errors are retried and eventually reported as failures."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        blocked_callback = MagicMock()
        failed_events = []
        downloader = StreamDownloader(
            "TestCreator",
            session_key="creator1:stream1",
            on_download_error=blocked_callback,
            on_download_failure=lambda event: failed_events.append(event),
        )
        output_path = tmp_path / "test.ts"
        downloader._download_start_time = datetime.now()
        mock_ydl.download.side_effect = [
            yt_dlp.utils.DownloadError("ERROR: ffmpeg exited with code 8"),
            yt_dlp.utils.DownloadError("ERROR: ffmpeg exited with code 8"),
            yt_dlp.utils.DownloadError("ERROR: ffmpeg exited with code 8"),
        ]

        with patch("core.downloader.time.sleep") as mock_sleep:
            downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)

        assert mock_ydl.download.call_count == 3
        assert [call.args[0] for call in mock_sleep.call_args_list] == [2.0, 4.0]
        blocked_callback.assert_not_called()
        assert len(failed_events) == 1
        assert failed_events[0].error_message == "ERROR: ffmpeg exited with code 8"

    def test_worker_logs_partial_output_details_on_failure(self, mock_yt_dlp, tmp_path):
        """Test final download error logs include .part output details."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        downloader = StreamDownloader(
            "TestCreator",
            session_key="creator1:stream1",
        )
        output_path = tmp_path / "test.ts"
        Path(f"{output_path}.part").write_bytes(b"abcdef")
        downloader._download_start_time = datetime.now()
        mock_ydl.download.side_effect = yt_dlp.utils.DownloadError("Some other error")

        with patch.object(downloader.logger, "error") as mock_error:
            downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)

        assert any(
            "output_path=" in str(call)
            and "part_exists=True" in str(call)
            and "part_size=6.0 B" in str(call)
            for call in mock_error.call_args_list
        )

    def test_worker_emits_failure_event_on_non_m3u8_error(self, mock_yt_dlp, tmp_path):
        """Test non-blocked download errors emit a raw failure event."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        events = []
        downloader = StreamDownloader(
            "TestCreator",
            session_key="creator1:2026-03-06T12:00:00",
            on_download_failure=lambda event: events.append(event),
        )
        output_path = tmp_path / "test.ts"
        downloader._download_start_time = datetime.now()
        mock_ydl.download.side_effect = yt_dlp.utils.DownloadError("Some other error")

        downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)

        assert len(events) == 1
        assert isinstance(events[0], RawDownloadFailed)
        assert events[0].session_key == "creator1:2026-03-06T12:00:00"
        assert events[0].error_message == "Some other error"

    def test_worker_emits_failure_event_on_unexpected_exception(self, mock_yt_dlp, tmp_path):
        """Test unexpected download exceptions emit a raw failure event."""
        mock_ydl_class, mock_ydl = mock_yt_dlp
        events = []
        downloader = StreamDownloader(
            "TestCreator",
            session_key="creator1:2026-03-06T12:00:00",
            on_download_failure=lambda event: events.append(event),
        )
        output_path = tmp_path / "test.ts"
        downloader._download_start_time = datetime.now()
        mock_ydl.download.side_effect = RuntimeError("Unexpected error")

        downloader._download_worker("http://example.com/stream.m3u8", {}, output_path)

        assert len(events) == 1
        assert isinstance(events[0], RawDownloadFailed)
        assert events[0].session_key == "creator1:2026-03-06T12:00:00"
        assert events[0].error_message == "Unexpected error"


class TestProperties:
    """Tests for StreamDownloader properties."""

    def test_current_output_path_returns_path(self):
        """Test current_output_path returns the set path."""
        downloader = StreamDownloader("TestCreator")
        test_path = Path("/tmp/test.mp4")
        downloader._current_output_path = test_path
        assert downloader.current_output_path == test_path

    def test_current_output_path_none_when_not_set(self):
        """Test current_output_path returns None when not downloading."""
        downloader = StreamDownloader("TestCreator")
        assert downloader.current_output_path is None

    def test_download_duration_during_download(self):
        """Test download_duration returns seconds during active download."""
        downloader = StreamDownloader("TestCreator")
        downloader._download_start_time = datetime.now()
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        downloader.download_thread = mock_thread
        duration = downloader.download_duration
        assert duration is not None
        assert duration >= 0

    def test_download_duration_none_when_not_downloading(self):
        """Test download_duration returns None when not downloading."""
        downloader = StreamDownloader("TestCreator")
        assert downloader.download_duration is None

    def test_download_duration_none_when_thread_dead(self):
        """Test download_duration returns None when thread finished."""
        downloader = StreamDownloader("TestCreator")
        downloader._download_start_time = datetime.now()
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        downloader.download_thread = mock_thread
        assert downloader.download_duration is None

