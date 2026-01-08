"""
Centralized logging module for rplay-live-dl.

Provides a unified logging system with:
- Console and file output
- Log rotation with size limits
- Automatic cleanup of old log files
- Consistent formatting across all modules
"""

import logging
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional

# Default log configuration
DEFAULT_LOG_LEVEL = logging.INFO
DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5MB per log file
DEFAULT_BACKUP_COUNT = 5
DEFAULT_LOG_RETENTION_DAYS = 30

# Global logs directory
_logs_dir: Optional[Path] = None


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

    # Create detailed formatter with more context
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(name)-15s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if log_to_console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    if log_to_file:
        logs_dir = get_logs_dir()
        log_file = logs_dir / f"{name}.log"

        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=DEFAULT_MAX_BYTES,
            backupCount=DEFAULT_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
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
