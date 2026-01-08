"""Tests for logger module."""

import tempfile
from pathlib import Path
from datetime import datetime, timedelta
import logging

import pytest

from core.logger import (
    cleanup_old_logs,
    get_all_loggers,
    get_logs_dir,
    setup_logger,
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


class TestGetAllLoggers:
    """Tests for get_all_loggers function."""

    def test_returns_list(self):
        """Test that get_all_loggers returns a list."""
        # First create a logger
        setup_logger("test_for_list")
        loggers = get_all_loggers()
        assert isinstance(loggers, list)
        assert "test_for_list" in loggers
