import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from core.env import EnvConfig, load_env
from core.live_stream_monitor import LiveStreamMonitor
from core.logger import setup_logger


class LiveStreamScheduler:
    def __init__(self, env: EnvConfig, logger):
        self.logger = logger
        self.logger.info("Starting live stream monitoring system")
        self.env = env
        self.monitor = LiveStreamMonitor(self.env.auth_token, self.env.user_oid)
        self.scheduler = BlockingScheduler()

    def check_and_download(self):
        """Execute check and download task"""
        try:
            self.monitor.check_live_streams_and_start_download()
        except Exception as e:
            self.logger.error(f"Error while checking live streams: {str(e)}")

    def start(self):
        try:
            self.scheduler.add_job(
                self.check_and_download,
                trigger=IntervalTrigger(seconds=self.env.interval),
                name="check_livestreams",
            )

            self.check_and_download()
            self.logger.info(
                f"Scheduler started, checking every {self.env.interval} seconds"
            )
            self.scheduler.start()

        except KeyboardInterrupt:
            self.logger.info("Monitoring system manually stopped")
        except Exception as e:
            self.logger.error(f"System runtime error: {str(e)}")
            raise


def main():
    logger = setup_logger("Main")
    try:
        env = load_env()
    except Exception as e:
        logger.error(f"Environment configuration error: {str(e)}")
        sys.exit(1)

    try:
        scheduler = LiveStreamScheduler(env=env, logger=logger)
        scheduler.start()
    except Exception as e:
        logger.error(f"Scheduler error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
