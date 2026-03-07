"""Live stream monitoring module."""

import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Event, RLock, Thread
from time import monotonic
from typing import Callable, Dict, List, Optional, Set, Union

from pathvalidate import sanitize_filename

from models.config import CreatorProfile
from models.download import (
    DownloadSession,
    MergeCompleted,
    MergeFailed,
    MergeJobSpec,
    MergeStarted,
    RawDownloadAuthFailed,
    RawDownloadBlocked,
    RawDownloadCompleted,
    RawDownloadFailed,
    SessionState,
)
from models.rplay import CreatorStreamState, LiveStream, StreamState

from .config import ConfigError, DEFAULT_CONFIG_PATH, read_app_config as read_config
from .download_merge_executor import DownloadMergeExecutor
from .downloader import StreamDownloader
from .logger import setup_logger
from .rplay import RPlayAPI, RPlayAPIError, RPlayAuthError, RPlayConnectionError

__all__ = [
    "LiveStreamMonitor",
]


@dataclass(frozen=True)
class _PollRequested:
    """Internal control-loop event requesting one monitor poll."""

    done: Event


@dataclass(frozen=True)
class _ShutdownRequested:
    """Internal control-loop event requesting shutdown."""


SessionEvent = Union[
    RawDownloadCompleted,
    RawDownloadAuthFailed,
    RawDownloadBlocked,
    RawDownloadFailed,
    MergeStarted,
    MergeCompleted,
    MergeFailed,
]


MonitorRuntimeEvent = Union[
    SessionEvent,
    _PollRequested,
    _ShutdownRequested,
]


