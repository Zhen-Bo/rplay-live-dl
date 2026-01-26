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
from typing import Any, Dict, List, Optional

import colorlog
import wcwidth

__all__ = [
    "setup_logger",
    "cleanup_old_logs",
    "get_logs_dir",
    "get_all_loggers",
    "DEFAULT_LOG_LEVEL",
]

# Default log configuration
DEFAULT_LOG_LEVEL = logging.INFO


def _get_log_max_bytes() -> int:
    return int(os.getenv("LOG_MAX_SIZE_MB", "5")) * 1024 * 1024


def _get_log_backup_count() -> int:
    return int(os.getenv("LOG_BACKUP_COUNT", "5"))


def _get_log_retention_days() -> int:
    return int(os.getenv("LOG_RETENTION_DAYS", "30"))

# Logger name display width (for alignment)
# Set to match the longest logger name: "Downloader" = 10 characters
LOGGER_NAME_WIDTH = 10

# Log level display width (for alignment)
# Set to match the longest level name: "CRITICAL" = 8 characters
LOG_LEVEL_WIDTH = 8

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


def _truncate_to_width(text: str, max_width: int, suffix: str = "…") -> str:
    """
    Truncate a string to fit within a maximum display width.

    Args:
        text: The string to truncate
        max_width: Maximum display width allowed
        suffix: Suffix to append when truncating (default: "…")

    Returns:
        Truncated string that fits within max_width
    """
    current_width = _get_display_width(text)
    if current_width <= max_width:
        return text

    suffix_width = _get_display_width(suffix)
    target_width = max_width - suffix_width

    # Build truncated string character by character
    result = []
    width = 0
    for char in text:
        char_width = wcwidth.wcwidth(char)
        if char_width < 0:
            char_width = 0
        if width + char_width > target_width:
            break
        result.append(char)
        width += char_width

    return "".join(result) + suffix


def _pad_to_width(text: str, target_width: int) -> str:
    """
    Pad a string to a target display width.

    If the text is longer than target_width, it will be returned as-is
    (no truncation). If shorter, it will be padded with spaces.

    Args:
        text: The string to pad
        target_width: The desired display width

    Returns:
        String padded to at least target_width display width
    """
    current_width = _get_display_width(text)
    if current_width >= target_width:
        return text
    padding = target_width - current_width
    return text + " " * padding


def _center_to_width(text: str, target_width: int) -> str:
    """
    Center a string within a target display width.

    If the text is longer than target_width, it will be returned as-is
    (no truncation). If shorter, it will be padded with spaces on both sides.

    Args:
        text: The string to center
        target_width: The desired display width

    Returns:
        String centered within target_width display width
    """
    current_width = _get_display_width(text)
    if current_width >= target_width:
        return text
    total_padding = target_width - current_width
    left_padding = total_padding // 2
    right_padding = total_padding - left_padding
    return " " * left_padding + text + " " * right_padding


class AlignedFormatter(logging.Formatter):
    """
    A formatter for file output with centered logger names and levels.

    Centers logger names and level names to fixed widths for consistent alignment.
    """

    def __init__(
        self,
        fmt: str,
        datefmt: str,
        name_width: int = LOGGER_NAME_WIDTH,
        level_width: int = LOG_LEVEL_WIDTH,
    ):
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.name_width = name_width
        self.level_width = level_width

    def format(self, record: logging.LogRecord) -> str:
        """Format the record with centered logger name and level."""
        # Center the name
        name = record.name
        name = _truncate_to_width(name, self.name_width)
        name = _center_to_width(name, self.name_width)
        record.name = name

        # Center the level name
        levelname = record.levelname
        record.levelname = _center_to_width(levelname, self.level_width)

        return super().format(record)


class ColoredAlignedFormatter(colorlog.ColoredFormatter):
    """
    A colored formatter for console output with centered logger names and levels.

    Centers logger names and level names to fixed widths for consistent alignment.
    """

    def __init__(
        self,
        fmt: str,
        datefmt: str,
        log_colors: Dict[str, str],
        name_width: int = LOGGER_NAME_WIDTH,
        level_width: int = LOG_LEVEL_WIDTH,
    ):
        super().__init__(fmt=fmt, datefmt=datefmt, log_colors=log_colors)
        self.name_width = name_width
        self.level_width = level_width

    def format(self, record: logging.LogRecord) -> str:
        """Format the record with centered logger name and level."""
        # Center the name
        original_name = record.name
        name = _truncate_to_width(original_name, self.name_width)
        name = _center_to_width(name, self.name_width)
        record.name = name

        # Save original levelname for color lookup
        original_levelname = record.levelname

        # Let colorlog format with original levelname (for correct color lookup)
        result = super().format(record)

        # Restore original name
        record.name = original_name

        # Replace levelname with centered version in the output
        # The format is: "date │ <color>LEVELNAME<reset> │ name │ message"
        centered_levelname = _center_to_width(original_levelname, self.level_width)
        parts = result.split('│', 2)
        if len(parts) >= 2:
            parts[1] = parts[1].replace(original_levelname, centered_levelname, 1)
            result = '│'.join(parts)

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
        self.stream: Any = None
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

    # Format strings - level and name are centered by the formatter
    console_fmt = "%(asctime)s │ %(log_color)s%(levelname)s%(reset)s │ %(name)s │ %(message)s"
    file_fmt = "%(asctime)s │ %(levelname)s │ %(name)s │ %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    if log_to_console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_formatter = ColoredAlignedFormatter(
            fmt=console_fmt,
            datefmt=date_fmt,
            log_colors=LOG_COLORS,
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    if log_to_file:
        logs_dir = get_logs_dir()
        log_file = logs_dir / f"{name}.log"

        file_handler = LazyRotatingFileHandler(
            filename=str(log_file),
            maxBytes=_get_log_max_bytes(),
            backupCount=_get_log_backup_count(),
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_formatter = AlignedFormatter(
            fmt=file_fmt,
            datefmt=date_fmt,
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger


def cleanup_old_logs(retention_days: Optional[int] = None) -> int:
    """
    Remove log files older than the specified retention period.

    Args:
        retention_days: Number of days to retain log files (default from env or 30)

    Returns:
        int: Number of files removed
    """
    if retention_days is None:
        retention_days = _get_log_retention_days()
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
