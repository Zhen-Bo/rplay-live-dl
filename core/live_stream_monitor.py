"""
Live stream monitoring module.

Provides functionality to monitor configured creators for active streams
and automatically initiate downloads when streams are detected.
"""

from typing import Dict, List, Optional, Set

from models.rplay import CreatorStreamState, LiveStream, StreamState

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
        self._check_count = 0
        self._last_status: Dict[str, int] = {"active_downloads": 0, "monitored_live": 0}

        # Track per-creator stream session state for M3U8 404 handling
        self._creator_states: Dict[str, CreatorStreamState] = {}

    def check_live_streams_and_start_download(self) -> None:
        """
        Check active streams and start new downloads.

        Updates downloader list, fetches stream status, and initiates
        downloads for live streams.
        """
        try:
            self._update_downloaders()
            live_streams = self.api.get_livestream_status()

            live_creator_oids = {s.creator_oid for s in live_streams}
            monitored_live = 0

            for stream in live_streams:
                if stream.creator_oid not in self.downloaders:
                    continue
                if stream.stream_state != StreamState.LIVE:
                    continue

                monitored_live += 1
                downloader = self.downloaders[stream.creator_oid]

                if downloader.is_alive():
                    continue
                if not self._should_attempt_download(stream):
                    continue

                self._start_download(stream, downloader)

            self._cleanup_offline_creator_states(live_creator_oids)
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

    def _should_attempt_download(self, stream: LiveStream) -> bool:
        """Check if download should be attempted for this stream."""
        creator_oid = stream.creator_oid
        state = self._creator_states.get(creator_oid)

        if state is None:
            return True

        if self._is_new_stream_session(stream):
            return True

        return not state.is_current_stream_blocked

    def _cleanup_offline_creator_states(self, live_creator_oids: Set[str]) -> None:
        """Clear state for creators no longer in the live list."""
        offline_creators = [
            oid for oid in self._creator_states
            if oid not in live_creator_oids
        ]
        for oid in offline_creators:
            self._clear_creator_state(oid)

    def _start_download(self, stream: LiveStream, downloader: StreamDownloader) -> None:
        """
        Start downloading a live stream.

        Args:
            stream: LiveStream object with stream information
            downloader: StreamDownloader instance for this creator
        """
        creator_name = downloader.creator_name
        creator_oid = stream.creator_oid

        self._update_creator_state(stream)
        self.logger.info(f"🔴 {creator_name} is live: \"{stream.title}\"")

        try:
            stream_url = self.api.get_stream_url(creator_oid)

            if not self.api.validate_m3u8_url(stream_url):
                self._get_or_create_creator_state(creator_oid).mark_blocked()
                self.logger.warning(
                    f"🔒 {creator_name}: Cannot access stream (likely paid content)"
                )
                return

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

        Only logs at INFO level when state changes. Periodic heartbeat at DEBUG level.
        """
        self._check_count += 1
        active_downloads = sum(1 for d in self.downloaders.values() if d.is_alive())

        current_status = {"active_downloads": active_downloads, "monitored_live": monitored_live}
        state_changed = current_status != self._last_status
        periodic_heartbeat = self._check_count % 10 == 0

        if state_changed and (active_downloads > 0 or self._last_status["active_downloads"] > 0):
            self.logger.info(
                f"📊 Status: {active_downloads} active download(s), "
                f"{monitored_live}/{self._monitored_count} monitored creator(s) live"
            )
        elif periodic_heartbeat and self._monitored_count > 0:
            self.logger.debug(
                f"📊 Checked {total_live} live stream(s), "
                f"none of {self._monitored_count} monitored creator(s) are live"
            )

        self._last_status = current_status

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

    def _is_new_stream_session(self, stream: LiveStream) -> bool:
        """Check if this is a new stream session based on streamStartTime."""
        state = self._creator_states.get(stream.creator_oid)
        if state is None:
            return True
        return state.last_stream_start_time != stream.stream_start_time

    def _update_creator_state(self, stream: LiveStream) -> None:
        """Update or create creator state with new stream start time."""
        creator_oid = stream.creator_oid
        if creator_oid not in self._creator_states:
            self._creator_states[creator_oid] = CreatorStreamState()
        self._creator_states[creator_oid].update_stream_start_time(
            stream.stream_start_time
        )

    def _clear_creator_state(self, creator_oid: str) -> None:
        """Remove creator state when they go offline."""
        self._creator_states.pop(creator_oid, None)

    def _get_or_create_creator_state(self, creator_oid: str) -> CreatorStreamState:
        """Get existing state or create new one for a creator."""
        if creator_oid not in self._creator_states:
            self._creator_states[creator_oid] = CreatorStreamState()
        return self._creator_states[creator_oid]
