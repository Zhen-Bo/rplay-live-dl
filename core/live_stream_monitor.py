"""Live stream monitoring module."""

import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from threading import Event, RLock, Thread
from time import monotonic
from typing import Callable, Dict, List, Optional, Set, Union

from pathvalidate import sanitize_filename

from core.constants import DEFAULT_MERGE_TIMEOUT_SECONDS as _MERGE_TIMEOUT_SECONDS
from core.constants import DEFAULT_MIN_FREE_DISK_GB
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

from .config import DEFAULT_CONFIG_PATH, ConfigError
from .config import read_app_config as read_config
from .download_merge_executor import DownloadMergeExecutor
from .downloader import StreamDownloader
from .health import touch_heartbeat
from .disk_space import DiskSpaceMonitor
from .recording_metadata import recording_metadata
from .logger import bind, clip, setup_logger
from .orphan_recovery import install_merge_output_without_overwrite
from .rplay import RPlayAPI, RPlayAPIError, RPlayAuthError, RPlayConnectionError
from .utils import (
    fit_filename_component_bytes,
    merge_ts_files_to_mp4,
    terminate_child_processes,
)

__all__ = [
    "LiveStreamMonitor",
]


@dataclass(frozen=True)
class _PollRequested:
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
    """Monitors configured creators and auto-downloads their live streams."""

    DEFAULT_MERGE_TIMEOUT_SECONDS = _MERGE_TIMEOUT_SECONDS
    POLL_WAIT_TIMEOUT_SECONDS = 30.0
    # One aggregate budget for the whole shutdown, not a per-step timeout that
    # the next step can extend: every wait below draws from the same deadline,
    # so SIGTERM to returned is bounded end to end. docker-compose.yaml's
    # stop_grace_period is set from this value plus margin.
    SHUTDOWN_BUDGET_SECONDS = 600.0
    # Cap for the fast phases so they cannot eat the merge's share of the budget.
    SHUTDOWN_PHASE_TIMEOUT_SECONDS = 30.0
    # A raw download failure gets one immediate recovery poll.  Repeated
    # failures (especially the no-output path, which has no downloader
    # backoff) are throttled per creator so another creator's poll cannot
    # turn this into a failure-speed loop.
    DOWNLOAD_RETRY_IMMEDIATE_BUDGET = 1
    DOWNLOAD_RETRY_COOLDOWN_BASE_SECONDS = 30.0
    DOWNLOAD_RETRY_COOLDOWN_MAX_SECONDS = 300.0
    TERMINAL_SESSION_STATES = {
        SessionState.BLOCKED,
        SessionState.DONE,
        SessionState.MERGE_FAILED,
    }

    def __init__(
        self,
        api_client: RPlayAPI,
        config_path: str = DEFAULT_CONFIG_PATH,
        merge_timeout_seconds: float = DEFAULT_MERGE_TIMEOUT_SECONDS,
        min_free_disk_gb: float = DEFAULT_MIN_FREE_DISK_GB,
        disk_monitor: Optional[DiskSpaceMonitor] = None,
        merge_reserve_gb: float = 1,
        merge_space_multiplier: float = 2.2,
    ) -> None:
        """min_free_disk_gb of 0 disables the disk check."""
        self.api_client = api_client
        self.config_path = config_path
        self.merge_timeout_seconds = merge_timeout_seconds
        self.min_free_disk_gb = min_free_disk_gb
        self.disk_monitor = disk_monitor or DiskSpaceMonitor()
        self.merge_reserve_gb = merge_reserve_gb
        self.merge_space_multiplier = merge_space_multiplier
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

        self._last_check_success = True
        self._monitored_count = 0
        self._check_count = 0
        self._last_status: Dict[str, int] = {"active_downloads": 0, "monitored_live": 0}
        self._auth_error_notified = False
        # Per cycle only, no TTL: key2 is user-scoped, not creator-scoped.
        self._cycle_stream_key: Optional[str] = None
        self._cycle_key_fetch_auth_failed = False
        self._heartbeat_write_warned = False

        self._creator_states: Dict[str, CreatorStreamState] = {}
        # Retry state is deliberately separate from CreatorStreamState:
        # _update_creator_stream_state() runs for every start attempt and
        # clears the blocked flag for a new handled session.  These values
        # must survive a failed attempt within the same stream instead.
        self._download_retry_failures: Dict[str, int] = {}
        self._download_retry_cooldown_until: Dict[str, float] = {}
        self._download_retry_stream_start: Dict[str, datetime] = {}

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
                            # the caller's API client shutdown is about to close.
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
        # Refusal and enqueue as one step, under the lock shutdown takes to set
        # its flag. Checked unlocked, shutdown slips its whole drain in between
        # and the poll is served after it: a live-list read racing the caller's API client shutdown.
        # Put on an unbounded Queue never blocks, so this holds the lock only
        # for the append.
        with self._state_lock:
            if self._shutdown_requested and isinstance(event, _PollRequested):
                return False
            self._event_queue.put(event)
        return True

    def _request_retry_poll(self, creator_oid: str) -> bool:
        # Signals rather than polls: the public poll waits on the very loop that
        # serves it, and failures are handled on that loop.
        # One region for check, flag and enqueue, so a shutdown lands wholly
        # before this (refused) or wholly after (skipped at dequeue), never in
        # between. RLock, so the nested acquire below is free.
        with self._state_lock:
            if creator_oid not in self._live_creator_oids:
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
        Wait until earlier events are applied. Bounded, unlike Queue.join(),
        so a wedged control loop cannot block shutdown.
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
        self.disk_monitor.check(Path.cwd() / StreamDownloader.ARCHIVE_DIR, self.logger)
        # Unconditional: never carry key2 across poll cycles (incl. A3 retry polls).
        self._cycle_stream_key = None
        self._cycle_key_fetch_auth_failed = False
        try:
            self._update_downloaders()
            live_streams = self.api_client.get_livestream_status()
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
                "Please verify USER_OID and REFRESH_TOKEN in .env."
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
        with self._state_lock:
            self._last_check_success = True

    def _mark_check_failed(self) -> None:
        with self._state_lock:
            self._last_check_success = False

    def _process_live_streams(self, live_streams: List[LiveStream]) -> int:
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
        with self._state_lock:
            self._reset_download_retry_for_new_stream_locked(stream)
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
            active_session_key = self._active_raw_session_by_creator.get(
                stream.creator_oid
            )
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

        if (
            active_session is not None
            and active_session.state == SessionState.RAW_RUNNING
        ):
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
        creator_oid = stream.creator_oid
        with self._state_lock:
            state = self._creator_states.get(creator_oid)
            cooldown_until = self._download_retry_cooldown_until.get(creator_oid, 0.0)

        if state is not None and state.is_current_stream_blocked:
            return False
        if cooldown_until > monotonic():
            return False
        return True

    def _cleanup_offline_creator_states(self, live_creator_oids: Set[str]) -> None:
        with self._state_lock:
            offline_creators = [
                oid for oid in self._creator_states if oid not in live_creator_oids
            ]
        for creator_oid in offline_creators:
            self._clear_creator_stream_state(creator_oid)

    def _start_download(self, stream: LiveStream) -> None:
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
                # Availability beats blocking recordings on a broken statvfs
                self.logger.warning(
                    f"Could not check free disk space for {output_dir} "
                    f"(via {check_path}): {exc}; allowing session"
                )
            else:
                required_bytes = int(self.min_free_disk_gb * (1024**3))
                if free_bytes < required_bytes:
                    free_gb = free_bytes / (1024**3)
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
                stream_key = self.api_client._get_stream_key()
                self._cycle_stream_key = stream_key
                self._cycle_key_fetch_auth_failed = False
                # Success re-arms auth-error logging.
                self._auth_error_notified = False
            stream_url = self.api_client.get_stream_url(
                creator_oid, stream_key=stream_key
            )
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

        # The downloader logs "Recording started" itself.
        active_downloader.download(stream_url, title)

    def _handle_start_download_error(
        self,
        session_key: str,
        creator_name: str,
        exc: Exception,
    ) -> None:
        self._remove_session(session_key)

        if isinstance(exc, RPlayAuthError):
            self._cycle_key_fetch_auth_failed = True
            self._log_auth_error(
                f"Auth error for {creator_name}: {exc}. "
                "Please verify USER_OID and REFRESH_TOKEN in .env."
            )
            return

        if isinstance(exc, RPlayAPIError):
            self.logger.warning(f"Failed to get stream URL for {creator_name}: {exc}")
            return

        self.logger.error(
            f"Error starting download for {creator_name}: {exc}", exc_info=exc
        )

    def _log_status_summary(self, total_live: int, monitored_live: int) -> None:
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
        self.api_client.set_base_url(runtime_config.api_base_url)
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
        if not creator_names:
            return ""
        if len(creator_names) <= 5:
            return f": {', '.join(creator_names)}"
        preview = ", ".join(creator_names[:5])
        return f": {preview}, +{len(creator_names) - 5} more"

    def _resolve_creator_name_locked(self, creator_oid: str) -> str:
        profile = self.monitored_creators.get(creator_oid)
        if profile is not None:
            return profile.creator_name
        for session in self.sessions.values():
            if session.creator_oid == creator_oid:
                return session.creator_name
        return creator_oid

    @property
    def is_healthy(self) -> bool:
        with self._state_lock:
            return self._last_check_success

    def _update_creator_stream_state(self, stream: LiveStream) -> None:
        with self._state_lock:
            if stream.creator_oid not in self._creator_states:
                self._creator_states[stream.creator_oid] = CreatorStreamState()
            self._creator_states[stream.creator_oid].update_stream_start_time(
                stream.stream_start_time,
            )

    def _clear_creator_stream_state(self, creator_oid: str) -> None:
        with self._state_lock:
            creator_name = self._resolve_creator_name_locked(creator_oid)
            creator_state = self._creator_states.pop(creator_oid, None)
            self._clear_download_retry_locked(creator_oid)
            self.latest_stream_oid_by_creator.pop(creator_oid, None)
            released_raw_lock = self._active_raw_session_by_creator.pop(
                creator_oid, None
            )
            pruned_terminal_sessions = self._prune_terminal_sessions_for_creator_locked(
                creator_oid
            )
            blocked = (
                creator_state.is_current_stream_blocked
                if creator_state is not None
                else False
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
            session_key = self._make_session_key(
                stream.creator_oid, recording_started_at
            )
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
                stream_oid=stream.oid,
            )
            self._active_raw_session_by_creator[stream.creator_oid] = session_key
            return self.sessions[session_key]

    def _remove_session(self, session_key: str) -> None:
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
        if recording_started_at.tzinfo is None:
            recording_started_at = recording_started_at.replace(tzinfo=timezone.utc)
        else:
            recording_started_at = recording_started_at.astimezone(timezone.utc)
        return f"{creator_oid}:{int(recording_started_at.timestamp() * 1000)}"

    def _build_session_output_dir(self, creator_name: str) -> Path:
        return Path.cwd() / StreamDownloader.ARCHIVE_DIR / creator_name

    def _make_session_prefix(self, recording_started_at: datetime) -> str:
        local_dt = recording_started_at.astimezone().replace(tzinfo=None)
        return local_dt.strftime("%Y%m%d_%H%M%S_")

    def _on_raw_download_complete(self, event: RawDownloadCompleted) -> None:
        self._queue_monitor_event(event)

    def _on_raw_download_auth_failed(self, event: RawDownloadAuthFailed) -> None:
        self._queue_monitor_event(event)

    def _on_raw_download_failed(self, event: RawDownloadFailed) -> None:
        self._queue_monitor_event(event)

    def _handle_monitor_event(self, event: SessionEvent) -> None:
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
                log_message = f"🎬 Merge started for {session.creator_name}: {session.session_key}"
            elif isinstance(event, MergeCompleted):
                session.state = SessionState.DONE
                log_method = self.logger.info
                log_message = f"✅ Merge completed for {session.creator_name}: {event.output_path}"
            elif isinstance(event, MergeFailed):
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
        with self._state_lock:
            session = self.sessions.get(event.session_key)
            if session is None:
                return

            session.state = SessionState.MERGE_QUEUED
            self._clear_download_retry_locked(session.creator_oid)
            active_session_key = self._active_raw_session_by_creator.get(
                session.creator_oid
            )
            if active_session_key == session.session_key:
                self._active_raw_session_by_creator.pop(session.creator_oid, None)
            merge_job = MergeJobSpec(
                session_key=session.session_key,
                creator_name=session.creator_name,
                title=session.title,
                stream_start_time=session.stream_start_time,
                output_dir=session.output_dir,
                session_prefix=session.session_prefix,
                creator_oid=session.creator_oid,
                stream_oid=session.stream_oid,
                recording_started_at=session.recording_started_at,
            )

            try:
                self.merge_executor.submit_merge(lambda: self._run_merge_job(merge_job))
            except RuntimeError:
                # A recording that outlived shutdown's join budget reports here
                # after the executor closed. Ignoring it idempotently is the
                # contract: re-raising would crash the control loop, and
                # re-queueing would wait on an executor that never reopens.
                # Late completions past the join budget stay orphaned.
                # Startup recovery merges them.
                session.state = SessionState.MERGE_FAILED
                late_creator_name = session.creator_name
                late_output_dir = session.output_dir
            else:
                late_creator_name = None
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
            f"{event.error_message}. Please verify USER_OID and "
            "REFRESH_TOKEN in .env."
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
            stale_stream = False
            if session is not None:
                active_session_key = self._active_raw_session_by_creator.get(
                    session.creator_oid
                )
                if active_session_key == session.session_key:
                    self._active_raw_session_by_creator.pop(session.creator_oid, None)
                # The observed stream marker is updated at poll entry, before
                # the start gate checks cooldown.  CreatorStreamState only
                # changes when a download is actually started, so it can lag
                # during a cooldown and must not be the sole stale check.
                current_stream_start = self._download_retry_stream_start.get(
                    session.creator_oid
                )
                if current_stream_start is None:
                    current_state = self._creator_states.get(session.creator_oid)
                    current_stream_start = (
                        current_state.last_stream_start_time
                        if current_state is not None
                        else None
                    )
                stale_stream = (
                    current_stream_start is None
                    or current_stream_start != session.stream_start_time
                )

        if session is None:
            return

        # A downloader can report after a new stream has already become
        # current (for example, a late thread completion during a fast
        # stream transition).  Its failure belongs to the old session and
        # must not consume the new stream's retry budget or trigger a poll.
        if stale_stream:
            self.logger.debug(
                f"Ignoring late raw download failure for {session.creator_name}: "
                f"session_stream_start={session.stream_start_time.isoformat()}"
            )
            return

        retried_now = self._record_download_failure(session.creator_oid)

        # Waiting for the next scheduled poll costs up to a whole INTERVAL (up
        # to 3600s) of a stream that is still running. Requested only after the
        # session has been cleared above, so the extra poll sees the creator
        # free to start again rather than skipping it as already recording.
        if retried_now:
            retried_now = self._request_retry_poll(session.creator_oid)
        next_attempt = (
            "retrying immediately" if retried_now else "will retry on next poll"
        )
        self.logger.warning(
            f"⚠️ Raw download failed for {session.creator_name}; {next_attempt}: "
            f"{event.error_message}"
        )

    def _reset_download_retry_for_new_stream_locked(self, stream: LiveStream) -> None:
        creator_oid = stream.creator_oid
        previous_start = self._download_retry_stream_start.get(creator_oid)
        if previous_start is None:
            self._download_retry_stream_start[creator_oid] = stream.stream_start_time
            return
        if previous_start != stream.stream_start_time:
            # A cooldown already in progress remains in force across a stream
            # transition; only the old stream's failure count is discarded.
            # This prevents a late old-session event from adding to the new
            # stream while preserving the back-pressure that is already due.
            self._download_retry_failures.pop(creator_oid, None)
            self._download_retry_stream_start[creator_oid] = stream.stream_start_time

    def _clear_download_retry_locked(self, creator_oid: str) -> None:
        """Clear a creator's failed-download budget (state lock required)."""
        self._download_retry_failures.pop(creator_oid, None)
        self._download_retry_cooldown_until.pop(creator_oid, None)
        self._download_retry_stream_start.pop(creator_oid, None)

    def _record_download_failure(self, creator_oid: str) -> bool:
        """Record a failure and return whether an immediate poll is allowed."""
        now = monotonic()
        with self._state_lock:
            failures = self._download_retry_failures.get(creator_oid, 0) + 1
            self._download_retry_failures[creator_oid] = failures
            if failures <= self.DOWNLOAD_RETRY_IMMEDIATE_BUDGET:
                self._download_retry_cooldown_until.pop(creator_oid, None)
                return True

            exponent = failures - self.DOWNLOAD_RETRY_IMMEDIATE_BUDGET - 1
            cooldown = min(
                self.DOWNLOAD_RETRY_COOLDOWN_MAX_SECONDS,
                self.DOWNLOAD_RETRY_COOLDOWN_BASE_SECONDS * (2**exponent),
            )
            self._download_retry_cooldown_until[creator_oid] = now + cooldown
            return False

    def _handle_raw_download_blocked(self, event: RawDownloadBlocked) -> None:
        """Apply blocked-session state when downloader reports access failure.

        Reaching here via 404 shortly after stream start is expected for no-access (paid) streams.
        """
        with self._state_lock:
            session = self.sessions.get(event.session_key)
            if session is None:
                return

            session.state = SessionState.BLOCKED
            active_session_key = self._active_raw_session_by_creator.get(
                session.creator_oid
            )
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
        self._queue_monitor_event(MergeStarted(session_key=merge_job.session_key))
        result = self._merge_session_to_mp4(merge_job)
        self._queue_monitor_event(result)

    def _merge_session_to_mp4(
        self, merge_job: MergeJobSpec
    ) -> Union[MergeCompleted, MergeFailed]:
        ts_files = sorted(merge_job.output_dir.glob(f"{merge_job.session_prefix}*.ts"))
        output_path: Optional[Path] = None
        temp_path: Optional[Path] = None

        try:
            if not ts_files:
                raise FileNotFoundError(
                    f"No ts files found for session {merge_job.session_key} "
                    f"(prefix={merge_job.session_prefix})"
                )

            # Reserve the *base* name only for deriving a stable output stem.
            # FFmpeg must never write directly to this collision-significant
            # path: a creator can finish another session while this merge is
            # running, and ``-y`` would otherwise clobber that recording.
            base_output_path = self._build_final_output_base_path(
                creator_name=merge_job.creator_name,
                title=merge_job.title,
                stream_start_time=merge_job.stream_start_time,
            )
            temp_path = self._build_merge_temp_path(base_output_path)
            self._clear_stale_merge_temp(temp_path)
            self._run_ffmpeg_merge(
                ts_files, temp_path, metadata=recording_metadata(merge_job)
            )

            # A successful ffmpeg exit is not enough to prove an artifact was
            # produced. Keep the raw inputs when a stub, muxer, or interrupted
            # process leaves no bytes to install.
            self._validate_merge_output(temp_path, "merge produced no output")

            # Install only after ffmpeg is done. The shared recovery helper
            # uses an atomic hardlink (or O_EXCL copy fallback) and retries a
            # suffix when another writer claims the name during the merge.
            output_path = install_merge_output_without_overwrite(
                self.logger, temp_path, base_output_path
            )

            # The installer may use a copy fallback. Keep the raw inputs until the
            # final path is present and non-empty.
            self._validate_merge_output(
                output_path, "installed merge output is invalid"
            )

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
            self._discard_partial_merge_output(temp_path)
            if output_path is not None and output_path != temp_path:
                self._discard_partial_merge_output(output_path)
            timeout_value = (
                int(exc.timeout)
                if exc.timeout is not None
                else self.merge_timeout_seconds
            )
            return MergeFailed(
                session_key=merge_job.session_key,
                error_message=f"ffmpeg merge timeout after {timeout_value} seconds",
            )
        except Exception as exc:
            self._discard_partial_merge_output(temp_path)
            if output_path is not None and output_path != temp_path:
                self._discard_partial_merge_output(output_path)
            self.logger.exception(f"Merge failed for session {merge_job.session_key}")
            return MergeFailed(
                session_key=merge_job.session_key,
                error_message=str(exc),
            )

    @staticmethod
    def _validate_merge_output(output_path: Path, error_prefix: str) -> None:
        try:
            is_file = output_path.is_file()
            size = output_path.stat().st_size if is_file else 0
        except OSError as exc:
            raise RuntimeError(f"{error_prefix} at {output_path.name}: {exc}") from exc

        if not is_file or size == 0:
            raise RuntimeError(f"{error_prefix} at {output_path.name}")

    def _discard_partial_merge_output(self, output_path: Optional[Path]) -> None:
        """Drop a half-written mp4 so a failed merge cannot pass for a finished one."""
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

    def _build_final_output_base_path(
        self,
        creator_name: str,
        title: str,
        stream_start_time: datetime,
    ) -> Path:
        safe_title = sanitize_filename(title, replacement_text="_") or "untitled"
        date_str = stream_start_time.astimezone().strftime("%Y-%m-%d")
        base_dir = Path.cwd() / StreamDownloader.ARCHIVE_DIR / creator_name
        base_dir.mkdir(parents=True, exist_ok=True)

        return fit_filename_component_bytes(
            base_dir / f"#{creator_name} {date_str} {safe_title}.mp4"
        )

    @staticmethod
    def _build_merge_temp_path(base_output_path: Path) -> Path:
        # Reserve the marker before truncating the base stem. Otherwise a
        # title near the filesystem limit can cut off ``.merging`` entirely,
        # making stale-temp inspection and cleanup ambiguous.
        temp_seed = base_output_path.with_name(f".{base_output_path.stem}.mp4")
        return fit_filename_component_bytes(temp_seed, appended_suffix=".merging")

    @staticmethod
    def _clear_stale_merge_temp(temp_path: Path) -> None:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"could not clear stale merge output {temp_path.name}: {exc}"
            ) from exc

    def _run_ffmpeg_merge(
        self,
        ts_files: List[Path],
        output_path: Path,
        *,
        metadata: Optional[Dict[str, str]] = None,
    ) -> None:
        merge_ts_files_to_mp4(
            ts_files,
            output_path,
            self._run_merge_subprocess,
            reserve_gb=self.merge_reserve_gb,
            space_multiplier=self.merge_space_multiplier,
            metadata=metadata,
        )

    def _run_merge_subprocess(self, command: List[str]) -> None:
        """
        Run one ffmpeg merge as a child this monitor can identify by pid.

        subprocess.run() hides the pid. Shutdown needs it to tell a merge ffmpeg
        from a recording ffmpeg, since yt-dlp spawns the recording ffmpeg as a
        plain child of this process.

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
        """
        with self._state_lock:
            if self._shutdown_requested:
                return
            self._shutdown_requested = True

        self._shutdown_deadline = monotonic() + self.SHUTDOWN_BUDGET_SECONDS

        # No new polls. Draining also lets an in-flight poll finish, so every
        # recording it started is registered before the snapshot below.
        self._drain_monitor_events(
            self._shutdown_time_left(self.SHUTDOWN_PHASE_TIMEOUT_SECONDS)
        )

        self._stop_active_recordings()

        # Submits each stopped recording's merge, so it must run while the
        # executor still accepts work.
        self._drain_monitor_events(
            self._shutdown_time_left(self.SHUTDOWN_PHASE_TIMEOUT_SECONDS)
        )

        # Close acceptance and flush the merge queue with whatever budget is
        # left. Anything arriving after this is late by definition and is
        # refused in _handle_raw_download_completed.
        self._close_merge_executor()

        self._drain_monitor_events(
            self._shutdown_time_left(self.SHUTDOWN_PHASE_TIMEOUT_SECONDS)
        )
        self._event_queue.put(_ShutdownRequested())
        self._control_thread.join(
            timeout=self._shutdown_time_left(self.SHUTDOWN_PHASE_TIMEOUT_SECONDS)
        )

    def _close_merge_executor(self) -> None:
        if self.merge_executor.drain(timeout=self._shutdown_time_left()):
            # Nothing is queued behind the barrier, so this cannot block.
            self.merge_executor.shutdown(wait=True)
            return

        # wait=True here would hold shutdown open with no deadline behind a
        # wedged ffmpeg merge.
        self.logger.warning(
            f"⚠️ A merge was still running after the {self.SHUTDOWN_BUDGET_SECONDS:.0f}s "
            "shutdown budget; abandoning it instead of blocking exit. Its raw .ts "
            "files stay on disk."
        )
        self.merge_executor.shutdown(wait=False, cancel_futures=True)

    def _stop_active_recordings(self) -> None:
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

    def _make_session_download_error_callback(
        self, session_key: str
    ) -> Callable[[str], None]:
        def _on_error(error_message: str) -> None:
            self._queue_monitor_event(
                RawDownloadBlocked(
                    session_key=session_key,
                    error_message=error_message,
                )
            )

        return _on_error
