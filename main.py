"""
rplay-live-dl - Automated RPlay live stream downloader.

Entry point for the application.
"""

import sys

from core.env import EnvConfigError, load_env
from core.logger import cleanup_old_logs, setup_logger
from core.scheduler import run_scheduler

__version__ = "1.2.0"


def main() -> None:
    """Main entry point for the application."""
    logger = setup_logger("Main")

    # Cleanup old log files on startup
    try:
        removed = cleanup_old_logs()
        if removed > 0:
            logger.info(f"Cleaned up {removed} old log file(s)")
    except Exception as e:
        logger.warning(f"Failed to cleanup old logs: {e}")

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
        logger.error(f"Unexpected error loading configuration: {e}")
        sys.exit(1)

    # Start the scheduler
    try:
        run_scheduler(env=env, logger=logger, version=__version__)
    except Exception as e:
        logger.error(f"Scheduler error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
