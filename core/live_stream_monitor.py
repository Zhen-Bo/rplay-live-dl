"""Live stream monitoring module."""

import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from threading import Event, RLock, Thread
from time import monotonic
from typing import Callable, Dict, List, Optional, Set, Union

from pathvalidate import sanitize_filename

from core.constants import (
    DEFAULT_MERGE_TIMEOUT_SECONDS as _DEFAULT_MERGE_TIMEOUT_SECONDS,
    DEFAULT_MIN_FREE_DISK_GB,
)
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
from .health import touch_heartbeat
from .logger import bind, clip, setup_logger
from .rplay import RPlayAPI, RPlayAPIError, RPlayAuthError, RPlayConnectionError
from .utils import merge_ts_files_to_mp4, terminate_child_processes

__all__ = [
    "LiveStreamMonitor",
]


@dataclass(frozen=True)
class _PollRequested:
    """Internal control-loop event requesting one monitor poll."""

    done: Event
    # Marks the extra poll queued after a download failure. The control loop
    # uses it to reopen retry deduplication; nobody waits on its done event.
    retry: bool = False


@dataclass(frozen=True)
class _DrainRequested:
    """Internal control-loop marker signalling that earlier events were handled."""

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
    _DrainRequested,
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

    DEFAULT_MERGE_TIMEOUT_SECONDS = _DEFAULT_MERGE_TIMEOUT_SECONDS
    POLL_WAIT_TIMEOUT_SECONDS = 30.0
    # One aggregate budget for the whole shutdown, not a per-step timeout that
    # the next step can extend: every wait below draws from the same deadline,
    # so SIGTERM to returned is bounded end to end. docker-compose.yaml's
    # stop_grace_period is set from this value plus margin.
    # ponytail: one budget for all phases; split it per phase only if a slow
    # merge is ever seen starving the recording joins.
    SHUTDOWN_BUDGET_SECONDS = 600.0
    # Cap for the fast phases so they cannot eat the merge's share of the budget.
    SHUTDOWN_PHASE_TIMEOUT_SECONDS = 30.0
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
        min_free_disk_gb: float = DEFAULT_MIN_FREE_DISK_GB,
    ) -> None:
        """
        Initialize monitor with authentication and configuration.

        Args:
            auth_token: JWT token for API auth
            user_oid: User's identifier
            config_path: Path to creator profiles YAML config
            api: Optional RPlayAPI instance for dependency injection (testing)
            merge_timeout_seconds: Timeout for ffmpeg merge commands
            min_free_disk_gb: Minimum free disk space in GiB before recording; 0 disables
        """
        self.api = api if api is not None else RPlayAPI(auth_token, user_oid)
        self.config_path = config_path
        self.merge_timeout_seconds = merge_timeout_seconds
        self.min_free_disk_gb = min_free_disk_gb
        self.monitored_creators: Dict[str, CreatorProfile] = {}
        self.sessions: Dict[str, DownloadSession] = {}
        self.latest_stream_oid_by_creator: Dict[str, str] = {}
        self._active_raw_session_by_creator: Dict[str, str] = {}
        self.merge_executor = DownloadMergeExecutor(max_workers=1)
        self.logger = setup_logger("Monitor")

        self._state_lock = RLock()
        self._event_queue: Queue[MonitorRuntimeEvent] = Queue()
        self._shutdown_requested = False
        # Creators the most recent poll saw actually live. A download failure
        # only earns an immediate re-poll while its creator is still in here.
        # Not latest_stream_oid_by_creator: its cleanup checks the unfiltered
        # live list, so a creator who moved to StreamState.TWITCH or YOUTUBE
        # keeps their entry there and would earn a re-poll per failure for a
        # stream this app cannot record.
        self._live_creator_oids: Set[str] = set()
        # True while a retry poll sits in the queue unstarted, so concurrent
        # failures merge into one extra poll instead of one poll each.
        self._retry_poll_queued = False
        # Recording downloaders, so shutdown can stop them and wait for their
        # terminal events instead of discovering them as orphaned ffmpeg pids.
        self._active_downloaders: Dict[str, StreamDownloader] = {}
        # Pids of merge ffmpeg children this monitor owns. Reaping recordings
        # skips these, otherwise shutdown would kill a merge in progress.
        self._merge_process_pids: Set[int] = set()
        self._shutdown_deadline: Optional[float] = None
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
        # ponytail: one bool; re-armed on successful key2 (get_stream_url).
        self._auth_error_notified = False
        # ponytail: per-cycle only, no TTL — key2 is user-scoped, not creator-scoped.
        self._cycle_stream_key: Optional[str] = None
        # Unrecovered key2 auth failure this cycle → mark poll unhealthy at end.
        self._cycle_key_fetch_auth_failed = False
        # One warning if the heartbeat file cannot be written; never spam logs.
        self._heartbeat_write_warned = False

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
                    if event.retry:
                        with self._state_lock:
                            # Reopened before the cycle, not after: a failure
                            # raised while this poll runs must be able to earn
                            # the next re-poll rather than merge into one that
                            # already read the live list.
                            self._retry_poll_queued = False
                            shutdown_started = self._shutdown_requested
                        if shutdown_started:
                            # Queued before shutdown began. A recording it
                            # started would be refused anyway, so serving it
                            # only adds a live-list read to the window
                            # api.close() is about to close.
                            event.done.set()
                            continue
                    try:
                        self._run_poll_cycle()
                    finally:
                        event.done.set()
                    continue

                if isinstance(event, _DrainRequested):
                    # FIFO: reaching this marker means everything queued ahead
                    # of it has already been applied.
                    event.done.set()
                    continue

                self._handle_monitor_event(event)
            except Exception as exc:
                self.logger.exception(f"Unexpected control-loop error: {exc}")
                if isinstance(event, (_PollRequested, _DrainRequested)):
                    event.done.set()
            finally:
                self._event_queue.task_done()

    def _queue_monitor_event(self, event: MonitorRuntimeEvent) -> bool:
        """Enqueue work for the monitor control loop."""
        # Refusal and enqueue as one step, under the lock shutdown takes to set
        # its flag. Checked unlocked, shutdown slips its whole drain in between
        # and the poll is served after it: a live-list read racing api.close().
        # Put on an unbounded Queue never blocks, so this holds the lock only
        # for the append.
        with self._state_lock:
            if self._shutdown_requested and isinstance(event, _PollRequested):
                return False
            self._event_queue.put(event)
        return True

    def _request_retry_poll(self, creator_oid: str) -> bool:
        """Queue at most one immediate retry poll."""
        # Signals rather than polls: the public poll waits on the very loop that
        # serves it, and failures are handled on that loop.
        # One region for check, flag and enqueue, so a shutdown lands wholly
        # before this (refused) or wholly after (skipped at dequeue), never in
        # between. RLock, so the nested acquire below is free.
        with self._state_lock:
            if creator_oid not in self._live_creator_oids:
                # Creator is offline; there is nothing left to record.
                return False
            if self._retry_poll_queued:
                # Concurrent failures merge: one poll re-reads the whole live
                # list, so a second request would find the same work done.
                return False
            self._retry_poll_queued = True

            if self._queue_monitor_event(_PollRequested(done=Event(), retry=True)):
                return True

            # Refused because shutdown started. Clear the marker so it cannot
            # wedge deduplication for a monitor that keeps running.
            self._retry_poll_queued = False
            return False

    def _drain_monitor_events(self, timeout: float) -> bool:
        """
        Wait until events queued so far have been applied by the control loop.

        Uses an ordered marker rather than Queue.join() so the wait is bounded:
        shutdown must never block forever on a control loop that is wedged.
        """
        done = Event()
        self._event_queue.put(_DrainRequested(done=done))
        if done.wait(timeout=timeout):
            return True

        self.logger.warning(
            f"Monitor events did not drain within {timeout:.0f}s; continuing shutdown"
        )
        return False

    def _run_poll_cycle(self) -> None:
        """Check active streams and start new downloads on the control loop."""
        # Unconditional: never carry key2 across poll cycles (incl. A3 retry polls).
        self._cycle_stream_key = None
        self._cycle_key_fetch_auth_failed = False
        try:
            self._update_downloaders()
            live_streams = self.api.get_livestream_status()
            # Recorded before any download starts, so a session that fails fast
            # is judged against the list this very poll read.
            with self._state_lock:
                self._live_creator_oids = {
                    stream.creator_oid
                    for stream in live_streams
                    if stream.stream_state == StreamState.LIVE
                }
            monitored_live = self._process_live_streams(live_streams)
            live_creator_oids = {stream.creator_oid for stream in live_streams}
            self._cleanup_offline_creator_states(live_creator_oids)
            self._log_status_summary(len(live_streams), monitored_live)
            # Match playlist-401 health: unrecovered key2 auth fails the cycle.
            if self._cycle_key_fetch_auth_failed:
                self._mark_check_failed()
            else:
                self._mark_check_succeeded()
        except ConfigError:
            self.logger.warning("Skipping check due to config file error")
            self._mark_check_failed()
        except RPlayAuthError as exc:
            self._log_auth_error(
                f"Authentication error: {exc}. "
                "Please update your AUTH_TOKEN in .env file"
            )
            self._mark_check_failed()
        except RPlayConnectionError as exc:
            self.logger.warning(f"Connection error (will retry): {exc}")
            self._mark_check_failed()
        except RPlayAPIError as exc:
            self.logger.error(f"API error: {exc}")
            self._mark_check_failed()
        except Exception as exc:
            self.logger.exception(f"Unexpected error during monitoring: {exc}")
            self._mark_check_failed()
        finally:
            # Heartbeat once per cycle so Docker can see the monitor is polling.
            try:
                touch_heartbeat()
            except OSError as exc:
                if not self._heartbeat_write_warned:
                    self._heartbeat_write_warned = True
                    self.logger.warning(f"Failed to write heartbeat file: {exc}")

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
        with self._state_lock:
            self.latest_stream_oid_by_creator[stream.creator_oid] = stream.oid
            self._prune_superseded_terminal_sessions_locked(
                stream.creator_oid,
                stream.stream_start_time,
            )
            creator_state = self._creator_states.get(stream.creator_oid)
            tracked_started_at = (
                creator_state.last_stream_start_time.isoformat()
                if creator_state is not None
                and creator_state.last_stream_start_time is not None
                else "None"
            )
            active_session_key = self._active_raw_session_by_creator.get(stream.creator_oid)
            active_session = (
                self.sessions.get(active_session_key)
                if active_session_key is not None
                else None
            )

        candidate_session_key = active_session_key or "pending_local_session"
        self.logger.debug(
            f"Inspecting live stream candidate: creator_oid={stream.creator_oid}, "
            f"stream_oid={stream.oid}, session_key={candidate_session_key}, "
            f"started_at={stream.stream_start_time.isoformat()}, "
            f"tracked_started_at={tracked_started_at}, "
            f"active_raw_session_key={active_session_key}, "
            f"active_raw_state={active_session.state.value if active_session else 'none'}, "
            f'title="{stream.title}"'
        )

        if active_session is not None and active_session.state == SessionState.RAW_RUNNING:
            active_recording_started_at = (
                active_session.recording_started_at.isoformat()
                if active_session.recording_started_at is not None
                else "None"
            )
            self.logger.debug(
                f"Skipping live stream candidate: creator_oid={stream.creator_oid}, "
                f"stream_oid={stream.oid}, session_key={candidate_session_key}, "
                f"reason=active_raw_running, active_session_key={active_session.session_key}, "
                f"active_recording_started_at={active_recording_started_at}"
            )
            return

        if not self._should_attempt_download(stream):
            self.logger.debug(
                f"Skipping live stream candidate: creator_oid={stream.creator_oid}, "
                f"stream_oid={stream.oid}, session_key={candidate_session_key}, "
                f"reason=current_stream_blocked, tracked_started_at={tracked_started_at}"
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
            if self._shutdown_requested:
                # Shutdown already snapshotted the recordings it has to stop; a
                # session started now would never be stopped or merged.
                return
            creator_profile = self.monitored_creators.get(stream.creator_oid)
        if creator_profile is None:
            return

        creator_name = creator_profile.creator_name
        creator_oid = stream.creator_oid
        output_dir = self._build_session_output_dir(creator_name)

        if self.min_free_disk_gb > 0:
            check_path = next(
                p for p in (output_dir, *output_dir.parents) if p.exists()
            )
            try:
                free_bytes = shutil.disk_usage(check_path).free
            except OSError as exc:
                # ponytail: availability beats blocking recordings on a broken statvfs
                self.logger.warning(
                    f"Could not check free disk space for {output_dir} "
                    f"(via {check_path}): {exc}; allowing session"
                )
            else:
                required_bytes = int(self.min_free_disk_gb * (1024 ** 3))
                if free_bytes < required_bytes:
                    free_gb = free_bytes / (1024 ** 3)
                    self.logger.error(
                        f"Insufficient free disk space to start recording: "
                        f"path={output_dir}, free={free_gb:.4f} GiB "
                        f"({free_bytes} bytes), "
                        f"required={self.min_free_disk_gb:g} GiB "
                        f"({required_bytes} bytes)"
                    )
                    return

        recording_started_at = datetime.now(timezone.utc)

        self._update_creator_stream_state(stream)
        session = self._get_or_create_session(
            stream=stream,
            creator_name=creator_name,
            recording_started_at=recording_started_at,
        )
        bind(self.logger, creator_name).info(f'🔴 Live: "{clip(stream.title)}"')

        try:
            if self._cycle_stream_key is not None:
                stream_key = self._cycle_stream_key
            else:
                # Real fetch: only successes are cached; failures leave the slot empty.
                stream_key = self.api._get_stream_key()
                self._cycle_stream_key = stream_key
                # A later success clears an earlier unrecovered auth failure this cycle.
                self._cycle_key_fetch_auth_failed = False
                # Successful key2 re-arms auth-error logging for the next failure streak.
                # Cache hits skip re-arm — they follow a success already in this cycle.
                self._auth_error_notified = False
            stream_url = self.api.get_stream_url(creator_oid, stream_key=stream_key)
            self._launch_session_downloader(
                session=session,
                stream_url=stream_url,
                title=stream.title,
            )
        except Exception as exc:
            self._handle_start_download_error(session.session_key, creator_name, exc)

    def _launch_session_downloader(
        self,
        session: DownloadSession,
        stream_url: str,
        title: str,
    ) -> None:
        """Create, register, and start the session-scoped downloader thread."""
        active_downloader = StreamDownloader(
            creator_name=session.creator_name,
            on_download_error=self._make_session_download_error_callback(
                session.session_key
            ),
            on_download_auth_error=self._on_raw_download_auth_failed,
            session_key=session.session_key,
            output_dir=session.output_dir,
            output_extension=".ts",
            filename_prefix=session.session_prefix,
            on_download_complete=self._on_raw_download_complete,
            on_download_failure=self._on_raw_download_failed,
        )

        # Registered before the thread starts: shutdown snapshots this map, and
        # a recording it cannot see is a recording it cannot stop or merge.
        # Rechecked here rather than only at poll entry: get_stream_url blocks on
        # the network, and shutdown can take its recording snapshot while this
        # poll sits in that call. Starting afterwards would create a recording
        # nobody stops and a merge nobody accepts.
        with self._state_lock:
            shutdown_started = self._shutdown_requested
            if not shutdown_started:
                self._active_downloaders[session.session_key] = active_downloader

        if shutdown_started:
            self._remove_session(session.session_key)
            self.logger.warning(
                f"Dropped pending session for {session.creator_name} "
                f"({session.session_key}): shutdown started while this poll was "
                "fetching the stream URL"
            )
            return

        # The downloader logs "Recording started" itself; repeating it here added
        # a line that carried no information the previous one did not already have.
        active_downloader.download(stream_url, title)

    def _handle_start_download_error(
        self,
        session_key: str,
        creator_name: str,
        exc: Exception,
    ) -> None:
        """Remove the pending session and log the download-start failure."""
        self._remove_session(session_key)

        if isinstance(exc, RPlayAuthError):
            self._cycle_key_fetch_auth_failed = True
            self._log_auth_error(
                f"Auth error for {creator_name}: {exc}. "
                "Please verify AUTH_TOKEN and USER_OID credentials."
            )
            return

        if isinstance(exc, RPlayAPIError):
            self.logger.warning(f"Failed to get stream URL for {creator_name}: {exc}")
            return

        self.logger.error(f"Error starting download for {creator_name}: {exc}", exc_info=exc)

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
            self.logger.info(
                f"📊 Checked {total_live} live stream(s), "
                f"none of {monitored_count} monitored creator(s) are live"
            )

    def _update_downloaders(self) -> None:
        """Refresh monitored creator metadata from the current config file."""
        runtime_config = read_config(self.config_path)
        self.api.set_base_url(runtime_config.api_base_url)
        creator_profiles = runtime_config.creators

        with self._state_lock:
            previous_creators = self.monitored_creators.copy()
            self.monitored_creators = {
                profile.creator_oid: profile for profile in creator_profiles
            }
            self._monitored_count = len(self.monitored_creators)

        previous_creator_ids = set(previous_creators)
        current_creator_ids = {profile.creator_oid for profile in creator_profiles}
        new_creators = [
            profile.creator_name
            for profile in creator_profiles
            if profile.creator_oid not in previous_creator_ids
        ]
        removed_creators = [
            profile.creator_name
            for creator_oid, profile in previous_creators.items()
            if creator_oid not in current_creator_ids
        ]

        if new_creators:
            self.logger.info(
                f"Added {len(new_creators)} new creator(s) to monitor"
                f"{self._format_creator_name_summary(new_creators)}"
            )
        if removed_creators:
            self.logger.info(
                f"Removed {len(removed_creators)} creator(s) from monitor"
                f"{self._format_creator_name_summary(removed_creators)}"
            )

    @staticmethod
    def _format_creator_name_summary(creator_names: List[str]) -> str:
        """Format a concise creator-name summary for info logs."""
        if not creator_names:
            return ""
        if len(creator_names) <= 5:
            return f": {', '.join(creator_names)}"
        preview = ", ".join(creator_names[:5])
        return f": {preview}, +{len(creator_names) - 5} more"

    def _resolve_creator_name_locked(self, creator_oid: str) -> str:
        """Resolve a creator name from monitored config or known sessions."""
        profile = self.monitored_creators.get(creator_oid)
        if profile is not None:
            return profile.creator_name
        for session in self.sessions.values():
            if session.creator_oid == creator_oid:
                return session.creator_name
        return creator_oid

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

    def _update_creator_stream_state(self, stream: LiveStream) -> None:
        """Update or create creator state with the current handled stream metadata."""
        with self._state_lock:
            if stream.creator_oid not in self._creator_states:
                self._creator_states[stream.creator_oid] = CreatorStreamState()
            self._creator_states[stream.creator_oid].update_stream_start_time(
                stream.stream_start_time,
                stream.oid,
            )

    def _clear_creator_stream_state(self, creator_oid: str) -> None:
        """Remove creator state when they go offline."""
        with self._state_lock:
            creator_name = self._resolve_creator_name_locked(creator_oid)
            creator_state = self._creator_states.pop(creator_oid, None)
            self.latest_stream_oid_by_creator.pop(creator_oid, None)
            released_raw_lock = self._active_raw_session_by_creator.pop(creator_oid, None)
            pruned_terminal_sessions = self._prune_terminal_sessions_for_creator_locked(
                creator_oid
            )
            blocked = (
                creator_state.is_current_stream_blocked if creator_state is not None else False
            )
            should_log = (
                creator_state is not None
                or released_raw_lock is not None
                or pruned_terminal_sessions > 0
            )

        if should_log:
            self.logger.info(
                f"Cleared creator state for {creator_name}: blocked={blocked}, "
                f"released_raw_lock={released_raw_lock is not None}, "
                f"pruned_terminal_sessions={pruned_terminal_sessions}"
            )

    def _prune_superseded_terminal_sessions_locked(
        self,
        creator_oid: str,
        current_stream_start_time: datetime | None = None,
    ) -> None:
        """Drop older terminal sessions once a newer session becomes current."""
        target_start_time = current_stream_start_time
        if target_start_time is None:
            state = self._creator_states.get(creator_oid)
            if state is None or state.last_stream_start_time is None:
                return
            target_start_time = state.last_stream_start_time

        removable_keys = [
            session_key
            for session_key, session in self.sessions.items()
            if session.creator_oid == creator_oid
            and session.stream_start_time != target_start_time
            and session.state in self.TERMINAL_SESSION_STATES
        ]
        for session_key in removable_keys:
            self.sessions.pop(session_key, None)

    def _prune_terminal_sessions_for_creator_locked(self, creator_oid: str) -> int:
        """Drop terminal sessions for a creator that is no longer active."""
        removable_keys = [
            session_key
            for session_key, session in self.sessions.items()
            if session.creator_oid == creator_oid
            and session.state in self.TERMINAL_SESSION_STATES
        ]
        for session_key in removable_keys:
            self.sessions.pop(session_key, None)
        return len(removable_keys)

    def _get_or_create_session(
        self,
        stream: LiveStream,
        creator_name: str,
        recording_started_at: datetime,
    ) -> DownloadSession:
        """Create a new local recording session and acquire the creator raw lock."""
        with self._state_lock:
            session_key = self._make_session_key(stream.creator_oid, recording_started_at)
            if session_key in self.sessions:
                suffix = 1
                base_session_key = session_key
                while session_key in self.sessions:
                    session_key = f"{base_session_key}-{suffix}"
                    suffix += 1

            session_prefix = self._make_session_prefix(recording_started_at)
            output_dir = self._build_session_output_dir(creator_name)
            self.sessions[session_key] = DownloadSession(
                session_key=session_key,
                creator_oid=stream.creator_oid,
                creator_name=creator_name,
                title=stream.title,
                stream_start_time=stream.stream_start_time,
                state=SessionState.RAW_RUNNING,
                output_dir=output_dir,
                session_prefix=session_prefix,
                recording_started_at=recording_started_at,
            )
            self._active_raw_session_by_creator[stream.creator_oid] = session_key
            return self.sessions[session_key]

    def _remove_session(self, session_key: str) -> None:
        """Remove a session that failed before raw download started."""
        with self._state_lock:
            self._active_downloaders.pop(session_key, None)
            session = self.sessions.pop(session_key, None)
            if session is not None:
                active_session_key = self._active_raw_session_by_creator.get(
                    session.creator_oid
                )
                if active_session_key == session_key:
                    self._active_raw_session_by_creator.pop(session.creator_oid, None)

    def _make_session_key(
        self,
        creator_oid: str,
        recording_started_at: datetime,
    ) -> str:
        """Build a session key from the local recording task start time."""
        if recording_started_at.tzinfo is None:
            recording_started_at = recording_started_at.replace(tzinfo=timezone.utc)
        else:
            recording_started_at = recording_started_at.astimezone(timezone.utc)
        return f"{creator_oid}:{int(recording_started_at.timestamp() * 1000)}"

    def _build_session_output_dir(self, creator_name: str) -> Path:
        """Return the flat output directory for a creator's recordings."""
        return Path.cwd() / StreamDownloader.ARCHIVE_DIR / creator_name

    def _make_session_prefix(self, recording_started_at: datetime) -> str:
        """Build the filename prefix from the local recording start time."""
        local_dt = recording_started_at.astimezone().replace(tzinfo=None)
        return local_dt.strftime("%Y%m%d_%H%M%S_")

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
        if isinstance(
            event,
            (
                RawDownloadCompleted,
                RawDownloadBlocked,
                RawDownloadAuthFailed,
                RawDownloadFailed,
            ),
        ):
            # One pop for every raw terminal outcome: whichever of the four
            # arrived, that session's downloader is done and shutdown must not
            # keep it in the set of recordings it waits on.
            with self._state_lock:
                self._active_downloaders.pop(event.session_key, None)

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
                session.state = SessionState.MERGE_FAILED
                log_method = self.logger.warning
                log_message = (
                    f"⚠️ Merge failed for {session.creator_name}: {event.error_message}. "
                    f"Raw .ts files left in: {session.output_dir}"
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
            active_session_key = self._active_raw_session_by_creator.get(session.creator_oid)
            if active_session_key == session.session_key:
                self._active_raw_session_by_creator.pop(session.creator_oid, None)
            merge_job = MergeJobSpec(
                session_key=session.session_key,
                creator_name=session.creator_name,
                title=session.title,
                stream_start_time=session.stream_start_time,
                output_dir=session.output_dir,
                session_prefix=session.session_prefix,
            )

            try:
                self.merge_executor.submit_merge(lambda: self._run_merge_job(merge_job))
            except RuntimeError:
                # A recording that outlived shutdown's join budget reports here
                # after the executor closed. Ignoring it idempotently is the
                # contract: re-raising would crash the control loop, and
                # re-queueing would wait on an executor that never reopens.
                # ponytail: late (>join budget) completions stay orphaned;
                # startup recovery lane will merge them.
                session.state = SessionState.MERGE_FAILED
                session.last_error = "merge submission closed by shutdown"
                late_creator_name = session.creator_name
                late_output_dir = session.output_dir
            else:
                late_output_dir = None

        if late_output_dir is not None:
            self.logger.warning(
                f"⚠️ Raw download for {late_creator_name} finished after shutdown "
                f"closed merge submission. Raw .ts files left in: {late_output_dir}"
            )
            return

        self.logger.info(
            f"🧩 Queued merge for {merge_job.creator_name}: "
            f"session_key={merge_job.session_key}, output_dir={merge_job.output_dir}"
        )

    def _handle_raw_download_auth_failed(self, event: RawDownloadAuthFailed) -> None:
        """Clear auth-failed raw sessions and surface credential guidance."""
        with self._state_lock:
            session = self.sessions.pop(event.session_key, None)
            if session is not None:
                active_session_key = self._active_raw_session_by_creator.get(
                    session.creator_oid
                )
                if active_session_key == session.session_key:
                    self._active_raw_session_by_creator.pop(session.creator_oid, None)

        if session is None:
            return

        self._mark_check_failed()
        self._log_auth_error(
            f"🔐 Authentication error while downloading {session.creator_name}: "
            f"{event.error_message}. Please verify AUTH_TOKEN and USER_OID credentials."
        )

    def _log_auth_error(self, message: str) -> None:
        """Log auth errors once per failure streak; repeats go to DEBUG."""
        log = self.logger.debug if self._auth_error_notified else self.logger.error
        self._auth_error_notified = True
        log(message)

    def _handle_raw_download_failed(self, event: RawDownloadFailed) -> None:
        """Clear the failed raw session and re-poll at once if the creator is still live."""
        with self._state_lock:
            session = self.sessions.pop(event.session_key, None)
            if session is not None:
                active_session_key = self._active_raw_session_by_creator.get(
                    session.creator_oid
                )
                if active_session_key == session.session_key:
                    self._active_raw_session_by_creator.pop(session.creator_oid, None)

        if session is None:
            return

        # Waiting for the next scheduled poll costs up to a whole INTERVAL (up
        # to 3600s) of a stream that is still running. Requested only after the
        # session has been cleared above, so the extra poll sees the creator
        # free to start again rather than skipping it as already recording.
        # ponytail: no separate retry budget here. Most of these arrive only
        # after the downloader spent its tenacity attempts with exponential
        # backoff, which keeps the extra poll off a hot loop. Not all:
        # downloader.py's "finished but produced no output file" path reports
        # with no backoff behind it, so that mode re-polls as fast as it fails.
        # Add a budget if failures are seen recurring faster than the backoff.
        retried_now = self._request_retry_poll(session.creator_oid)
        next_attempt = "retrying immediately" if retried_now else "will retry on next poll"
        self.logger.warning(
            f"⚠️ Raw download failed for {session.creator_name}; {next_attempt}: "
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
            active_session_key = self._active_raw_session_by_creator.get(session.creator_oid)
            if active_session_key == session.session_key:
                self._active_raw_session_by_creator.pop(session.creator_oid, None)
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

    def _run_merge_job(self, merge_job: MergeJobSpec) -> None:
        """Run merge I/O work on the merge executor and emit events back to monitor."""
        self._queue_monitor_event(MergeStarted(session_key=merge_job.session_key))
        result = self._merge_session_to_mp4(merge_job)
        self._queue_monitor_event(result)

    def _merge_session_to_mp4(self, merge_job: MergeJobSpec) -> Union[MergeCompleted, MergeFailed]:
        """Merge one session's raw ts outputs into the final mp4 artifact."""
        ts_files = sorted(merge_job.output_dir.glob(f"{merge_job.session_prefix}*.ts"))
        output_path: Optional[Path] = None

        try:
            if not ts_files:
                raise FileNotFoundError(
                    f"No ts files found for session {merge_job.session_key} "
                    f"(prefix={merge_job.session_prefix})"
                )

            output_path = self._reserve_final_output_path(
                creator_name=merge_job.creator_name,
                title=merge_job.title,
                stream_start_time=merge_job.stream_start_time,
            )
            self._run_ffmpeg_merge(ts_files, output_path)

            # The merge succeeded: from here the mp4 is the artifact of record.
            # A locked .ts must neither fail the merge nor reach the except
            # below, which would delete a perfectly good mp4 as a "partial".
            for ts_file in ts_files:
                try:
                    ts_file.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    self.logger.warning(
                        f"Merged, but could not remove {ts_file.name}: "
                        f"{cleanup_error}. The startup scan will list it."
                    )

            return MergeCompleted(
                session_key=merge_job.session_key,
                output_path=output_path,
            )

        except subprocess.TimeoutExpired as exc:
            self._discard_partial_merge_output(output_path)
            timeout_value = int(exc.timeout) if exc.timeout is not None else self.merge_timeout_seconds
            return MergeFailed(
                session_key=merge_job.session_key,
                error_message=f"ffmpeg merge timeout after {timeout_value} seconds",
            )
        except Exception as exc:
            self._discard_partial_merge_output(output_path)
            self.logger.exception(f"Merge failed for session {merge_job.session_key}")
            return MergeFailed(
                session_key=merge_job.session_key,
                error_message=str(exc),
            )

    def _discard_partial_merge_output(self, output_path: Optional[Path]) -> None:
        """
        Drop a half-written mp4 so a failed merge cannot pass for a finished one.

        The raw .ts inputs are only deleted after a successful merge, so the
        recording stays recoverable while the broken artifact goes away.
        """
        if output_path is None:
            return
        try:
            output_path.unlink(missing_ok=True)
        except OSError as exc:
            # A locked file must not turn a merge failure into a thread crash,
            # but a broken mp4 surviving under a final name must not be silent.
            self.logger.warning(
                f"Could not remove partial merge output {output_path.name}: {exc}"
            )

    def _reserve_final_output_path(
        self,
        creator_name: str,
        title: str,
        stream_start_time: datetime,
    ) -> Path:
        """Reserve the next available final mp4 output path."""
        safe_title = sanitize_filename(title, replacement_text="_") or "untitled"
        date_str = stream_start_time.astimezone().strftime("%Y-%m-%d")
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
        merge_ts_files_to_mp4(ts_files, output_path, self._run_merge_subprocess)

    def _run_merge_subprocess(self, command: List[str]) -> None:
        """
        Run one ffmpeg merge as a child this monitor can identify by pid.

        subprocess.run() hides the pid, and without it shutdown cannot tell a
        merge ffmpeg from a recording ffmpeg: both are plain children of this
        process, because yt-dlp spawns its own recording ffmpeg directly.
        Registering the pid is what lets the recording sweep spare this child.

        Raises:
            subprocess.TimeoutExpired: If the merge outlives merge_timeout_seconds
            subprocess.CalledProcessError: If ffmpeg exits non-zero
        """
        # Spawned under the state lock so the recording sweep, which holds the
        # same lock, can never run between this Popen and its registration.
        with self._state_lock:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._merge_process_pids.add(process.pid)

        try:
            with process:
                try:
                    stdout, stderr = process.communicate(
                        timeout=self.merge_timeout_seconds
                    )
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                    raise
                if process.returncode != 0:
                    raise subprocess.CalledProcessError(
                        process.returncode, command, output=stdout, stderr=stderr
                    )
        finally:
            with self._state_lock:
                self._merge_process_pids.discard(process.pid)

    def _shutdown_time_left(self, cap: Optional[float] = None) -> float:
        """Return the seconds left in the aggregate budget, capped for one phase."""
        if self._shutdown_deadline is None:
            return self.SHUTDOWN_BUDGET_SECONDS if cap is None else cap
        left = max(0.0, self._shutdown_deadline - monotonic())
        return left if cap is None else min(cap, left)

    def shutdown(self) -> None:
        """
        Stop monitoring, then hand every stopped recording to the merge step.

        Order is the whole point. Recordings are stopped and joined first so
        their terminal events can queue merge work while the executor is still
        open; only then is the executor closed and its queue flushed. Closing it
        earlier makes submit_merge raise for whatever was still recording, which
        orphans that session's raw .ts files.

        Bounded end to end: every wait draws from one deadline, and a merge that
        outlives it is abandoned rather than allowed to hold up the process.
        """
        with self._state_lock:
            if self._shutdown_requested:
                return
            self._shutdown_requested = True

        self._shutdown_deadline = monotonic() + self.SHUTDOWN_BUDGET_SECONDS

        # 1. No new polls. Draining also lets an in-flight poll finish, so every
        #    recording it started is registered before step 2 snapshots them.
        self._drain_monitor_events(
            self._shutdown_time_left(self.SHUTDOWN_PHASE_TIMEOUT_SECONDS)
        )

        # 2. Stop recordings: kill their ffmpeg, spare any merge ffmpeg, and
        #    wait for the downloader threads to emit their terminal events.
        self._stop_active_recordings()

        # 3. Apply those events. This is where a stopped recording's merge is
        #    submitted, and it must happen while the executor still accepts work.
        self._drain_monitor_events(
            self._shutdown_time_left(self.SHUTDOWN_PHASE_TIMEOUT_SECONDS)
        )

        # 4. Close acceptance and flush the merge queue with whatever budget is
        #    left. Anything arriving after this is late by definition and is
        #    refused in _handle_raw_download_completed.
        self._close_merge_executor()

        # 5. Merge result events, then the control loop and the API client.
        self._drain_monitor_events(
            self._shutdown_time_left(self.SHUTDOWN_PHASE_TIMEOUT_SECONDS)
        )
        self._event_queue.put(_ShutdownRequested())
        self._control_thread.join(
            timeout=self._shutdown_time_left(self.SHUTDOWN_PHASE_TIMEOUT_SECONDS)
        )
        self.api.close()

    def _close_merge_executor(self) -> None:
        """Flush queued merges within the remaining budget, then close the pool."""
        if self.merge_executor.drain(timeout=self._shutdown_time_left()):
            # Nothing is queued behind the barrier, so this cannot block.
            self.merge_executor.shutdown(wait=True)
            return

        # Waiting on wait=True here is what used to make the budget a lie: a
        # wedged ffmpeg merge would hold shutdown open with no deadline at all.
        self.logger.warning(
            f"⚠️ A merge was still running after the {self.SHUTDOWN_BUDGET_SECONDS:.0f}s "
            "shutdown budget; abandoning it instead of blocking exit. Its raw .ts "
            "files stay on disk."
        )
        self.merge_executor.shutdown(wait=False, cancel_futures=True)

    def _stop_active_recordings(self) -> None:
        """Stop recording downloads and wait for their terminal events."""
        with self._state_lock:
            downloaders = list(self._active_downloaders.values())
            for downloader in downloaders:
                downloader.request_stop()

            # Reaped under the state lock: _run_merge_subprocess takes the same
            # lock around its Popen, so no merge child can appear unprotected
            # between the pid snapshot and the sweep. One merge worker means at
            # most one pid to spare.
            reaped = 0
            if any(downloader.is_alive() for downloader in downloaders):
                reaped = terminate_child_processes(
                    exclude_pid=next(iter(self._merge_process_pids), None)
                )

        if reaped:
            self.logger.warning(
                f"Terminated {reaped} recording subprocess(es) left running by yt-dlp"
            )

        self._join_recording_threads(downloaders)

    def _join_recording_threads(self, downloaders: List[StreamDownloader]) -> None:
        """Give stopped downloader threads a bounded window to report their outcome."""
        join_budget = self._shutdown_time_left(self.SHUTDOWN_PHASE_TIMEOUT_SECONDS)
        deadline = monotonic() + join_budget
        unfinished: List[str] = []

        for downloader in downloaders:
            thread = downloader.download_thread
            if thread is None:
                continue
            thread.join(timeout=max(0.0, deadline - monotonic()))
            if thread.is_alive():
                unfinished.append(downloader.creator_name)

        if unfinished:
            # Whatever these report later is past the join budget: the merge
            # executor will refuse it and their raw .ts stay on disk.
            self.logger.warning(
                f"{len(unfinished)} recording(s) did not stop within "
                f"{join_budget:.0f}s: {', '.join(unfinished)}"
            )

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

