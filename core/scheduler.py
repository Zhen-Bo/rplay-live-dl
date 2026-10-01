"""Periodic live stream check scheduler for rplay-live-dl."""

import logging
import os
import signal
import sys
from typing import Optional

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from core.config import DEFAULT_CONFIG_PATH, validate_startup_config_path
from core.disk_space import DiskSpaceMonitor
from core.env import EnvConfig
from core.live_stream_monitor import LiveStreamMonitor
from core.notifications import DiscordNotifier
from core.rplay import RPlayAPI
from core.utils import terminate_child_processes

__all__ = [
    "LiveStreamScheduler",
    "run_scheduler",
]

_scheduler: Optional["LiveStreamScheduler"] = None


def _signal_handler(signum: int, frame) -> None:
    signal_name = signal.Signals(signum).name
    if _scheduler:
        _scheduler.logger.info(f"Received {signal_name}, shutting down gracefully...")
        _scheduler.stop()
    sys.exit(0)


class LiveStreamScheduler:
    def __init__(
        self,
        env: EnvConfig,
        logger: logging.Logger,
        api_client: RPlayAPI,
        version: str = "unknown",
        notifier: Optional[DiscordNotifier] = None,
    ) -> None:
        self.logger = logger
        self.env = env
        self.version = version
        self.git_sha = os.getenv("APP_GIT_SHA", "").strip()
        self.monitor = LiveStreamMonitor(
            api_client=api_client,
            min_free_disk_gb=self.env.min_free_disk_gb,
            disk_monitor=DiskSpaceMonitor(
                self.env.disk_warning_gb,
                self.env.disk_critical_gb,
                self.env.disk_recovery_margin_gb,
                self.env.disk_reminder_seconds,
            ),
            merge_reserve_gb=self.env.merge_min_free_disk_gb,
            merge_space_multiplier=self.env.merge_space_multiplier,
            notifier=notifier,
        )
        self.scheduler = BlockingScheduler()
        self._stopped = False

    def check_and_download(self) -> None:
        try:
            self.monitor.check_live_streams_and_start_download()
        except Exception as e:
            self.logger.exception(f"Error while checking live streams: {e}")

    def start(self) -> None:
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

            self.check_and_download()

            self.scheduler.start()

        except KeyboardInterrupt:
            self.logger.info("Monitoring system manually stopped")
            self.stop()
        except Exception as e:
            self.logger.exception(f"System runtime error: {e}")
            raise

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True

        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

        # Always shut the monitor down, even when the scheduler never started,
        # because its control thread and merge executor exist from construction.
        # It stops recordings while sparing merge children, then merges what they left.
        self.monitor.shutdown()

        # Safety net only. The merge executor is already closed, so anything alive
        # is a recording child that escaped the monitor's sweep (a yt-dlp retry
        # that respawned). Those outlive the interpreter unless reaped.
        reaped = terminate_child_processes()
        if reaped:
            self.logger.warning(
                f"Terminated {reaped} download subprocess(es) left running by yt-dlp"
            )

        self.logger.info("Scheduler stopped")


def run_scheduler(
    env: EnvConfig,
    logger: logging.Logger,
    version: str,
    api_client: RPlayAPI,
    notifier: Optional[DiscordNotifier] = None,
) -> None:
    global _scheduler

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    validate_startup_config_path(DEFAULT_CONFIG_PATH)
    _scheduler = LiveStreamScheduler(
        env=env,
        logger=logger,
        api_client=api_client,
        version=version,
        notifier=notifier,
    )
    try:
        _scheduler.start()
    finally:
        _scheduler.stop()
