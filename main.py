"""
rplay-live-dl - Automated RPlay live stream downloader.

This module serves as the entry point for the application,
initializing the scheduler and starting the monitoring system.
"""

import sys
from typing import NoReturn

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from core.env import EnvConfig, load_env
from core.live_stream_monitor import LiveStreamMonitor
from core.logger import cleanup_old_logs, setup_logger

__version__ = "1.1.2"


class LiveStreamScheduler:
    """
    Scheduler for periodic live stream monitoring and downloading.

    Manages the APScheduler instance and coordinates the monitoring
    of configured creators for active live streams.
    """

    def __init__(self, env: EnvConfig, logger) -> None:
        """
        Initialize the scheduler with environment configuration.

        Args:
            env: Environment configuration containing auth and interval settings
            logger: Logger instance for output
        """
        self.logger = logger
        self.env = env
        self.monitor = LiveStreamMonitor(self.env.auth_token, self.env.user_oid)
        self.scheduler = BlockingScheduler()

    def check_and_download(self) -> None:
        """Execute check and download task."""
        try:
            self.monitor.check_live_streams_and_start_download()
        except Exception as e:
            self.logger.error(f"Error while checking live streams: {e}")

    def start(self) -> None:
        """
        Start the scheduler and begin monitoring.

        Performs an initial check immediately, then schedules
        periodic checks at the configured interval.
        """
        try:
            self.logger.info("=" * 50)
            self.logger.info(f"rplay-live-dl v{__version__}")
            self.logger.info("=" * 50)
            self.logger.info("Starting live stream monitoring system")

            self.scheduler.add_job(
                self.check_and_download,
                trigger=IntervalTrigger(seconds=self.env.interval),
                name="check_livestreams",
            )

            # Perform initial check
            self.check_and_download()

            self.logger.info(
                f"Scheduler started, checking every {self.env.interval} seconds"
            )
            self.scheduler.start()

        except KeyboardInterrupt:
            self.logger.info("Monitoring system manually stopped")
        except Exception as e:
            self.logger.error(f"System runtime error: {e}")
            raise

    def stop(self) -> None:
        """Stop the scheduler gracefully."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            self.logger.info("Scheduler stopped")


def main() -> NoReturn:
    """
    Main entry point for the application.

    Initializes logging, loads configuration, and starts the scheduler.
    """
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
    except FileNotFoundError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except ValueError as e:
        logger.error(f"Invalid configuration: {e}")
        sys.exit(1)

    # Start the scheduler
    try:
        scheduler = LiveStreamScheduler(env=env, logger=logger)
        scheduler.start()
    except Exception as e:
        logger.error(f"Scheduler error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
