"""Tests for logger module."""

import logging
import os
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from core.logger import (
    DEFAULT_LOG_LEVEL,
    LOG_COLORS,
    AlignedFormatter,
    ColoredAlignedFormatter,
    _display_width,
    bind,
    cleanup_old_logs,
    clip,
    configure_logging,
    get_logs_dir,
    is_ytdlp_internal_logging_enabled,
    setup_logger,
)
from models.env import EnvConfig


class TestSetupLogger:
    """Tests for setup_logger function."""

    def test_creates_logger(self, monkeypatch):
        """Test that setup_logger creates a logger at the default level."""
        import core.logger as logger_module

        monkeypatch.setattr(logger_module, "_configured_log_level", DEFAULT_LOG_LEVEL)
        logger = setup_logger("test_logger_1", log_to_file=False)
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_logger_1"
        assert logger.level == logging.INFO
        # Console-only logger still has at least the console handler
        assert len(logger.handlers) >= 1

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

    def test_configure_logging_applies_level_and_ytdlp_flag(self, monkeypatch):
        """Test configure_logging sets both log level and yt-dlp internal flag."""
        import core.logger as logger_module

        monkeypatch.setattr(logger_module, "_configured_log_level", DEFAULT_LOG_LEVEL)
        monkeypatch.setattr(logger_module, "_configured_ytdlp_internal", False)

        assert is_ytdlp_internal_logging_enabled() is False

        configure_logging(
            EnvConfig(
                user_oid="oid",
                refresh_token="token",
                log_level="DEBUG",
                log_ytdlp_internal=True,
            )
        )
        logger = setup_logger("test_logger_env_debug", log_to_file=False)

        assert logger.level == logging.DEBUG
        assert is_ytdlp_internal_logging_enabled() is True

    def test_configure_logging_applies_rotation_settings(self, tmp_path, monkeypatch):
        """Validated EnvConfig values control handlers and cleanup defaults."""
        import core.logger as logger_module

        monkeypatch.setattr(logger_module, "_logs_dir", tmp_path)
        config = EnvConfig(
            user_oid="oid",
            refresh_token="token",
            log_max_size_mb=7,
            log_backup_count=3,
            log_retention_days=42,
        )
        configure_logging(config)

        logger_name = "test_logger_env_rotation"
        logger = setup_logger(logger_name, log_to_console=False)
        try:
            handler = next(
                h for h in logger.handlers if isinstance(h, RotatingFileHandler)
            )
            assert handler.maxBytes == 7 * 1024 * 1024
            assert handler.backupCount == 3
        finally:
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)

        # The retention value is also sourced from the same validated config.
        old_file = tmp_path / "old.log"
        old_file.write_text("old")
        old_time = time.time() - (43 * 24 * 60 * 60)
        os.utime(old_file, (old_time, old_time))
        assert cleanup_old_logs() == 1


class TestGetLogsDir:
    """Tests for get_logs_dir function."""

    def test_directory_exists(self):
        """Test that get_logs_dir returns an existing directory Path."""
        logs_dir = get_logs_dir()
        assert isinstance(logs_dir, Path)
        assert logs_dir.exists()
        assert logs_dir.is_dir()


class TestCleanupOldLogs:
    """Tests for cleanup_old_logs function."""

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

    def test_preserves_original_name(self):
        """Test that formatting produces output and restores the original record name."""
        formatter = ColoredAlignedFormatter(
            fmt="%(asctime)s │ %(log_color)s%(levelname)s%(reset)s │ %(name)s │ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors=LOG_COLORS,
        )
        record = logging.LogRecord(
            name="OriginalName",
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
        # Original name should be restored
        assert record.name == "OriginalName"


class TestRotatingFileHandlerLazyCreation:
    """Regression tests for the file handler setup_logger constructs.

    LazyRotatingFileHandler used to hand-roll lazy creation by skipping
    FileHandler.__init__, which left `delay`/`errors` unset and crashed
    stdlib's doRollover() on the first rotation. Replaced with stock
    RotatingFileHandler(delay=True), which gives lazy creation for free.

    These go through setup_logger() itself (not a hand-built handler) so a
    regression back to the old broken class actually fails them: this test
    module imports setup_logger at module scope, before the autouse
    disable_file_logging fixture monkeypatches the module attribute, so the
    real function under test runs here regardless of that fixture.
    """

    def test_no_file_created_until_first_emit(self, tmp_path, monkeypatch):
        from core import logger as logger_module

        monkeypatch.setattr(logger_module, "_logs_dir", tmp_path)

        logger_name = "test_lazy_creation_regression"
        logger = setup_logger(logger_name, log_to_file=True, log_to_console=False)
        try:
            log_file = tmp_path / f"{logger_name}.log"
            assert not log_file.exists()

            logger.info("hello world")

            assert "hello world" in log_file.read_text()
        finally:
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)

    def test_rollover_does_not_crash_or_lose_messages(
        self, tmp_path, monkeypatch, capsys
    ):
        from core import logger as logger_module

        monkeypatch.setattr(logger_module, "_logs_dir", tmp_path)

        logger_name = "test_rollover_regression"
        logger = setup_logger(logger_name, log_to_file=True, log_to_console=False)
        try:
            file_handler = next(
                (h for h in logger.handlers if isinstance(h, RotatingFileHandler)),
                None,
            )
            assert file_handler is not None

            # Shrink only the rotation threshold so rollover triggers almost
            # immediately; keep the real construction (incl. delay=True) from
            # setup_logger so this exercises the actual bug site.
            file_handler.maxBytes = 50

            messages = ["message one", "message two", "message three"]
            for msg in messages:
                logger.info(msg)

            assert (tmp_path / f"{logger_name}.log.1").exists()

            all_content = "".join(
                f.read_text() for f in tmp_path.glob(f"{logger_name}.log*")
            )
            for msg in messages:
                assert msg in all_content

            # logging.Handler.handleError() prints "--- Logging error ---" to
            # stderr on unhandled exceptions inside emit(); its absence is the
            # regression check for the AttributeError this bug used to raise.
            assert "--- Logging error ---" not in capsys.readouterr().err
        finally:
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)


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

    @pytest.mark.parametrize("text", ["a" * 40, "耳舐めASMR"])
    def test_text_within_budget_is_not_clipped(self, text):
        """Test text at or under the budget passes through unchanged."""
        result = clip(text)
        assert result == text
        assert "…" not in result

    def test_ascii_text_is_clipped_to_the_budget(self):
        """Test ASCII text over the budget is clipped to exactly 40 columns."""
        result = clip("a" * 60)
        assert len(result) == 40
        assert result.endswith("…")

    def test_cjk_counts_as_two_columns(self):
        """Test CJK characters count as two columns each, unlike len()."""
        assert _display_width("耳舐め") == 6
        assert len("耳舐め") == 3

    @pytest.mark.parametrize(
        "text,columns",
        [
            *[("配信配信配信", n) for n in range(6)],
            ("配信" * 40, 40),
            ("a" * 30, 10),
            ("界", 0),
        ],
    )
    def test_never_exceeds_budget(self, text, columns):
        """Test clip() never exceeds the columns budget, and is empty at zero."""
        result = clip(text, columns=columns)
        assert (
            _display_width(result) <= columns
        ), f"columns={columns} produced {result!r}"
        if columns == 0:
            assert result == ""
