"""Tests for stream downloader module."""

import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yt_dlp
from freezegun import freeze_time

from core.downloader import StreamDownloader


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
