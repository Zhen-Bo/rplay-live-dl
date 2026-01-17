"""Tests for logger module."""

import logging
import os
import time
from pathlib import Path

import pytest

from core.logger import (
    _get_display_width,
    _truncate_to_width,
    _pad_to_width,
    _center_to_width,
    cleanup_old_logs,
    get_all_loggers,
    get_logs_dir,
    setup_logger,
    AlignedFormatter,
    ColoredAlignedFormatter,
    LazyRotatingFileHandler,
    LOGGER_NAME_WIDTH,
    LOG_LEVEL_WIDTH,
    LOG_COLORS,
)


class TestSetupLogger:
    """Tests for setup_logger function."""

    def test_creates_logger(self):
        """Test that setup_logger creates a logger."""
        logger = setup_logger("test_logger_1")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_logger_1"

    def test_logger_level(self):
        """Test that logger has correct level."""
        logger = setup_logger("test_logger_2", level=logging.DEBUG)
        assert logger.level == logging.DEBUG

    def test_no_duplicate_handlers(self):
        """Test that calling setup_logger twice doesn't add duplicate handlers."""
        logger1 = setup_logger("test_logger_3")
        handler_count = len(logger1.handlers)
        logger2 = setup_logger("test_logger_3")
        assert len(logger2.handlers) == handler_count

    def test_console_only(self):
        """Test creating a console-only logger."""
        logger = setup_logger("test_console_only", log_to_file=False)
        # Should have at least one handler (console)
        assert len(logger.handlers) >= 1


class TestGetLogsDir:
    """Tests for get_logs_dir function."""

    def test_returns_path(self):
        """Test that get_logs_dir returns a Path."""
        logs_dir = get_logs_dir()
        assert isinstance(logs_dir, Path)

    def test_directory_exists(self):
        """Test that logs directory exists."""
        logs_dir = get_logs_dir()
        assert logs_dir.exists()
        assert logs_dir.is_dir()


class TestCleanupOldLogs:
    """Tests for cleanup_old_logs function."""

    def test_cleanup_returns_count(self):
        """Test that cleanup returns a count."""
        result = cleanup_old_logs(retention_days=30)
        assert isinstance(result, int)
        assert result >= 0

    def test_removes_old_files(self, tmp_path, monkeypatch):
        """Test that files older than retention are removed."""
        from core import logger as logger_module

        # Patch logs directory
        monkeypatch.setattr(logger_module, "_logs_dir", tmp_path)

        # Create an old log file
        old_file = tmp_path / "old.log"
        old_file.write_text("old content")
        # Set modification time to 40 days ago
        old_time = time.time() - (40 * 24 * 60 * 60)
        os.utime(old_file, (old_time, old_time))

        # Create a recent log file
        recent_file = tmp_path / "recent.log"
        recent_file.write_text("recent content")

        removed = cleanup_old_logs(retention_days=30)

        assert removed == 1
        assert not old_file.exists()
        assert recent_file.exists()

    def test_keeps_recent_files(self, tmp_path, monkeypatch):
        """Test that recent files are kept."""
        from core import logger as logger_module

        monkeypatch.setattr(logger_module, "_logs_dir", tmp_path)

        # Create recent log files
        for i in range(3):
            log_file = tmp_path / f"recent_{i}.log"
            log_file.write_text(f"content {i}")

        removed = cleanup_old_logs(retention_days=30)

        assert removed == 0
        assert len(list(tmp_path.glob("*.log"))) == 3

    def test_handles_rotated_logs(self, tmp_path, monkeypatch):
        """Test that rotated log files (.log.1, .log.2) are also cleaned."""
        from core import logger as logger_module

        monkeypatch.setattr(logger_module, "_logs_dir", tmp_path)

        # Create old rotated log files
        for suffix in [".log", ".log.1", ".log.2"]:
            old_file = tmp_path / f"app{suffix}"
            old_file.write_text("old content")
            old_time = time.time() - (40 * 24 * 60 * 60)
            os.utime(old_file, (old_time, old_time))

        removed = cleanup_old_logs(retention_days=30)

        assert removed == 3


