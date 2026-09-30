"""Centralized logging for rplay-live-dl."""

import logging
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

import colorlog
import wcwidth

from core.constants import (
    DEFAULT_LOG_BACKUP_COUNT,
)
from core.constants import DEFAULT_LOG_LEVEL as DEFAULT_LOG_LEVEL_NAME
from core.constants import (
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

DEFAULT_LOG_LEVEL = logging.getLevelNamesMapping()[DEFAULT_LOG_LEVEL_NAME]

# Set by configure_logging() after EnvConfig validation, the single place
# where environment values are parsed. Handlers do not read the environment.
_configured_log_level: int = DEFAULT_LOG_LEVEL
_configured_ytdlp_internal: bool = DEFAULT_LOG_YTDLP_INTERNAL
_configured_log_max_size_mb: int = DEFAULT_LOG_MAX_SIZE_MB
_configured_log_backup_count: int = DEFAULT_LOG_BACKUP_COUNT
_configured_log_retention_days: int = DEFAULT_LOG_RETENTION_DAYS


def _get_log_max_bytes() -> int:
    return _configured_log_max_size_mb * 1024 * 1024


def _get_log_backup_count() -> int:
    return _configured_log_backup_count


def _get_log_retention_days() -> int:
    return _configured_log_retention_days


def configure_logging(env: "EnvConfig") -> None:
    global _configured_log_level, _configured_ytdlp_internal
    global _configured_log_max_size_mb, _configured_log_backup_count
    global _configured_log_retention_days
    _configured_log_level = logging.getLevelNamesMapping()[env.log_level]
    _configured_ytdlp_internal = env.log_ytdlp_internal
    _configured_log_max_size_mb = env.log_max_size_mb
    _configured_log_backup_count = env.log_backup_count
    _configured_log_retention_days = env.log_retention_days


def is_ytdlp_internal_logging_enabled() -> bool:
    return _configured_ytdlp_internal


def _resolve_log_level(level: Optional[int]) -> int:
    if level is not None:
        return level
    return _configured_log_level


# Longest logger name: "Downloader"
LOGGER_NAME_WIDTH = 10

# Longest level name: "CRITICAL"
LOG_LEVEL_WIDTH = 8

# Measured live: with this cap every line fits in ~110 columns, while 10 of 16
# real titles would otherwise wrap a 120-column terminal.
LOG_TEXT_MAX_COLUMNS = 40

_logs_dir: Optional[Path] = None

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
    """CJK characters and emoji occupy two columns each, so len() understates."""
    total = 0
    for char in text:
        width = wcwidth.wcwidth(char)
        # wcwidth returns -1 for unprintable characters.
        total += width if width and width > 0 else 0
    return total


def clip(text: str, columns: int = LOG_TEXT_MAX_COLUMNS, suffix: str = "…") -> str:
    """
    Clip text to a terminal-column budget, keeping the front.

    The tail of a stream title is usually the creator name or a timestamp,
    both already elsewhere on the log line.
    """
    if _display_width(text) <= columns:
        return text

    budget = columns - _display_width(suffix)
    if budget < 0:
        # No room for the suffix, and the result must never exceed `columns`.
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
    def process(self, msg: Any, kwargs: Any) -> Any:
        context = (self.extra or {}).get("context")
        return (f"[{context}] {msg}" if context else msg), kwargs


def bind(logger: logging.Logger, context: str) -> logging.LoggerAdapter:
    """Prefix every message with ``[context]`` so one recording can be grepped."""
    return ContextAdapter(logger, {"context": context})


class AlignedFormatter(logging.Formatter):
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
        record.name = _fit(record.name, self.name_width)
        record.levelname = _fit(record.levelname, self.level_width)
        return super().format(record)


class ColoredAlignedFormatter(colorlog.ColoredFormatter):
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
        original_name = record.name
        record.name = _fit(original_name, self.name_width)

        # colorlog picks the colour by looking up record.levelname, so it has to
        # stay unpadded until after formatting.
        original_levelname = record.levelname
        result = super().format(record)
        record.name = original_name

        # Format is: "date │ <color>LEVELNAME<reset> │ name │ message"
        centered_levelname = _fit(original_levelname, self.level_width)
        parts = result.split("│", 2)
        if len(parts) >= 2:
            parts[1] = parts[1].replace(original_levelname, centered_levelname, 1)
            result = "│".join(parts)

        return result


def get_logs_dir() -> Path:
    """Return the logs directory, creating it on first use."""
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
    Create a logger with colorized console output and plain-text file output.

    name is also the log filename. level falls back to configure_logging(),
    then DEFAULT_LOG_LEVEL.
    """
    resolved_level = _resolve_log_level(level)
    logger = logging.getLogger(name)
    logger.setLevel(resolved_level)

    if logger.handlers:
        for handler in logger.handlers:
            handler.setLevel(resolved_level)
        return logger

    # Level and name are centered by the formatter.
    console_fmt = (
        "%(asctime)s │ %(log_color)s%(levelname)s%(reset)s │ %(name)s │ %(message)s"
    )
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

        # delay=True defers file creation until the first record. It does not
        # create directories, so the parent relies on get_logs_dir() above.
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
    """Remove log files older than the retention period and return the count."""
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
            pass

    return removed_count
