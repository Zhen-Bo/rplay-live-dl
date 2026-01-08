"""
Centralized logging module for rplay-live-dl.

Provides a unified logging system with:
- Console and file output with colored formatting
- Log rotation with size limits
- Automatic cleanup of old log files
- Consistent formatting across all modules
- Lazy file creation (only when first log is written)
"""

import logging
import os
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional

import colorlog
import wcwidth

__all__ = [
    "setup_logger",
    "cleanup_old_logs",
    "get_logs_dir",
    "get_all_loggers",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_LOG_RETENTION_DAYS",
]

# Default log configuration
DEFAULT_LOG_LEVEL = logging.INFO
DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5MB per log file
DEFAULT_BACKUP_COUNT = 5
DEFAULT_LOG_RETENTION_DAYS = 30

# Logger name display width (for alignment)
# Set to 28 to accommodate "Downloader-" prefix + CJK creator names
LOGGER_NAME_WIDTH = 28

# Global logs directory
_logs_dir: Optional[Path] = None

# Color scheme for different log levels
LOG_COLORS = {
    "DEBUG": "cyan",
    "INFO": "green",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "red,bg_white",
}


def _get_display_width(text: str) -> int:
    """
    Calculate the display width of a string using wcwidth.

    This properly handles East Asian characters (CJK) and emojis
    that take multiple terminal columns.

    Args:
        text: The string to measure

    Returns:
        The display width in terminal columns
    """
    width = 0
    for char in text:
        char_width = wcwidth.wcwidth(char)
        # wcwidth returns -1 for non-printable characters, treat as 0
        if char_width < 0:
            char_width = 0
        width += char_width
    return width


def _pad_to_width(text: str, target_width: int) -> str:
    """
    Pad a string to a target display width.

    Args:
        text: The string to pad
        target_width: The desired display width

    Returns:
        The padded string
    """
    current_width = _get_display_width(text)
    if current_width >= target_width:
        return text
    padding = target_width - current_width
    return text + " " * padding


class AlignedFormatter(logging.Formatter):
    """
    A formatter that properly aligns logger names with CJK/emoji characters.
    """

    def __init__(self, fmt: str, datefmt: str, name_width: int = LOGGER_NAME_WIDTH):
        # Use a placeholder for the name field
        self._name_width = name_width
        self._base_fmt = fmt
        super().__init__(fmt=fmt, datefmt=datefmt)

    def format(self, record: logging.LogRecord) -> str:
        # Pad the logger name to the target width
        original_name = record.name
        record.name = _pad_to_width(original_name, self._name_width)
        result = super().format(record)
        record.name = original_name  # Restore original
        return result


class ColoredAlignedFormatter(colorlog.ColoredFormatter):
    """
    A colored formatter that properly aligns logger names with CJK/emoji characters.
    """

    def __init__(
        self,
        fmt: str,
        datefmt: str,
        log_colors: dict,
        name_width: int = LOGGER_NAME_WIDTH,
    ):
        self._name_width = name_width
        super().__init__(fmt=fmt, datefmt=datefmt, log_colors=log_colors)

    def format(self, record: logging.LogRecord) -> str:
        # Pad the logger name to the target width
        original_name = record.name
        record.name = _pad_to_width(original_name, self._name_width)
        result = super().format(record)
        record.name = original_name  # Restore original
        return result


class LazyRotatingFileHandler(RotatingFileHandler):
    """
    A RotatingFileHandler that delays file creation until the first log is written.

    This prevents empty log files from being created for loggers that never log anything.
    """

    def __init__(
        self,
        filename: str,
        maxBytes: int = 0,
        backupCount: int = 0,
        encoding: Optional[str] = None,
    ) -> None:
        """
        Initialize the handler without creating the file.

        Args:
            filename: Path to the log file
            maxBytes: Maximum file size before rotation
            backupCount: Number of backup files to keep
            encoding: File encoding
        """
        self._log_filename = filename
        self._file_created = False
        self._maxBytes = maxBytes
        self._backupCount = backupCount
        self._encoding = encoding
        # Initialize base Handler only, not StreamHandler or FileHandler
        # This avoids file creation
        logging.Handler.__init__(self)
        # Set attributes needed by RotatingFileHandler
        self.mode = "a"
        self.encoding = encoding
        self.maxBytes = maxBytes
        self.backupCount = backupCount
        # Initialize stream to None to prevent AttributeError on shutdown
        self.stream = None
        self.baseFilename = os.path.abspath(filename)

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a record, creating the file if necessary.

        Args:
            record: The log record to emit
        """
        if not self._file_created:
            self._create_file()
        super().emit(record)

    def _create_file(self) -> None:
        """Create the log file and properly initialize file handling."""
        # Ensure directory exists
        log_dir = os.path.dirname(self._log_filename)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        # Open the stream
        self.stream = open(
            self._log_filename,
            mode=self.mode,
            encoding=self.encoding,
        )
        self._file_created = True


def get_logs_dir() -> Path:
    """Get the logs directory, creating it if necessary."""
    global _logs_dir
    if _logs_dir is None:
        _logs_dir = Path(__file__).parent.parent / "logs"
        _logs_dir.mkdir(exist_ok=True)
    return _logs_dir


def setup_logger(
    name: str,
    level: int = DEFAULT_LOG_LEVEL,
    log_to_file: bool = True,
    log_to_console: bool = True,
) -> logging.Logger:
    """
    Configure and create a logger instance with both console and file output.

    Console output is colorized for better readability.
    File output uses plain text without colors.

    Args:
        name: Logger name (used for both identification and log filename)
        level: Logging level (default: INFO)
        log_to_file: Whether to output to file (default: True)
        log_to_console: Whether to output to console (default: True)

    Returns:
        logging.Logger: Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid adding duplicate handlers
    if logger.handlers:
        return logger

    # Format strings - name field is handled by custom formatter
    console_fmt = "%(asctime)s │ %(name)s │ %(log_color)s%(levelname)-8s%(reset)s │ %(message)s"
    file_fmt = "%(asctime)s │ %(name)s │ %(levelname)-8s │ %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    if log_to_console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_formatter = ColoredAlignedFormatter(
            fmt=console_fmt,
            datefmt=date_fmt,
            log_colors=LOG_COLORS,
            name_width=LOGGER_NAME_WIDTH,
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    if log_to_file:
        logs_dir = get_logs_dir()
        log_file = logs_dir / f"{name}.log"

        file_handler = LazyRotatingFileHandler(
            filename=str(log_file),
            maxBytes=DEFAULT_MAX_BYTES,
            backupCount=DEFAULT_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_formatter = AlignedFormatter(
            fmt=file_fmt,
            datefmt=date_fmt,
            name_width=LOGGER_NAME_WIDTH,
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger


def cleanup_old_logs(retention_days: int = DEFAULT_LOG_RETENTION_DAYS) -> int:
    """
    Remove log files older than the specified retention period.

    Args:
        retention_days: Number of days to retain log files (default: 30)

    Returns:
        int: Number of files removed
    """
    logs_dir = get_logs_dir()
    cutoff_date = datetime.now() - timedelta(days=retention_days)
    removed_count = 0

    for log_file in logs_dir.glob("*.log*"):
        try:
            file_mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
            if file_mtime < cutoff_date:
                log_file.unlink()
                removed_count += 1
        except OSError:
            # Skip files that can't be accessed
            pass

    return removed_count


def get_all_loggers() -> List[str]:
    """
    Get names of all active loggers.

    Returns:
        list[str]: List of logger names
    """
    return list(logging.Logger.manager.loggerDict.keys())
