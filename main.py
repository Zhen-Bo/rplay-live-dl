"""
rplay-live-dl - Automated RPlay live stream downloader.

Entry point for the application.
"""

import logging
import sys
import tomllib
from pathlib import Path

from dotenv import load_dotenv

from core.downloader import StreamDownloader
from core.env import EnvConfigError, load_env
from core.logger import cleanup_old_logs, setup_logger
from core.scheduler import run_scheduler


def _read_version() -> str:
    pyproject_path = Path(__file__).parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    return data["tool"]["poetry"]["version"]


__version__ = _read_version()


def _warn_about_orphaned_downloads(logger: logging.Logger) -> None:
    """
    List files left behind by interrupted recordings.

    Nothing else in the codebase ever looks at these again, so without this
    they accumulate silently. yt-dlp's HLS downloader can leave *.part,
    *.ytdl and *.part-Frag* behind on a kill; unmerged session .ts files stay
    when a merge fails or the process dies first.
    """
    # ponytail: report-only. Auto-merging .ts is deferred until shutdown is
    # trustworthy, and .part fragments may be truncated mid-write, so merging
    # those would produce broken video.
    archive = Path.cwd() / StreamDownloader.ARCHIVE_DIR
    if not archive.is_dir():
        return

    # *.part* covers .part, .part-FragN and .part-FragN.part in one pattern,
    # so the three patterns are disjoint and need no dedup.
    patterns = ("[0-9]*_*.ts", "*.part*", "*.ytdl")
    orphans = sorted(path for pattern in patterns for path in archive.glob(f"*/{pattern}"))
    if not orphans:
        return

    logger.warning(f"Found {len(orphans)} file(s) left behind by interrupted recordings:")
    for path in orphans[:10]:
        logger.warning(f"  {path.relative_to(archive)}")
    if len(orphans) > 10:
        logger.warning(f"  ... and {len(orphans) - 10} more")


def main() -> None:
    """Main entry point for the application."""
    load_dotenv()
    logger = setup_logger("Main")

    # Cleanup old log files on startup
    try:
        removed = cleanup_old_logs()
        if removed > 0:
            logger.info(f"Cleaned up {removed} old log file(s)")
    except Exception as e:
        logger.warning(f"Failed to cleanup old logs: {e}")

    _warn_about_orphaned_downloads(logger)

    # Load environment configuration
    try:
        env = load_env()
        logger.info("Environment configuration loaded successfully")
    except EnvConfigError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except ValueError as e:
        logger.error(f"Invalid configuration: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Unexpected error loading configuration: {e}")
        sys.exit(1)

    # Start the scheduler
    try:
        run_scheduler(env=env, logger=logger, version=__version__)
    except Exception as e:
        logger.exception(f"Scheduler error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