class LiveStreamMonitor:
    """
    Class for monitoring and auto-downloading live streams.

    This class handles:
    - Monitoring configured creators for live streams
    - Managing download tasks for active streams
    - Automatic cleanup of inactive downloaders which are not monitored
    """

    DEFAULT_MERGE_TIMEOUT_SECONDS = 7200
    POLL_WAIT_TIMEOUT_SECONDS = 30.0
    TERMINAL_SESSION_STATES = {
        SessionState.BLOCKED,
        SessionState.DONE,
        SessionState.MERGE_FAILED,
    }

    def __init__(
        self,
        auth_token: str,
        user_oid: str,
        config_path: str = DEFAULT_CONFIG_PATH,
        api: Optional[RPlayAPI] = None,
        merge_timeout_seconds: int = DEFAULT_MERGE_TIMEOUT_SECONDS,
    ) -> None:
        """
        Initialize monitor with authentication and configuration.

        Args:
            auth_token: JWT token for API auth
            user_oid: User's identifier
            config_path: Path to creator profiles YAML config
            api: Optional RPlayAPI instance for dependency injection (testing)
            merge_timeout_seconds: Timeout for ffmpeg merge commands
        """
        self.api = api if api is not None else RPlayAPI(auth_token, user_oid)
        self.config_path = config_path
        self.merge_timeout_seconds = merge_timeout_seconds
        self.monitored_creators: Dict[str, CreatorProfile] = {}
        self.sessions: Dict[str, DownloadSession] = {}
        self.latest_stream_oid_by_creator: Dict[str, str] = {}
        self.merge_executor = DownloadMergeExecutor(max_workers=1)
        self.logger = setup_logger("Monitor")

        self._state_lock = RLock()
        self._event_queue: Queue[MonitorRuntimeEvent] = Queue()
        self._shutdown_requested = False
        self._control_thread = Thread(
            target=self._event_loop,
            name="monitor-control",
            daemon=True,
        )

        # Track monitoring state for better UX
        self._last_check_success = True
        self._monitored_count = 0
        self._check_count = 0
        self._last_status: Dict[str, int] = {"active_downloads": 0, "monitored_live": 0}

        # Track per-creator stream session state for M3U8 404 handling
        self._creator_states: Dict[str, CreatorStreamState] = {}

        self._control_thread.start()

    def check_live_streams_and_start_download(self) -> None:
        """Request one monitor poll and wait for it to finish."""
        if self._shutdown_requested:
            return

        done = Event()
        if not self._queue_monitor_event(_PollRequested(done=done)):
            return

        deadline = monotonic() + self.POLL_WAIT_TIMEOUT_SECONDS
        while not done.wait(timeout=0.5):
            if self._shutdown_requested:
                return
            if monotonic() >= deadline:
                self.logger.warning(
                    "Monitor poll did not finish before timeout; continuing"
                )
                return

    def _event_loop(self) -> None:
        """Run the monitor control loop as the single session-state writer."""
        while True:
            event = self._event_queue.get()
            try:
                if isinstance(event, _ShutdownRequested):
                    return

                if isinstance(event, _PollRequested):
                    try:
                        self._run_poll_cycle()
                    finally:
                        event.done.set()
                    continue

                self._handle_monitor_event(event)
            except Exception as exc:
                self.logger.error(f"Unexpected control-loop error: {exc}")
                if isinstance(event, _PollRequested):
                    event.done.set()
            finally:
                self._event_queue.task_done()

    def _queue_monitor_event(self, event: MonitorRuntimeEvent) -> bool:
        """Enqueue work for the monitor control loop."""
        if self._shutdown_requested and isinstance(event, _PollRequested):
            return False
        self._event_queue.put(event)
        return True

    def _run_poll_cycle(self) -> None:
        """Check active streams and start new downloads on the control loop."""
        try:
            self._update_downloaders()
            live_streams = self.api.get_livestream_status()
            monitored_live = self._process_live_streams(live_streams)
            live_creator_oids = {stream.creator_oid for stream in live_streams}
            self._cleanup_offline_creator_states(live_creator_oids)
            self._log_status_summary(len(live_streams), monitored_live)
            self._mark_check_succeeded()
        except ConfigError:
            self.logger.warning("Skipping check due to config file error")
            self._mark_check_failed()
        except RPlayAuthError as exc:
            self.logger.error(f"Authentication error: {exc}")
            self.logger.error("Please update your AUTH_TOKEN in .env file")
            self._mark_check_failed()
        except RPlayConnectionError as exc:
            self.logger.warning(f"Connection error (will retry): {exc}")
            self._mark_check_failed()
        except RPlayAPIError as exc:
            self.logger.error(f"API error: {exc}")
            self._mark_check_failed()
        except Exception as exc:
            self.logger.error(f"Unexpected error during monitoring: {exc}")
            self._mark_check_failed()

    def _mark_check_succeeded(self) -> None:
        """Record a successful monitor poll."""
        with self._state_lock:
            self._last_check_success = True

    def _mark_check_failed(self) -> None:
        """Record a failed monitor poll."""
        with self._state_lock:
            self._last_check_success = False

    def _process_live_streams(self, live_streams: List[LiveStream]) -> int:
        """Process monitored live streams and return their count."""
        monitored_live = 0
        for stream in live_streams:
            if stream.stream_state != StreamState.LIVE:
                continue

            with self._state_lock:
                is_monitored = stream.creator_oid in self.monitored_creators

            if not is_monitored:
                continue

            monitored_live += 1
            self._process_live_stream(stream)

        return monitored_live

    def _process_live_stream(self, stream: LiveStream) -> None:
        """Process one monitored live stream candidate."""
        session_key = self._make_session_key(stream)
        self.logger.debug(
            f"Inspecting live stream candidate: creator_oid={stream.creator_oid}, "
            f"stream_oid={stream.oid}, session_key={session_key}, "
            f"started_at={stream.stream_start_time.isoformat()}, title=\"{stream.title}\""
        )
        with self._state_lock:
            self.latest_stream_oid_by_creator[stream.creator_oid] = stream.oid
            self._prune_superseded_terminal_sessions_locked(stream.creator_oid)
            existing_session = self.sessions.get(session_key)

        if existing_session is not None and existing_session.state in {
            SessionState.RAW_RUNNING,
            SessionState.MERGE_QUEUED,
            SessionState.MERGING,
            SessionState.DONE,
            SessionState.BLOCKED,
            SessionState.MERGE_FAILED,
        }:
            self.logger.debug(
                f"Skipping live stream candidate: creator_oid={stream.creator_oid}, "
                f"stream_oid={stream.oid}, session_key={session_key}, "
                f"existing_state={existing_session.state.value}"
            )
            return

        if not self._should_attempt_download(stream):
            self.logger.debug(
                f"Skipping live stream candidate: creator_oid={stream.creator_oid}, "
                f"stream_oid={stream.oid}, session_key={session_key}, "
                "current stream is already marked inaccessible"
            )
            return

        self._start_download(stream)

    def _should_attempt_download(self, stream: LiveStream) -> bool:
        """Check if download should be attempted for this stream."""
        creator_oid = stream.creator_oid
        with self._state_lock:
            state = self._creator_states.get(creator_oid)

        if state is None:
            return True

        if self._is_new_stream_for_creator(stream):
            return True

        return not state.is_current_stream_blocked

    def _cleanup_offline_creator_states(self, live_creator_oids: Set[str]) -> None:
        """Clear state for creators no longer in the live list."""
        with self._state_lock:
            offline_creators = [oid for oid in self._creator_states if oid not in live_creator_oids]
        for creator_oid in offline_creators:
            self._clear_creator_stream_state(creator_oid)

    def _start_download(self, stream: LiveStream) -> None:
        """Start downloading a live stream on the control loop."""
        with self._state_lock:
            creator_profile = self.monitored_creators.get(stream.creator_oid)
        if creator_profile is None:
            return

        creator_name = creator_profile.creator_name
        creator_oid = stream.creator_oid
        session_key = self._make_session_key(stream)

        self._update_creator_stream_state(stream)
        self.logger.info(f"🔴 {creator_name} is live: \"{stream.title}\"")

        session = self._get_or_create_session(stream, creator_name)

        try:
            stream_url = self._get_accessible_stream_url(
                creator_oid=creator_oid,
                session_key=session_key,
                creator_name=creator_name,
            )
            if stream_url is None:
                return

            self._launch_session_downloader(
                session=session,
                stream_url=stream_url,
                title=stream.title,
            )
        except Exception as exc:
            self._handle_start_download_error(session_key, creator_name, exc)

    def _get_accessible_stream_url(
        self,
        creator_oid: str,
        session_key: str,
        creator_name: str,
    ) -> Optional[str]:
        """Get a stream URL and return it only when the m3u8 is accessible."""
        stream_url = self.api.get_stream_url(creator_oid)
        if self.api.validate_m3u8_url(stream_url):
            return stream_url

        self._mark_session_blocked(
            session_key=session_key,
            error_message="Cannot access stream (likely paid content)",
            creator_name=creator_name,
        )
        self.logger.warning(
            f"🔒 {creator_name}: Cannot access stream (likely paid content)"
        )
        return None

    def _launch_session_downloader(
        self,
        session: DownloadSession,
        stream_url: str,
        title: str,
    ) -> None:
        """Create and start the session-scoped downloader thread."""
        active_downloader = StreamDownloader(
            creator_name=session.creator_name,
            on_download_error=self._make_session_download_error_callback(
                session.session_key
            ),
            on_download_auth_error=self._on_raw_download_auth_failed,
            session_key=session.session_key,
            output_dir=session.staging_dir,
            output_extension=".ts",
            on_download_complete=self._on_raw_download_complete,
            on_download_failure=self._on_raw_download_failed,
        )

        active_downloader.download(stream_url, title)
        self.logger.info(f"⬇️  Started downloading: {session.creator_name}")

    def _handle_start_download_error(
        self,
        session_key: str,
        creator_name: str,
        exc: Exception,
    ) -> None:
        """Remove the pending session and log the download-start failure."""
        self._remove_session(session_key)

        if isinstance(exc, RPlayAuthError):
            self.logger.error(
                f"Auth error for {creator_name}: {exc}. "
                "Please verify AUTH_TOKEN and USER_OID credentials."
            )
            return

        if isinstance(exc, RPlayAPIError):
            self.logger.warning(f"Failed to get stream URL for {creator_name}: {exc}")
            return

        self.logger.error(f"Error starting download for {creator_name}: {exc}")

    def _log_status_summary(self, total_live: int, monitored_live: int) -> None:
        """Log a summary of the current monitoring status."""
        with self._state_lock:
            self._check_count += 1
            active_downloads = sum(
                1
                for session in self.sessions.values()
                if session.state == SessionState.RAW_RUNNING
            )
            current_status = {
                "active_downloads": active_downloads,
                "monitored_live": monitored_live,
            }
            state_changed = current_status != self._last_status
            previous_active = self._last_status["active_downloads"]
            periodic_heartbeat = self._check_count % 10 == 0
            monitored_count = self._monitored_count
            self._last_status = current_status

        if state_changed and (active_downloads > 0 or previous_active > 0):
            self.logger.info(
                f"📊 Status: {active_downloads} active download(s), "
                f"{monitored_live}/{monitored_count} monitored creator(s) live"
            )
        elif periodic_heartbeat and monitored_count > 0:
            self.logger.debug(
                f"📊 Checked {total_live} live stream(s), "
                f"none of {monitored_count} monitored creator(s) are live"
            )

    def _update_downloaders(self) -> None:
        """Refresh monitored creator metadata from the current config file."""
        runtime_config = read_config(self.config_path)
        self.api.set_base_url(runtime_config.api_base_url)
        creator_profiles = runtime_config.creators
        new_creators: List[str] = []

        with self._state_lock:
            existing_creator_ids = set(self.monitored_creators)
            self.monitored_creators = {
                profile.creator_oid: profile for profile in creator_profiles
            }
            self._monitored_count = len(self.monitored_creators)

        for profile in creator_profiles:
            if profile.creator_oid not in existing_creator_ids:
                new_creators.append(profile.creator_name)

        if new_creators:
            self.logger.info(f"Added {len(new_creators)} new creator(s) to monitor")

    def get_active_downloads(self) -> List[str]:
        """Get list of creators with active raw download sessions."""
        with self._state_lock:
            return [
                session.creator_name
                for session in self.sessions.values()
                if session.state == SessionState.RAW_RUNNING
            ]

    @property
    def is_healthy(self) -> bool:
        """Check if the last monitoring check was successful."""
        with self._state_lock:
            return self._last_check_success

    def _is_new_stream_for_creator(self, stream: LiveStream) -> bool:
        """Check if the creator is now on a different live stream oid."""
        with self._state_lock:
            state = self._creator_states.get(stream.creator_oid)
        if state is None:
            return True
        return state.last_stream_oid != stream.oid

    def _update_creator_stream_state(self, stream: LiveStream) -> None:
        """Update or create creator state with the current stream oid."""
        with self._state_lock:
            if stream.creator_oid not in self._creator_states:
                self._creator_states[stream.creator_oid] = CreatorStreamState()
            self._creator_states[stream.creator_oid].update_stream_oid(stream.oid)

    def _clear_creator_stream_state(self, creator_oid: str) -> None:
        """Remove creator state when they go offline."""
        with self._state_lock:
            self._creator_states.pop(creator_oid, None)
            self.latest_stream_oid_by_creator.pop(creator_oid, None)
            self._prune_terminal_sessions_for_creator_locked(creator_oid)

    def _prune_superseded_terminal_sessions_locked(self, creator_oid: str) -> None:
        """Drop older terminal sessions once a newer session becomes current."""
        keep_stream_oid = self.latest_stream_oid_by_creator.get(creator_oid)
        if keep_stream_oid is None:
            return

        removable_keys = [
            session_key
            for session_key, session in self.sessions.items()
            if session.creator_oid == creator_oid
            and not session.session_key.endswith(f":{keep_stream_oid}")
            and session.state in self.TERMINAL_SESSION_STATES
        ]
        for session_key in removable_keys:
            self.sessions.pop(session_key, None)

    def _prune_terminal_sessions_for_creator_locked(self, creator_oid: str) -> None:
        """Drop terminal sessions for a creator that is no longer active."""
        removable_keys = [
            session_key
            for session_key, session in self.sessions.items()
            if session.creator_oid == creator_oid
            and session.state in self.TERMINAL_SESSION_STATES
        ]
        for session_key in removable_keys:
            self.sessions.pop(session_key, None)

    def _get_or_create_session(self, stream: LiveStream, creator_name: str) -> DownloadSession:
        """Get existing session or create a new session record."""
        session_key = self._make_session_key(stream)
        with self._state_lock:
            if session_key not in self.sessions:
                self.sessions[session_key] = DownloadSession(
                    session_key=session_key,
                    creator_oid=stream.creator_oid,
                    creator_name=creator_name,
                    title=stream.title,
                    stream_start_time=stream.stream_start_time,
                    state=SessionState.RAW_RUNNING,
                    staging_dir=self._build_staging_dir(creator_name, session_key),
                )
            return self.sessions[session_key]

    def _remove_session(self, session_key: str) -> None:
        """Remove a session that failed before raw download started."""
        with self._state_lock:
            self.sessions.pop(session_key, None)

    def _make_session_key(self, stream: LiveStream) -> str:
        """Build a stable key for one live session."""
        return f"{stream.creator_oid}:{stream.oid}"

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

    def _on_raw_download_complete(self, event: RawDownloadCompleted) -> None:
        """Receive raw completion from a downloader thread and queue it."""
        self._queue_monitor_event(event)

    def _on_raw_download_auth_failed(self, event: RawDownloadAuthFailed) -> None:
        """Receive raw download auth failure from a downloader thread and queue it."""
        self._queue_monitor_event(event)

    def _on_raw_download_failed(self, event: RawDownloadFailed) -> None:
        """Receive raw download failure from a downloader thread and queue it."""
        self._queue_monitor_event(event)

    def _handle_monitor_event(self, event: SessionEvent) -> None:
        """Apply one monitor event on the control loop."""
        if isinstance(event, RawDownloadCompleted):
            self._handle_raw_download_completed(event)
            return

        if isinstance(event, RawDownloadBlocked):
            self._handle_raw_download_blocked(event)
            return

        if isinstance(event, RawDownloadAuthFailed):
            self._handle_raw_download_auth_failed(event)
            return

        if isinstance(event, RawDownloadFailed):
            self._handle_raw_download_failed(event)
            return

        log_method: Optional[Callable[[str], None]] = None
        log_message: Optional[str] = None
        with self._state_lock:
            session = self.sessions.get(event.session_key)
            if session is None:
                return

            if isinstance(event, MergeStarted):
                session.state = SessionState.MERGING
                log_method = self.logger.info
                log_message = (
                    f"🎬 Merge started for {session.creator_name}: {session.session_key}"
                )
            elif isinstance(event, MergeCompleted):
                session.final_output_path = event.output_path
                session.last_error = None
                session.state = SessionState.DONE
                log_method = self.logger.info
                log_message = (
                    f"✅ Merge completed for {session.creator_name}: {event.output_path}"
                )
            elif isinstance(event, MergeFailed):
                session.last_error = event.error_message
                session.staging_dir = event.failed_staging_dir
                session.state = SessionState.MERGE_FAILED
                log_method = self.logger.warning
                log_message = (
                    f"⚠️ Merge failed for {session.creator_name}: {event.error_message}. "
                    f"Raw files preserved at: {event.failed_staging_dir}"
                )
            else:
                self.logger.error(f"Unhandled session event type: {type(event)}")
                return

        if log_method is not None and log_message is not None:
            log_method(log_message)

    def _handle_raw_download_completed(self, event: RawDownloadCompleted) -> None:
        """Queue merge work as soon as raw download completes."""
        with self._state_lock:
            session = self.sessions.get(event.session_key)
            if session is None:
                return

            session.state = SessionState.MERGE_QUEUED
            session.staging_dir = event.staging_dir
            merge_job = MergeJobSpec(
                session_key=session.session_key,
                creator_name=session.creator_name,
                title=session.title,
                stream_start_time=session.stream_start_time,
                staging_dir=session.staging_dir,
            )

        self.merge_executor.submit_merge(lambda: self._run_merge_job(merge_job))
        self.logger.info(
            f"🧩 Queued merge for {merge_job.creator_name}: "
            f"session_key={merge_job.session_key}, staging_dir={merge_job.staging_dir}"
        )

    def _handle_raw_download_auth_failed(self, event: RawDownloadAuthFailed) -> None:
        """Clear auth-failed raw sessions and surface credential guidance."""
        with self._state_lock:
            session = self.sessions.pop(event.session_key, None)

        if session is None:
            return

        self._mark_check_failed()
        self.logger.error(
            f"🔐 Authentication error while downloading {session.creator_name}: "
            f"{event.error_message}. Please verify AUTH_TOKEN and USER_OID credentials."
        )

    def _handle_raw_download_failed(self, event: RawDownloadFailed) -> None:
        """Clear failed raw sessions so the next poll can retry them."""
        with self._state_lock:
            session = self.sessions.pop(event.session_key, None)

        if session is None:
            return

        self.logger.warning(
            f"⚠️ Raw download failed for {session.creator_name}; will retry on next poll: "
            f"{event.error_message}"
        )

    def _handle_raw_download_blocked(self, event: RawDownloadBlocked) -> None:
        """Apply blocked-session state when downloader reports access failure."""
        with self._state_lock:
            session = self.sessions.get(event.session_key)
            if session is None:
                return

            session.last_error = event.error_message
            session.state = SessionState.BLOCKED
            creator_name = session.creator_name
            creator_oid = session.creator_oid
            state = self._creator_states.get(creator_oid)
            if state is None:
                state = CreatorStreamState()
                self._creator_states[creator_oid] = state
            was_blocked = state.is_current_stream_blocked
            state.mark_blocked()

        if not was_blocked:
            self.logger.warning(
                f"🔒 {creator_name}: Stream marked as inaccessible "
                f"after download failure (likely paid content)"
            )

    def _mark_session_blocked(
        self,
        session_key: str,
        error_message: str,
        creator_name: str,
    ) -> None:
        """Mark a session blocked directly on the control loop."""
        with self._state_lock:
            session = self.sessions.get(session_key)
            if session is None:
                return

            session.last_error = error_message
            session.state = SessionState.BLOCKED
            state = self._creator_states.get(session.creator_oid)
            if state is None:
                state = CreatorStreamState()
                self._creator_states[session.creator_oid] = state
            was_blocked = state.is_current_stream_blocked
            state.mark_blocked()

        if not was_blocked:
            self.logger.warning(
                f"🔒 {creator_name}: Stream marked as inaccessible "
                f"after download failure (likely paid content)"
            )

    def _run_merge_job(self, merge_job: MergeJobSpec) -> None:
        """Run merge I/O work on the merge executor and emit events back to monitor."""
        self._queue_monitor_event(MergeStarted(session_key=merge_job.session_key))
        result = self._merge_session_to_mp4(merge_job)
        self._queue_monitor_event(result)

    def _merge_session_to_mp4(self, merge_job: MergeJobSpec) -> Union[MergeCompleted, MergeFailed]:
        """Merge one session's raw ts outputs into the final mp4 artifact."""
        ts_files = sorted(merge_job.staging_dir.glob("*.ts"))

        try:
            if not ts_files:
                raise FileNotFoundError(
                    f"No ts files found for session {merge_job.session_key}"
                )

            output_path = self._reserve_final_output_path(
                creator_name=merge_job.creator_name,
                title=merge_job.title,
                stream_start_time=merge_job.stream_start_time,
            )
            self._run_ffmpeg_merge(ts_files, output_path)

            for ts_file in ts_files:
                ts_file.unlink(missing_ok=True)

            if merge_job.staging_dir.exists():
                shutil.rmtree(merge_job.staging_dir)

            return MergeCompleted(
                session_key=merge_job.session_key,
                output_path=output_path,
            )

        except subprocess.TimeoutExpired as exc:
            failed_dir = self._move_failed_staging_dir(
                creator_name=merge_job.creator_name,
                session_key=merge_job.session_key,
                staging_dir=merge_job.staging_dir,
            )
            timeout_value = int(exc.timeout) if exc.timeout is not None else self.merge_timeout_seconds
            return MergeFailed(
                session_key=merge_job.session_key,
                error_message=f"ffmpeg merge timeout after {timeout_value} seconds",
                failed_staging_dir=failed_dir,
            )
        except Exception as exc:
            failed_dir = self._move_failed_staging_dir(
                creator_name=merge_job.creator_name,
                session_key=merge_job.session_key,
                staging_dir=merge_job.staging_dir,
            )
            return MergeFailed(
                session_key=merge_job.session_key,
                error_message=str(exc),
                failed_staging_dir=failed_dir,
            )

    def _reserve_final_output_path(
        self,
        creator_name: str,
        title: str,
        stream_start_time: datetime,
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
            self._format_ffconcat_input_path(ts_file) for ts_file in ts_files
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
                timeout=self.merge_timeout_seconds,
            )
        finally:
            list_path.unlink(missing_ok=True)

    def _format_ffconcat_input_path(self, ts_file: Path) -> str:
        """Format one concat-demuxer input line with apostrophe-safe escaping."""
        escaped_path = ts_file.resolve().as_posix().replace("'", "'\''")
        return f"file '{escaped_path}'"

    def _move_failed_staging_dir(
        self,
        creator_name: str,
        session_key: str,
        staging_dir: Path,
    ) -> Path:
        """Move failed raw session files into the visible failed directory."""
        failed_dir = (
            Path.cwd()
            / StreamDownloader.ARCHIVE_DIR
            / creator_name
            / "_failed"
            / self._make_session_dir_name(session_key)
        )

        if not staging_dir.exists():
            return failed_dir

        failed_dir.parent.mkdir(parents=True, exist_ok=True)
        if failed_dir.exists():
            shutil.rmtree(failed_dir)
        shutil.move(staging_dir, failed_dir)
        return failed_dir

    def shutdown(self) -> None:
        """Shut down background monitor and merge work."""
        if self._shutdown_requested:
            return

        self._shutdown_requested = True
        self._event_queue.join()
        self.merge_executor.shutdown(wait=True)
        self._event_queue.join()
        self._event_queue.put(_ShutdownRequested())
        self._control_thread.join()

    def _make_session_download_error_callback(self, session_key: str) -> Callable[[str], None]:
        """Create a callback for a specific session download failure."""

        def _on_error(error_message: str) -> None:
            self._queue_monitor_event(
                RawDownloadBlocked(
                    session_key=session_key,
                    error_message=error_message,
                )
            )

        return _on_error

