"""Tests for logger module."""

import logging
import os
import time
from pathlib import Path

import pytest

from core.logger import (
    _display_width,
    bind,
    cleanup_old_logs,
    clip,
    get_logs_dir,
    setup_logger,
    AlignedFormatter,
    ColoredAlignedFormatter,
    LazyRotatingFileHandler,
    LOGGER_NAME_WIDTH,
    LOG_LEVEL_WIDTH,
    LOG_TEXT_MAX_COLUMNS,
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

    def test_uses_log_level_from_environment(self, monkeypatch):
        """Test LOG_LEVEL env var controls the default logger level."""
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")

        logger = setup_logger("test_logger_env_debug", log_to_file=False)

        assert logger.level == logging.DEBUG

    def test_invalid_log_level_falls_back_to_info(self, monkeypatch, tmp_path):
        """Test invalid LOG_LEVEL values fall back to INFO."""
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("LOG_LEVEL=LOUD\n", encoding="utf-8")

        logger = setup_logger("test_logger_invalid_level", log_to_file=False)

        assert logger.level == logging.INFO


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
        assert len(result.strip()) <= 5


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


class TestContextAdapter:
    """Tests for ContextAdapter and bind()."""

    def test_prefixes_message_with_context(self, caplog):
        """Test bind() prefixes a logged message with the bound context tag."""
        logger = logging.getLogger("test_context_adapter_prefix")
        logger.setLevel(logging.INFO)
        adapter = bind(logger, "SomeCreator")

        adapter.info("hello")

        assert caplog.records[-1].getMessage() == "[SomeCreator] hello"

    def test_returns_message_unchanged_when_context_is_empty(self, caplog):
        """Test bind() with an empty context leaves the message unprefixed."""
        logger = logging.getLogger("test_context_adapter_empty_context")
        logger.setLevel(logging.INFO)
        adapter = bind(logger, "")

        adapter.info("hello")

        assert caplog.records[-1].getMessage() == "hello"

    def test_adapter_forwards_level_filtering(self, caplog):
        """Test the adapter forwards the underlying logger's level filtering."""
        logger = logging.getLogger("test_context_adapter_level_filter")
        logger.setLevel(logging.WARNING)
        adapter = bind(logger, "SomeCreator")

        adapter.info("should be filtered")
        adapter.warning("should be logged")

        messages = [record.getMessage() for record in caplog.records]
        assert "[SomeCreator] should be filtered" not in messages
        assert "[SomeCreator] should be logged" in messages

    def test_exception_through_adapter_keeps_traceback(self, caplog):
        """Test .exception() through the adapter keeps exc_info and the context prefix."""
        logger = logging.getLogger("test_context_adapter_exception")
        logger.setLevel(logging.INFO)
        adapter = bind(logger, "SomeCreator")

        try:
            raise ValueError("boom")
        except ValueError:
            adapter.exception("boom")

        record = caplog.records[-1]
        assert record.exc_info is not None
        assert record.getMessage() == "[SomeCreator] boom"


class TestClip:
    """Tests for clip() and _display_width()."""

    def test_short_text_is_returned_unchanged(self):
        """Test text under the column budget passes through unchanged."""
        assert clip("耳舐めASMR") == "耳舐めASMR"

    def test_ascii_text_is_clipped_to_the_budget(self):
        """Test ASCII text over the budget is clipped to exactly 40 columns."""
        result = clip("a" * 60)
        assert len(result) == 40
        assert result.endswith("…")

    def test_cjk_counts_as_two_columns(self):
        """Test CJK characters count as two columns each, unlike len()."""
        assert _display_width("耳舐め") == 6
        assert len("耳舐め") == 3

    def test_clipped_result_never_exceeds_the_budget_in_columns(self):
        """Test a clipped CJK string never exceeds the column budget."""
        result = clip("配信" * 40)
        assert _display_width(result) <= 40

    def test_custom_budget_is_respected(self):
        """Test a custom columns budget is honored instead of the default."""
        result = clip("a" * 30, columns=10)
        assert _display_width(result) <= 10

    def test_exact_budget_is_not_clipped(self):
        """Test text exactly at the budget is not clipped."""
        result = clip("a" * 40)
        assert result == "a" * 40
        assert "…" not in result

    def test_returns_empty_when_budget_cannot_fit_the_suffix(self):
        """Test clip() returns empty text when the budget can't even fit the suffix."""
        assert clip("界", columns=0) == ""

    def test_never_exceeds_budget_for_any_small_budget(self):
        """Test clip() never exceeds a small columns budget, for every budget 0-5."""
        for n in range(6):
            result = clip("配信配信配信", columns=n)
            assert _display_width(result) <= n, f"columns={n} produced {result!r}"
