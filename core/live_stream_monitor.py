"""Live stream monitoring module."""

import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from pathvalidate import sanitize_filename

from models.config import CreatorProfile
from models.download import DownloadResult, DownloadSession, SessionState
from models.rplay import CreatorStreamState, LiveStream, StreamState

from .config import ConfigError, read_config
from .download_merge_executor import DownloadMergeExecutor
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
        config_path: str = "./config/config.yaml",
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
        self.monitored_creators: Dict[str, CreatorProfile] = {}
        self.sessions: Dict[str, DownloadSession] = {}
        self.latest_session_by_creator: Dict[str, str] = {}
        self.merge_executor = DownloadMergeExecutor(max_workers=1)
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
                if stream.creator_oid not in self.monitored_creators:
                    continue
                if stream.stream_state != StreamState.LIVE:
                    continue

                monitored_live += 1
                session_key = self._make_session_key(stream)
                self.latest_session_by_creator[stream.creator_oid] = session_key

                existing_session = self.sessions.get(session_key)
                if existing_session is not None and existing_session.state in {
                    SessionState.RAW_RUNNING,
                    SessionState.MERGE_QUEUED,
                    SessionState.MERGING,
                    SessionState.DONE,
                    SessionState.BLOCKED,
                }:
                    continue

                downloader = self.downloaders[stream.creator_oid]
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
        session_key = self._make_session_key(stream)

        self._update_creator_state(stream)
        self.logger.info(f"🔴 {creator_name} is live: \"{stream.title}\"")

        if session_key not in self.sessions:
            self.sessions[session_key] = DownloadSession(
                session_key=session_key,
                creator_oid=creator_oid,
                creator_name=creator_name,
                title=stream.title,
                stream_start_time=stream.stream_start_time,
                state=SessionState.RAW_RUNNING,
                staging_dir=self._build_staging_dir(creator_name, session_key),
            )

        try:
            stream_url = self.api.get_stream_url(creator_oid)

            if not self.api.validate_m3u8_url(stream_url):
                self._get_or_create_creator_state(creator_oid).mark_blocked()
                self.sessions[session_key].state = SessionState.BLOCKED
                self.logger.warning(
                    f"🔒 {creator_name}: Cannot access stream (likely paid content)"
                )
                return

            active_downloader = downloader
            if isinstance(downloader, StreamDownloader):
                active_downloader = StreamDownloader(
                    creator_name=creator_name,
                    on_download_error=self._make_session_download_error_callback(
                        session_key, creator_oid, creator_name
                    ),
                    session_key=session_key,
                    output_dir=self.sessions[session_key].staging_dir,
                    output_extension=".ts",
                    on_download_complete=lambda result: self._on_download_complete(
                        session_key, result
                    ),
                )
                self.downloaders[creator_oid] = active_downloader

            active_downloader.download(stream_url, stream.title)
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
        active_downloads = sum(
            1 for session in self.sessions.values() if session.state == SessionState.RAW_RUNNING
        )
        if active_downloads == 0:
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
            self.monitored_creators = {
                profile.creator_oid: profile for profile in creator_profiles
            }

            # Track new creators for logging
            new_creators = []

            for profile in creator_profiles:
                if profile.creator_oid not in self.downloaders:
                    self.downloaders[profile.creator_oid] = StreamDownloader(
                        creator_name=profile.creator_name,
                        on_download_error=self._make_download_error_callback(
                            profile.creator_oid, profile.creator_name
                        ),
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
        active_sessions = [
            session.creator_name
            for session in self.sessions.values()
            if session.state == SessionState.RAW_RUNNING
        ]
        if active_sessions:
            return active_sessions

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

    def _make_session_key(self, stream: LiveStream) -> str:
        """Build a stable key for one live session."""
        return f"{stream.creator_oid}:{stream.stream_start_time.isoformat()}"

    def _build_staging_dir(self, creator_name: str, session_key: str) -> Path:
        """Build the staging directory for one session."""
        return (
            Path.cwd()
            / StreamDownloader.ARCHIVE_DIR
            / creator_name
            / ".staging"
            / self._make_session_dir_name(session_key)
        )

    def _make_session_dir_name(self, session_key: str) -> str:
        """Convert a session key into a filesystem-safe directory name."""
        return sanitize_filename(session_key, replacement_text="_")

    def _on_download_complete(self, session_key: str, result: DownloadResult) -> None:
        """Queue merge work after raw download completion."""
        session = self.sessions.get(session_key)
        if session is None:
            return

        session.state = SessionState.MERGE_QUEUED
        self.merge_executor.submit_merge(
            session_key,
            lambda: self._merge_session_to_mp4(session_key, result),
        )

    def _merge_session_to_mp4(self, session_key: str, result: DownloadResult) -> None:
        """Merge one session's raw ts outputs into the final mp4 artifact."""
        session = self.sessions.get(session_key)
        if session is None:
            return

        session.state = SessionState.MERGING
        ts_files = sorted(result.staging_dir.glob("*.ts"))

        try:
            if not ts_files:
                raise FileNotFoundError(f"No ts files found for session {session_key}")

            output_path = self._reserve_final_output_path(
                creator_name=session.creator_name,
                title=session.title,
                stream_start_time=session.stream_start_time,
            )
            self._run_ffmpeg_merge(ts_files, output_path)

            for ts_file in ts_files:
                ts_file.unlink(missing_ok=True)

            if session.staging_dir.exists():
                shutil.rmtree(session.staging_dir)

            session.final_output_path = output_path
            session.state = SessionState.DONE

        except Exception as exc:
            session.state = SessionState.MERGE_FAILED
            session.last_error = str(exc)
            self._move_failed_staging_dir(session)

    def _reserve_final_output_path(
        self,
        creator_name: str,
        title: str,
        stream_start_time,
    ) -> Path:
        """Reserve the next available final mp4 output path."""
        safe_title = sanitize_filename(title, replacement_text="_") or "untitled"
        date_str = stream_start_time.strftime("%Y-%m-%d")
        base_dir = Path.cwd() / StreamDownloader.ARCHIVE_DIR / creator_name
        base_dir.mkdir(parents=True, exist_ok=True)

        base_path = base_dir / f"#{creator_name} {date_str} {safe_title}.mp4"
        if not base_path.exists():
            return base_path

        counter = 1
        while True:
            candidate = base_dir / f"#{creator_name} {date_str} {safe_title}_{counter}.mp4"
            if not candidate.exists():
                return candidate
            counter += 1

    def _run_ffmpeg_merge(self, ts_files: List[Path], output_path: Path) -> None:
        """Merge ts fragments into one mp4 file using ffmpeg concat."""
        list_path = ts_files[0].parent / "merge-inputs.txt"
        list_content = "\n".join(
            f"file '{ts_file.resolve().as_posix()}'" for ts_file in ts_files
        )
        list_path.write_text(list_content, encoding="utf-8")

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_path),
                    "-c",
                    "copy",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        finally:
            list_path.unlink(missing_ok=True)

    def _move_failed_staging_dir(self, session: DownloadSession) -> None:
        """Move failed raw session files into the visible failed directory."""
        if not session.staging_dir.exists():
            return

        failed_dir = (
            Path.cwd()
            / StreamDownloader.ARCHIVE_DIR
            / session.creator_name
            / "_failed"
            / self._make_session_dir_name(session.session_key)
        )
        failed_dir.parent.mkdir(parents=True, exist_ok=True)
        if failed_dir.exists():
            shutil.rmtree(failed_dir)
        shutil.move(str(session.staging_dir), str(failed_dir))
        session.staging_dir = failed_dir

    def shutdown(self) -> None:
        """Shut down background merge work."""
        self.merge_executor.shutdown(wait=False)

    def _make_download_error_callback(
        self, creator_oid: str, creator_name: str
    ) -> Callable[[str], None]:
        """
        Create a callback for handling download errors from a specific creator.

        The callback marks the creator's stream as blocked when yt-dlp encounters
        M3U8 access errors (e.g., paid content where the master playlist returns
        200 but media playlists return 404).

        Args:
            creator_oid: Unique identifier for the creator
            creator_name: Display name of the creator

        Returns:
            Callback function that accepts an error message string
        """

        def _on_error(error_message: str) -> None:
            state = self._get_or_create_creator_state(creator_oid)
            if not state.is_current_stream_blocked:
                state.mark_blocked()
                self.logger.warning(
                    f"🔒 {creator_name}: Stream marked as inaccessible "
                    f"after download failure (likely paid content)"
                )

        return _on_error

    def _make_session_download_error_callback(
        self, session_key: str, creator_oid: str, creator_name: str
    ) -> Callable[[str], None]:
        """Create a callback for a specific session download failure."""

        def _on_error(error_message: str) -> None:
            state = self._get_or_create_creator_state(creator_oid)
            if not state.is_current_stream_blocked:
                state.mark_blocked()
                self.logger.warning(
                    f"🔒 {creator_name}: Stream marked as inaccessible "
                    f"after download failure (likely paid content)"
                )

            session = self.sessions.get(session_key)
            if session is not None:
                session.state = SessionState.BLOCKED
                session.last_error = error_message

        return _on_error