class TestGetAllLoggers:
    """Tests for get_all_loggers function."""

    def test_returns_list(self):
        """Test that get_all_loggers returns a list."""
        # First create a logger
        setup_logger("test_for_list")
        loggers = get_all_loggers()
        assert isinstance(loggers, list)
        assert "test_for_list" in loggers


class TestGetDisplayWidth:
    """Tests for _get_display_width function."""

    def test_ascii_characters(self):
        """Test width calculation for ASCII characters."""
        assert _get_display_width("hello") == 5
        assert _get_display_width("a") == 1
        assert _get_display_width("") == 0

    def test_cjk_characters(self):
        """Test width calculation for CJK (double-width) characters."""
        assert _get_display_width("你好") == 4  # 2 chars * 2 width
        assert _get_display_width("日本語") == 6  # 3 chars * 2 width

    def test_mixed_characters(self):
        """Test width calculation for mixed ASCII and CJK."""
        assert _get_display_width("hello你好") == 9  # 5 + 4

    def test_emoji_characters(self):
        """Test width calculation for emoji characters."""
        # Emoji width varies by implementation, test it doesn't crash
        width = _get_display_width("👍")
        assert isinstance(width, int)
        assert width >= 0

    def test_non_printable_characters(self):
        """Test width calculation handles non-printable characters."""
        # Non-printable should be treated as 0 width
        assert _get_display_width("\x00") == 0


class TestTruncateToWidth:
    """Tests for _truncate_to_width function."""

    def test_no_truncation_needed(self):
        """Test string shorter than max width is unchanged."""
        assert _truncate_to_width("hello", 10) == "hello"

    def test_exact_width(self):
        """Test string exactly at max width is unchanged."""
        assert _truncate_to_width("hello", 5) == "hello"

    def test_truncation_with_suffix(self):
        """Test string longer than max width is truncated with suffix."""
        result = _truncate_to_width("hello world", 8)
        assert result == "hello w…"
        assert _get_display_width(result) <= 8

    def test_truncation_cjk(self):
        """Test truncation with CJK characters."""
        result = _truncate_to_width("你好世界", 5)
        # Should truncate to fit within 5 width + suffix
        assert _get_display_width(result) <= 5

    def test_custom_suffix(self):
        """Test truncation with custom suffix."""
        result = _truncate_to_width("hello world", 8, suffix="...")
        assert result.endswith("...")
        assert _get_display_width(result) <= 8


class TestPadToWidth:
    """Tests for _pad_to_width function."""

    def test_pad_shorter_string(self):
        """Test padding a string shorter than target width."""
        result = _pad_to_width("hi", 5)
        assert result == "hi   "
        assert len(result) == 5

    def test_pad_exact_width(self):
        """Test string at exact width is unchanged."""
        result = _pad_to_width("hello", 5)
        assert result == "hello"

    def test_pad_longer_string(self):
        """Test string longer than target is not truncated."""
        result = _pad_to_width("hello world", 5)
        assert result == "hello world"

    def test_pad_cjk_string(self):
        """Test padding with CJK characters."""
        result = _pad_to_width("你好", 6)  # 你好 is 4 width
        assert _get_display_width(result) == 6


class TestCenterToWidth:
    """Tests for _center_to_width function."""

    def test_center_shorter_string(self):
        """Test centering a string shorter than target width."""
        result = _center_to_width("hi", 6)
        assert result == "  hi  "
        assert len(result) == 6

    def test_center_odd_padding(self):
        """Test centering with odd total padding."""
        result = _center_to_width("hi", 5)
        # 5 - 2 = 3 padding, left=1, right=2
        assert result == " hi  "

    def test_center_exact_width(self):
        """Test string at exact width is unchanged."""
        result = _center_to_width("hello", 5)
        assert result == "hello"

    def test_center_longer_string(self):
        """Test string longer than target is not truncated."""
        result = _center_to_width("hello world", 5)
        assert result == "hello world"


