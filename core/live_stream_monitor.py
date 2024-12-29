import logging
from typing import Dict

from models.rplay import StreamState

from .config import ConfigError, read_config
from .downloader import StreamDownloader
from .rplay import RPlayAPI


class LiveStreamMonitor:
    """
    Class for monitoring and auto-downloading live streams.

    This class handles:
    - Monitoring configured creators for live streams
    - Managing download tasks for active streams
    - Automatic cleanup of inactive downloaders which are not monitored
    """

    def __init__(
        self, auth_token: str, user_oid: str, config_path: str = "./config.yaml"
    ):
        """
        Initialize monitor with authentication and configuration.

        Args:
            auth_token: JWT token for API auth
            user_oid: User's identifier
            config_path: Path to creator profiles YAML config
        """
        self.api = RPlayAPI(auth_token, user_oid)
        self.config_path = config_path
        self.downloaders: Dict[str, StreamDownloader] = {}

        # Setup logging
        self.logger = logging.getLogger("Monitor")
        self.logger.setLevel(logging.INFO)

    def check_live_streams_and_start_download(self) -> None:
        """
        Check active streams and start new downloads.
        Updates downloader list, fetches stream status, and initiates downloads for live streams.
        """
        try:
            self.__update_downloaders()
            live_streams = self.api.get_livestream_status()

            for stream in live_streams:
                # Start download if stream is live and not already downloading
                if (
                    stream.creator_oid in self.downloaders
                    and stream.stream_state == StreamState.LIVE
                    and not self.downloaders[stream.creator_oid].is_alive()
                ):
                    self.logger.info(
                        f"creator is live: {stream.creator_oid}, start downloading"
                    )

                    try:
                        stream_url = self.api.get_stream_url(stream.creator_oid)
                    except Exception as e:
                        self.logger.warning(
                            f"Error getting stream URL for {stream.creator_oid}: {str(e)}"
                        )
                        continue

                    self.downloaders[stream.creator_oid].download(
                        stream_url, stream.title
                    )

        except ConfigError:
            self.logger.warning("Skipping check due to config file error")
            return
        except Exception as e:
            self.logger.error(f"Error during monitoring: {str(e)}")

    def __update_downloaders(self) -> None:
        """
        Update downloader list and cleanup inactive downloaders which are not monitored.

        Steps:
        1. Load creator profiles from config
        2. Add new downloaders for unmonitored creators
        3. Remove inactive downloaders not in config
        """
        try:
            # Read creator profiles from config
            creator_profiles = read_config(self.config_path)

            # Add new downloaders
            for profile in creator_profiles:
                if profile.creator_oid not in self.downloaders:
                    self.downloaders[profile.creator_oid] = StreamDownloader(
                        profile.creator_name
                    )

            # Get set of configured creator IDs
            config_creator_ids = {profile.creator_oid for profile in creator_profiles}

            # Update downloaders list, keep only monitored or active downloads
            self.downloaders = {
                creator_id: downloader
                for creator_id, downloader in self.downloaders.items()
                if creator_id in config_creator_ids or downloader.is_alive()
            }

        except ConfigError as ce:
            self.logger.warning(f"Error reading config file: {ce}")
            raise
        except Exception as e:
            self.logger.error(f"Error updating downloaders: {str(e)}")
