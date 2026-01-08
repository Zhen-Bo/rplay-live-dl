"""
Scheduler module for rplay-live-dl.

Provides the scheduling infrastructure for periodic live stream
monitoring and downloading operations.
"""

import logging
import signal
import sys
from typing import Optional

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from core.env import EnvConfig
from core.live_stream_monitor import LiveStreamMonitor

__all__ = [
    "LiveStreamScheduler",
    "run_scheduler",
]

# Global scheduler reference for signal handling
_scheduler: Optional["LiveStreamScheduler"] = None


def _signal_handler(signum: int, frame) -> None:
    """Handle shutdown signals gracefully."""
    signal_name = signal.Signals(signum).name
    if _scheduler:
        _scheduler.logger.info(f"Received {signal_name}, shutting down gracefully...")
        _scheduler.stop()
    sys.exit(0)


class LiveStreamScheduler:
    """
    Scheduler for periodic live stream monitoring and downloading.

    Manages the APScheduler instance and coordinates the monitoring
    of configured creators for active live streams.
    """

    def __init__(
        self,
        env: EnvConfig,
        logger: logging.Logger,
        version: str = "unknown",
    ) -> None:
        """
        Initialize the scheduler with environment configuration.

        Args:
            env: Environment configuration containing auth and interval settings
            logger: Logger instance for output
            version: Application version string for display
        """
        self.logger = logger
        self.env = env
        self.version = version
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
            self.logger.info(f"rplay-live-dl v{self.version}")
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


def run_scheduler(env: EnvConfig, logger: logging.Logger, version: str) -> None:
    """
    Initialize and run the scheduler with signal handling.

    Args:
        env: Environment configuration
        logger: Logger instance
        version: Application version string
    """
    global _scheduler

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    _scheduler = LiveStreamScheduler(env=env, logger=logger, version=version)
    _scheduler.start()
