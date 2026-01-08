"""
Live stream monitoring module.

Provides functionality to monitor configured creators for active streams
and automatically initiate downloads when streams are detected.
"""

from typing import Dict, List, Optional

from models.rplay import StreamState

from .config import ConfigError, read_config
from .downloader import StreamDownloader
from .logger import setup_logger
from .rplay import RPlayAPI, RPlayAPIError, RPlayAuthError, RPlayConnectionError

__all__ = [
    "LiveStreamMonitor",
]


class LiveStreamMonitor:
    """
    Class for monitoring and auto-downloading live streams.

    This class handles:
    - Monitoring configured creators for live streams
    - Managing download tasks for active streams
    - Automatic cleanup of inactive downloaders which are not monitored
    """

    def __init__(
        self,
        auth_token: str,
        user_oid: str,
        config_path: str = "./config.yaml",
        api: Optional[RPlayAPI] = None,
    ) -> None:
        """
        Initialize monitor with authentication and configuration.

        Args:
            auth_token: JWT token for API auth
            user_oid: User's identifier
            config_path: Path to creator profiles YAML config
            api: Optional RPlayAPI instance for dependency injection (testing)
        """
        self.api = api if api is not None else RPlayAPI(auth_token, user_oid)
        self.config_path = config_path
        self.downloaders: Dict[str, StreamDownloader] = {}
        self.logger = setup_logger("Monitor")

        # Track monitoring state for better UX
        self._last_check_success = True
        self._monitored_count = 0

    def check_live_streams_and_start_download(self) -> None:
        """
        Check active streams and start new downloads.

        Updates downloader list, fetches stream status, and initiates
        downloads for live streams.
        """
        try:
            self._update_downloaders()
            live_streams = self.api.get_livestream_status()

            # Count monitored creators that are live
            monitored_live = 0

            for stream in live_streams:
                if stream.creator_oid not in self.downloaders:
                    continue

                downloader = self.downloaders[stream.creator_oid]

                # Check if stream is live and not already downloading
                if stream.stream_state == StreamState.LIVE:
                    monitored_live += 1

                    if not downloader.is_alive():
                        self._start_download(stream, downloader)

            # Log status summary
            self._log_status_summary(len(live_streams), monitored_live)
            self._last_check_success = True

        except ConfigError:
            self.logger.warning("Skipping check due to config file error")
            self._last_check_success = False

        except RPlayAuthError as e:
            self.logger.error(f"Authentication error: {e}")
            self.logger.error("Please update your AUTH_TOKEN in .env file")
            self._last_check_success = False

        except RPlayConnectionError as e:
            self.logger.warning(f"Connection error (will retry): {e}")
            self._last_check_success = False

        except RPlayAPIError as e:
            self.logger.error(f"API error: {e}")
            self._last_check_success = False

        except Exception as e:
            self.logger.error(f"Unexpected error during monitoring: {e}")
            self._last_check_success = False

    def _start_download(self, stream, downloader: StreamDownloader) -> None:
        """
        Start downloading a live stream.

        Args:
            stream: LiveStream object with stream information
            downloader: StreamDownloader instance for this creator
        """
        creator_name = downloader.creator_name
        self.logger.info(f"🔴 {creator_name} is live: \"{stream.title}\"")

        try:
            stream_url = self.api.get_stream_url(stream.creator_oid)
            downloader.download(stream_url, stream.title)
            self.logger.info(f"⬇️  Started downloading: {creator_name}")

        except RPlayAuthError as e:
            self.logger.error(f"Auth error for {creator_name}: {e}")

        except RPlayAPIError as e:
            self.logger.warning(f"Failed to get stream URL for {creator_name}: {e}")

        except Exception as e:
            self.logger.error(f"Error starting download for {creator_name}: {e}")

    def _log_status_summary(self, total_live: int, monitored_live: int) -> None:
        """
        Log a summary of the current monitoring status.

        Args:
            total_live: Total number of live streams on the platform
            monitored_live: Number of monitored creators currently live
        """
        active_downloads = sum(1 for d in self.downloaders.values() if d.is_alive())

        if active_downloads > 0:
            self.logger.info(
                f"📊 Status: {active_downloads} active download(s), "
                f"{monitored_live}/{self._monitored_count} monitored creator(s) live"
            )
        elif self._monitored_count > 0:
            self.logger.debug(
                f"📊 Checked {total_live} live stream(s), "
                f"none of {self._monitored_count} monitored creator(s) are live"
            )

    def _update_downloaders(self) -> None:
        """
        Update downloader list and cleanup inactive downloaders.

        Steps:
        1. Load creator profiles from config
        2. Add new downloaders for unmonitored creators
        3. Remove inactive downloaders not in config
        """
        try:
            creator_profiles = read_config(self.config_path)

            # Track new creators for logging
            new_creators = []

            for profile in creator_profiles:
                if profile.creator_oid not in self.downloaders:
                    self.downloaders[profile.creator_oid] = StreamDownloader(
                        profile.creator_name
                    )
                    new_creators.append(profile.creator_name)

            if new_creators:
                self.logger.info(f"Added {len(new_creators)} new creator(s) to monitor")

            # Get set of configured creator IDs
            config_creator_ids = {p.creator_oid for p in creator_profiles}

            # Update downloaders list, keep only monitored or active downloads
            self.downloaders = {
                creator_id: downloader
                for creator_id, downloader in self.downloaders.items()
                if creator_id in config_creator_ids or downloader.is_alive()
            }

            self._monitored_count = len(config_creator_ids)

        except ConfigError as e:
            self.logger.warning(f"Error reading config file: {e}")
            raise

        except Exception as e:
            self.logger.error(f"Error updating downloaders: {e}")
            raise

    def get_active_downloads(self) -> List[str]:
        """
        Get list of creators with active downloads.

        Returns:
            List of creator names with active downloads
        """
        return [
            downloader.creator_name
            for downloader in self.downloaders.values()
            if downloader.is_alive()
        ]

    @property
    def is_healthy(self) -> bool:
        """Check if the last monitoring check was successful."""
        return self._last_check_success