class TestAlignedFormatter:
    """Tests for AlignedFormatter class."""

    def test_centers_logger_name(self):
        """Test that formatter centers the logger name."""
        formatter = AlignedFormatter(
            fmt="%(name)s - %(message)s",
            datefmt="%Y-%m-%d",
        )
        record = logging.LogRecord(
            name="Test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test message",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        # Name should be centered within LOGGER_NAME_WIDTH
        assert "   Test   " in result or "  Test  " in result

    def test_centers_level_name(self):
        """Test that formatter centers the level name."""
        formatter = AlignedFormatter(
            fmt="%(levelname)s - %(message)s",
            datefmt="%Y-%m-%d",
        )
        record = logging.LogRecord(
            name="Test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test message",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        # INFO should be centered within LOG_LEVEL_WIDTH (8)
        assert "  INFO  " in result

    def test_truncates_long_name(self):
        """Test that long logger names are truncated."""
        formatter = AlignedFormatter(
            fmt="%(name)s",
            datefmt="%Y-%m-%d",
            name_width=5,
        )
        record = logging.LogRecord(
            name="VeryLongLoggerName",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        assert _get_display_width(result.strip()) <= 5


class TestColoredAlignedFormatter:
    """Tests for ColoredAlignedFormatter class."""

    def test_format_produces_output(self):
        """Test that formatter produces formatted output."""
        formatter = ColoredAlignedFormatter(
            fmt="%(asctime)s │ %(log_color)s%(levelname)s%(reset)s │ %(name)s │ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors=LOG_COLORS,
        )
        record = logging.LogRecord(
            name="Test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test message",
            args=(),
            exc_info=None,
        )
        result = formatter.format(record)
        assert "test message" in result
        assert "│" in result

    def test_preserves_original_name(self):
        """Test that original record name is preserved after formatting."""
        formatter = ColoredAlignedFormatter(
            fmt="%(name)s - %(message)s",
            datefmt="%Y-%m-%d",
            log_colors=LOG_COLORS,
        )
        record = logging.LogRecord(
            name="OriginalName",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        formatter.format(record)
        # Original name should be restored
        assert record.name == "OriginalName"


class TestLazyRotatingFileHandler:
    """Tests for LazyRotatingFileHandler class."""

    def test_no_file_created_on_init(self, tmp_path):
        """Test that no file is created during initialization."""
        log_file = tmp_path / "test.log"
        handler = LazyRotatingFileHandler(
            filename=str(log_file),
            maxBytes=1024,
            backupCount=3,
        )
        assert not log_file.exists()
        handler.close()

    def test_file_created_on_first_emit(self, tmp_path):
        """Test that file is created on first log emit."""
        log_file = tmp_path / "test.log"
        handler = LazyRotatingFileHandler(
            filename=str(log_file),
            maxBytes=1024,
            backupCount=3,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            name="Test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test message",
            args=(),
            exc_info=None,
        )
        handler.emit(record)

        assert log_file.exists()
        handler.close()

    def test_creates_parent_directory(self, tmp_path):
        """Test that handler creates parent directories if needed."""
        log_file = tmp_path / "subdir" / "nested" / "test.log"
        handler = LazyRotatingFileHandler(
            filename=str(log_file),
            maxBytes=1024,
            backupCount=3,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            name="Test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test message",
            args=(),
            exc_info=None,
        )
        handler.emit(record)

        assert log_file.exists()
        assert log_file.parent.exists()
        handler.close()

    def test_writes_log_content(self, tmp_path):
        """Test that log content is written to file."""
        log_file = tmp_path / "test.log"
        handler = LazyRotatingFileHandler(
            filename=str(log_file),
            maxBytes=1024,
            backupCount=3,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            name="Test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        handler.close()

        content = log_file.read_text()
        assert "hello world" in content
