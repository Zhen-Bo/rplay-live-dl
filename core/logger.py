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
from typing import TYPE_CHECKING, Any, Dict, Optional

import colorlog
import wcwidth

from core.constants import (
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_LEVEL as DEFAULT_LOG_LEVEL_NAME,
    DEFAULT_LOG_MAX_SIZE_MB,
    DEFAULT_LOG_RETENTION_DAYS,
    DEFAULT_LOG_YTDLP_INTERNAL,
)

if TYPE_CHECKING:
    from models.env import EnvConfig

__all__ = [
    "setup_logger",
    "configure_logging",
    "is_ytdlp_internal_logging_enabled",
    "cleanup_old_logs",
    "get_logs_dir",
    "bind",
    "clip",
    "DEFAULT_LOG_LEVEL",
    "LOG_TEXT_MAX_COLUMNS",
]

# Default log level constant (int) derived from the shared name default.
DEFAULT_LOG_LEVEL = logging.getLevelNamesMapping()[DEFAULT_LOG_LEVEL_NAME]

# Process-wide settings; start at constants defaults, overridden by configure_logging.
_configured_log_level: int = DEFAULT_LOG_LEVEL
_configured_ytdlp_internal: bool = DEFAULT_LOG_YTDLP_INTERNAL


def _get_log_max_bytes() -> int:
    return int(os.getenv("LOG_MAX_SIZE_MB", str(DEFAULT_LOG_MAX_SIZE_MB))) * 1024 * 1024


def _get_log_backup_count() -> int:
    return int(os.getenv("LOG_BACKUP_COUNT", str(DEFAULT_LOG_BACKUP_COUNT)))


def _get_log_retention_days() -> int:
    return int(os.getenv("LOG_RETENTION_DAYS", str(DEFAULT_LOG_RETENTION_DAYS)))


def configure_logging(env: "EnvConfig") -> None:
    """Apply validated env log settings as process-wide logging configuration."""
    global _configured_log_level, _configured_ytdlp_internal
    _configured_log_level = logging.getLevelNamesMapping()[env.log_level]
    _configured_ytdlp_internal = env.log_ytdlp_internal


def is_ytdlp_internal_logging_enabled() -> bool:
    """Return whether yt-dlp internal debug chatter should be surfaced."""
    return _configured_ytdlp_internal


def _resolve_log_level(level: Optional[int]) -> int:
    """Resolve log level from explicit arg or configure_logging defaults."""
    if level is not None:
        return level
    return _configured_log_level

# Logger name display width (for alignment)
# Set to match the longest logger name: "Downloader" = 10 characters
LOGGER_NAME_WIDTH = 10

# Log level display width (for alignment)
# Set to match the longest level name: "CRITICAL" = 8 characters
LOG_LEVEL_WIDTH = 8

# Column budget for user-controlled text (stream titles) inside a log message.
# Measured live: with this cap every line fits in ~110 columns, while 10 of 16
# real titles would otherwise wrap a 120-column terminal.
LOG_TEXT_MAX_COLUMNS = 40

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


def _fit(text: str, width: int) -> str:
    """Center text in a fixed-width column, truncating anything that overflows."""
    return f"{text:^{width}.{width}}"


def _display_width(text: str) -> int:
    """
    Terminal columns occupied by text.

    CJK characters and emoji occupy two columns each, so len() understates them
    badly: a 40-character Japanese title is 80 columns wide.
    """
    total = 0
    for char in text:
        width = wcwidth.wcwidth(char)
        # wcwidth returns -1 for unprintable characters; they occupy nothing.
        total += width if width and width > 0 else 0
    return total


def clip(text: str, columns: int = LOG_TEXT_MAX_COLUMNS, suffix: str = "…") -> str:
    """
    Clip text to a terminal-column budget, keeping the front.

    Stream titles are front-loaded: the bracketed category comes first and the
    tail is usually the creator name or a timestamp, both of which already
    appear elsewhere on the log line. Keeping the head is what stays useful.
    """
    if _display_width(text) <= columns:
        return text

    budget = columns - _display_width(suffix)
    if budget < 0:
        # No room for the suffix itself; the contract is that the result never
        # exceeds `columns`, so nothing can be shown.
        return ""
    kept, used = [], 0
    for char in text:
        width = wcwidth.wcwidth(char)
        width = width if width and width > 0 else 0
        if used + width > budget:
            break
        kept.append(char)
        used += width
    return "".join(kept) + suffix


class ContextAdapter(logging.LoggerAdapter):
    """Prefix every message with a stable ``[context]`` tag."""

    def process(self, msg: Any, kwargs: Any) -> Any:
        context = (self.extra or {}).get("context")
        return (f"[{context}] {msg}" if context else msg), kwargs


def bind(logger: logging.Logger, context: str) -> logging.LoggerAdapter:
    """
    Bind a logger to a stable context tag, usually the creator name.

    Every message logged through the returned adapter is prefixed with
    ``[context]``, so one recording can be followed with a single grep.
    """
    return ContextAdapter(logger, {"context": context})


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
        record.name = _fit(record.name, self.name_width)
        record.levelname = _fit(record.levelname, self.level_width)
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
        original_name = record.name
        record.name = _fit(original_name, self.name_width)

        # colorlog picks the colour by looking up record.levelname, so it has to
        # stay unpadded until after formatting.
        original_levelname = record.levelname
        result = super().format(record)
        record.name = original_name

        # Replace levelname with centered version in the output
        # The format is: "date │ <color>LEVELNAME<reset> │ name │ message"
        centered_levelname = _fit(original_levelname, self.level_width)
        parts = result.split('│', 2)
        if len(parts) >= 2:
            parts[1] = parts[1].replace(original_levelname, centered_levelname, 1)
            result = '│'.join(parts)

        return result


def get_logs_dir() -> Path:
    """Get the logs directory, creating it if necessary."""
    global _logs_dir
    if _logs_dir is None:
        _logs_dir = Path(__file__).parent.parent / "logs"
        _logs_dir.mkdir(exist_ok=True)
    return _logs_dir


def setup_logger(
    name: str,
    level: Optional[int] = None,
    log_to_file: bool = True,
    log_to_console: bool = True,
) -> logging.Logger:
    """
    Configure and create a logger instance with both console and file output.

    Console output is colorized for better readability.
    File output uses plain text without colors.

    Args:
        name: Logger name (used for both identification and log filename)
        level: Logging level. When omitted, uses configure_logging() then DEFAULT_LOG_LEVEL.
        log_to_file: Whether to output to file (default: True)
        log_to_console: Whether to output to console (default: True)

    Returns:
        logging.Logger: Configured logger instance
    """
    resolved_level = _resolve_log_level(level)
    logger = logging.getLogger(name)
    logger.setLevel(resolved_level)

    # Avoid adding duplicate handlers
    if logger.handlers:
        for handler in logger.handlers:
            handler.setLevel(resolved_level)
        return logger

    # Format strings - level and name are centered by the formatter
    console_fmt = "%(asctime)s │ %(log_color)s%(levelname)s%(reset)s │ %(name)s │ %(message)s"
    file_fmt = "%(asctime)s │ %(levelname)s │ %(name)s │ %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    if log_to_console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(resolved_level)
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

        # ponytail: delay=True gives lazy file creation for free (stdlib);
        # relies on setup_logger's get_logs_dir() call above to mkdir the
        # parent, since delay=True does not create directories.
        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=_get_log_max_bytes(),
            backupCount=_get_log_backup_count(),
            encoding="utf-8",
            delay=True,
        )
        file_handler.setLevel(resolved_level)
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
