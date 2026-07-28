"""
Scheduler module for rplay-live-dl.

Provides the scheduling infrastructure for periodic live stream
monitoring and downloading operations.
"""

import logging
import os
import signal
import sys
from typing import Optional

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from core.config import DEFAULT_CONFIG_PATH, validate_startup_config_path
from core.env import EnvConfig
from core.live_stream_monitor import LiveStreamMonitor
from core.utils import terminate_child_processes

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
        self.git_sha = os.getenv("APP_GIT_SHA", "").strip()
        self.monitor = LiveStreamMonitor(self.env.auth_token, self.env.user_oid)
        self.scheduler = BlockingScheduler()
        self._stopped = False

    def check_and_download(self) -> None:
        """Execute check and download task."""
        try:
            self.monitor.check_live_streams_and_start_download()
        except Exception as e:
            self.logger.exception(f"Error while checking live streams: {e}")

    def start(self) -> None:
        """
        Start the scheduler and begin monitoring.

        Performs an initial check immediately, then schedules
        periodic checks at the configured interval.
        """
        try:
            build = f" ({self.git_sha[:7]})" if self.git_sha else ""
            self.logger.info(
                f"rplay-live-dl v{self.version}{build} — "
                f"checking every {self.env.interval}s"
            )

            self.scheduler.add_job(
                self.check_and_download,
                trigger=IntervalTrigger(seconds=self.env.interval),
                name="check_livestreams",
            )

            # Perform initial check
            self.check_and_download()

            self.scheduler.start()

        except KeyboardInterrupt:
            self.logger.info("Monitoring system manually stopped")
            self.stop()
        except Exception as e:
            self.logger.exception(f"System runtime error: {e}")
            raise

    def stop(self) -> None:
        """Stop the scheduler, drain the monitor, and reap download subprocesses."""
        if self._stopped:
            return
        self._stopped = True

        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

        # Always shut the monitor down, even when the scheduler never started:
        # its control thread and merge executor exist from construction.
        # The monitor owns recording subprocess lifecycle: it stops recordings
        # while sparing merge children, then merges what they left behind.
        self.monitor.shutdown()

        # Safety net only. The monitor has closed its merge executor by now, so
        # nothing here can be a merge ffmpeg; anything still alive is a
        # recording child that escaped the monitor's sweep (a yt-dlp retry that
        # respawned after it). Those outlive the interpreter unless reaped.
        reaped = terminate_child_processes()
        if reaped:
            self.logger.warning(
                f"Terminated {reaped} download subprocess(es) left running by yt-dlp"
            )

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

    validate_startup_config_path(DEFAULT_CONFIG_PATH)
    _scheduler = LiveStreamScheduler(env=env, logger=logger, version=version)
    _scheduler.start()
