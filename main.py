"""Entry point for rplay-live-dl."""

import logging
import sys
import tomllib
from pathlib import Path

from dotenv import load_dotenv

from core.config import DEFAULT_CONFIG_PATH, ConfigError, read_app_config
from core.constants import DEFAULT_RPLAY_API_BASE_URL
from core.downloader import StreamDownloader
from core.env import EnvConfig, EnvConfigError, load_env
from core.logger import cleanup_old_logs, configure_logging, setup_logger
from core.notifications import DiscordNotifier
from core.orphan_recovery import recover_orphaned_sessions
from core.rplay import RPlayAPI, RPlayAPIError, RPlayAuthError
from core.scheduler import run_scheduler
from models.notification import Notification, NotificationKind


def _read_version() -> str:
    pyproject_path = Path(__file__).parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    return data["tool"]["poetry"]["version"]


__version__ = _read_version()


def _warn_about_orphaned_downloads(logger: logging.Logger) -> None:
    """List leftovers of interrupted recordings, which nothing else revisits."""
    # Runs after recovery, so it lists only what recovery could not fix. That
    # includes .part-FragN and .ytdl artifacts, which may be torn mid-write and
    # would merge into broken video.
    archive = Path.cwd() / StreamDownloader.ARCHIVE_DIR
    if not archive.is_dir():
        return

    # *.part* covers .part, .part-FragN and .part-FragN.part in one pattern,
    # so the three patterns are disjoint and need no dedup.
    patterns = ("[0-9]*_*.ts", "*.part*", "*.ytdl")
    orphans = sorted(
        path for pattern in patterns for path in archive.glob(f"*/{pattern}")
    )
    if not orphans:
        return

    logger.warning(
        f"Found {len(orphans)} file(s) left behind by interrupted recordings:"
    )
    for path in orphans[:10]:
        logger.warning(f"  {path.relative_to(archive)}")
    if len(orphans) > 10:
        logger.warning(f"  ... and {len(orphans) - 10} more")


def main() -> None:
    load_dotenv()

    # Validate env before configuring logging so invalid values fail fast.
    try:
        env = load_env()
    except EnvConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Invalid configuration: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error loading configuration: {e}", file=sys.stderr)
        sys.exit(1)

    configure_logging(env)
    logger = setup_logger("Main")
    logger.info("Environment configuration loaded successfully")
    # configure_logging registered the webhook and tokens with the shared redactor.
    notifier = DiscordNotifier(
        env.discord_webhook_url.get_secret_value(),
        events=env.discord_events,
        logger=logger,
    )
    try:
        _run_application(env, logger, notifier)
    finally:
        notifier.close()


def _run_application(
    env: EnvConfig, logger: logging.Logger, notifier: DiscordNotifier
) -> None:
    try:
        removed = cleanup_old_logs()
        if removed > 0:
            logger.info(f"Cleaned up {removed} old log file(s)")
    except Exception as e:
        logger.warning(f"Failed to cleanup old logs: {e}")

    # Before the scheduler polls: nothing else is writing the archive yet, so
    # merging here cannot race a fresh recording into the same directory.
    try:
        recover_orphaned_sessions(
            logger,
            reserve_gb=env.merge_min_free_disk_gb,
            space_multiplier=env.merge_space_multiplier,
            notifier=notifier,
        )
    except Exception as e:
        # Recovery is best-effort housekeeping and must never block startup.
        # Every input it touches is kept on failure, so the next run retries.
        logger.warning(f"Failed to recover orphaned recordings: {e}")

    _warn_about_orphaned_downloads(logger)

    # Same apiBaseUrl the monitor applies from config.yaml on each poll.
    try:
        api_base_url = read_app_config(DEFAULT_CONFIG_PATH).api_base_url
    except ConfigError as exc:
        # The scheduler owns hard config failures, so probe with the default URL.
        logger.warning(
            f"Could not load config for credential check "
            f"(using default API URL): {exc}"
        )
        api_base_url = DEFAULT_RPLAY_API_BASE_URL

    api_client = RPlayAPI(
        base_url=api_base_url,
        user_oid=env.user_oid,
        refresh_token=env.refresh_token,
        token_refresh_leeway_seconds=env.token_refresh_leeway_seconds,
    )
    try:
        try:
            api_client.validate_credentials()
            logger.info("API credentials validated successfully")
        except RPlayAuthError as exc:
            notifier.notify(
                Notification(
                    NotificationKind.AUTH_FAILED,
                    detail="This happened during startup credential validation.",
                )
            )
            logger.error(
                f"Authentication failed: {exc}. "
                "Please update REFRESH_TOKEN and USER_OID in your .env file, then restart."
            )
            sys.exit(1)
        except RPlayAPIError as exc:
            # RPlayConnectionError is an RPlayAPIError, the monitor owns retries.
            logger.warning(
                f"Could not verify credentials due to API error "
                f"(continuing; will retry while running): {exc}"
            )

        # Share the validated client so monitoring retains its acquired JWT.
        try:
            run_scheduler(
                env=env,
                logger=logger,
                version=__version__,
                api_client=api_client,
                notifier=notifier,
            )
        except Exception as e:
            logger.exception(f"Scheduler error: {e}")
            sys.exit(1)
    finally:
        api_client.close()


if __name__ == "__main__":
    main()
