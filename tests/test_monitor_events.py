"""Tests for event-driven monitor behavior."""

import inspect

from threading import Event as ThreadEvent, Thread
from time import monotonic

from models.download import MergeCompleted

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from core.downloader import StreamDownloader
from core.live_stream_monitor import LiveStreamMonitor, _PollRequested
from models.config import AppConfig, CreatorProfile
from models.env import EnvConfig
from core.rplay import RPlayAPI
from models.download import (
    DownloadSession,
    RawDownloadCompleted,
    RawDownloadFailed,
    SessionState,
)
from models.rplay import StreamState


def test_raw_completion_event_immediately_submits_merge(tmp_path):
    """Test raw completion queues merge work without waiting for another poll."""
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)
    session_key = "creator1:2026-03-06T12:00:00"
    monitor.sessions[session_key] = DownloadSession(
        session_key=session_key,
        creator_oid="creator1",
        creator_name="Creator1",
        title="Test Stream",
        stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
        state=SessionState.RAW_RUNNING,
        output_dir=tmp_path,
        session_prefix="20260306_120000_",
    )

    with patch.object(monitor.merge_executor, "submit_merge") as mock_submit:
        monitor._on_raw_download_complete(
            RawDownloadCompleted(session_key=session_key, output_dir=tmp_path)
        )
        monitor._event_queue.join()

    assert monitor.sessions[session_key].state == SessionState.MERGE_QUEUED
    mock_submit.assert_called_once()
    monitor.shutdown()


def test_raw_failure_event_allows_same_session_retry(tmp_path):
    """Test a failed raw download clears the stuck session so the next poll can retry."""
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)
    session_key = "creator1:2026-03-06T12:00:00"
    monitor.monitored_creators["creator1"] = CreatorProfile(
        creator_name="Creator1",
        creator_oid="creator1",
    )
    monitor.sessions[session_key] = DownloadSession(
        session_key=session_key,
        creator_oid="creator1",
        creator_name="Creator1",
        title="Test Stream",
        stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
        state=SessionState.RAW_RUNNING,
        output_dir=tmp_path,
        session_prefix="20260306_120000_",
    )
    mock_stream = MagicMock()
    mock_stream.creator_oid = "creator1"
    mock_stream.stream_state = "live"
    mock_stream.stream_start_time = datetime(2026, 3, 6, 12, 0, 0)
    mock_stream.title = "Test Stream"

    with patch.object(monitor, "_start_download") as mock_start_download:
        monitor._on_raw_download_failed(
            RawDownloadFailed(
                session_key=session_key,
                error_message="Some other error",
            )
        )
        monitor._event_queue.join()
        monitor._process_live_stream(mock_stream)

    assert session_key not in monitor.sessions
    mock_start_download.assert_called_once_with(mock_stream)
    monitor.shutdown()


def test_get_active_downloads_uses_session_state_only(tmp_path):
    """Test active downloads are derived from session state, not downloader liveness fallback."""
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)
    monitor.sessions["creator1:2026-03-06T12:00:00"] = DownloadSession(
        session_key="creator1:2026-03-06T12:00:00",
        creator_oid="creator1",
        creator_name="Creator1",
        title="Test Stream",
        stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
        state=SessionState.RAW_RUNNING,
        output_dir=tmp_path,
        session_prefix="20260306_120000_",
    )

    assert monitor.get_active_downloads() == ["Creator1"]
    monitor.shutdown()


def test_no_session_means_no_active_downloads_even_if_template_downloader_alive():
    """Test session state is the sole source for active download reporting."""
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)

    assert monitor.get_active_downloads() == []
    monitor.shutdown()


def test_session_download_error_callback_accepts_only_session_key():
    """Test the session-specific error callback factory takes only the session key."""
    parameters = inspect.signature(
        LiveStreamMonitor._make_session_download_error_callback
    ).parameters

    assert list(parameters) == ["self", "session_key"]


def test_unhandled_session_event_logs_error(tmp_path):
    """Test unknown session events are logged instead of being silently ignored."""
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)
    session_key = "creator1:2026-03-06T12:00:00"
    monitor.sessions[session_key] = DownloadSession(
        session_key=session_key,
        creator_oid="creator1",
        creator_name="Creator1",
        title="Test Stream",
        stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
        state=SessionState.MERGE_QUEUED,
        output_dir=tmp_path,
        session_prefix="20260306_120000_",
    )

    class UnknownSessionEvent:
        def __init__(self, session_key: str) -> None:
            self.session_key = session_key

    with patch.object(monitor.logger, "error") as mock_error:
        monitor._handle_monitor_event(UnknownSessionEvent(session_key))

    mock_error.assert_called_once()
    assert "Unhandled session event type" in mock_error.call_args.args[0]
    monitor.shutdown()


def test_check_returns_immediately_when_poll_not_queued():
    """Test poll requests rejected during shutdown do not block on the local done event."""
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)

    with (
        patch.object(monitor, "_queue_monitor_event", return_value=False),
        patch("core.live_stream_monitor.Event.wait", autospec=True, side_effect=AssertionError("wait should not be called")),
    ):
        monitor.check_live_streams_and_start_download()

    monitor.shutdown()


def test_shutdown_drains_pending_raw_completion_before_executor_shutdown(tmp_path):
    """Test shutdown lets a queued raw completion submit merge work before the merge executor closes."""
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)
    session_key = "creator1:2026-03-06T12:00:00"
    monitor.sessions[session_key] = DownloadSession(
        session_key=session_key,
        creator_oid="creator1",
        creator_name="Creator1",
        title="Test Stream",
        stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
        state=SessionState.RAW_RUNNING,
        output_dir=tmp_path,
        session_prefix="20260306_120000_",
    )

    handle_started = ThreadEvent()
    release_handle = ThreadEvent()
    original_handle_monitor_event = monitor._handle_monitor_event

    def blocking_handle(event):
        if isinstance(event, RawDownloadCompleted):
            handle_started.set()
            release_handle.wait(timeout=1)
        return original_handle_monitor_event(event)

    monitor._handle_monitor_event = blocking_handle

    merge_executor_closed = False

    def fake_shutdown(wait=False):
        nonlocal merge_executor_closed
        merge_executor_closed = True

    def fake_submit(task):
        if merge_executor_closed:
            raise RuntimeError("merge executor is shut down")
        task()
        return MagicMock()

    with (
        patch.object(monitor.merge_executor, "shutdown", side_effect=fake_shutdown),
        patch.object(monitor.merge_executor, "submit_merge", side_effect=fake_submit),
        patch.object(
            monitor,
            "_merge_session_to_mp4",
            return_value=MergeCompleted(
                session_key=session_key,
                output_path=tmp_path / "final.mp4",
            ),
        ),
    ):
        monitor._on_raw_download_complete(
            RawDownloadCompleted(session_key=session_key, output_dir=tmp_path)
        )
        assert handle_started.wait(timeout=1)

        shutdown_thread = Thread(target=monitor.shutdown)
        shutdown_thread.start()
        release_handle.set()
        shutdown_thread.join(timeout=5)
        # A shutdown still running here would make the state assertion below
        # pass for the wrong reason.
        assert not shutdown_thread.is_alive()
        monitor._event_queue.join()

    assert monitor.sessions[session_key].state == SessionState.DONE


class _FakeRecording:
    """
    Stand-in for a StreamDownloader whose thread is still recording.

    Mirrors the parts of the real downloader that shutdown drives: it is alive
    until asked to stop, and only then reports its terminal event.
    """

    def __init__(self, monitor, session_key, output_dir):
        self.creator_name = "Creator1"
        self._monitor = monitor
        self._session_key = session_key
        self._output_dir = output_dir
        self._stop_requested = ThreadEvent()
        self.stop_calls = 0
        self.download_thread = Thread(target=self._run, daemon=True)
        self.download_thread.start()

    def request_stop(self):
        """Record the stop request and let the recording thread finish."""
        self.stop_calls += 1
        self._stop_requested.set()

    def is_alive(self):
        """Report liveness the way StreamDownloader does."""
        return self.download_thread.is_alive()

    def _run(self):
        self._stop_requested.wait(timeout=5)
        self._monitor._on_raw_download_complete(
            RawDownloadCompleted(
                session_key=self._session_key,
                output_dir=self._output_dir,
            )
        )


def _make_running_session(monitor, tmp_path, session_key="creator1:2026-03-06T12:00:00"):
    """Register a RAW_RUNNING session for shutdown tests."""
    monitor.sessions[session_key] = DownloadSession(
        session_key=session_key,
        creator_oid="creator1",
        creator_name="Creator1",
        title="Test Stream",
        stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
        state=SessionState.RAW_RUNNING,
        output_dir=tmp_path,
        session_prefix="20260306_120000_",
    )
    return session_key


def test_shutdown_merges_recording_that_was_active_at_shutdown(tmp_path):
    """Test a recording stopped by shutdown still gets its raw output merged."""
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)
    session_key = _make_running_session(monitor, tmp_path)
    recording = _FakeRecording(monitor, session_key, tmp_path)
    monitor._active_downloaders[session_key] = recording

    with (
        patch(
            "core.live_stream_monitor.terminate_child_processes", return_value=1
        ) as mock_terminate,
        patch.object(
            monitor,
            "_merge_session_to_mp4",
            return_value=MergeCompleted(
                session_key=session_key,
                output_path=tmp_path / "final.mp4",
            ),
        ),
    ):
        monitor.shutdown()

    # The recording had to be stopped, and its merge had to reach the executor
    # while it was still open: MERGE_QUEUED or MERGE_FAILED here would mean the
    # session .ts was orphaned.
    assert recording.stop_calls == 1
    mock_terminate.assert_called_once()
    assert monitor.sessions[session_key].state == SessionState.DONE


def test_shutdown_spares_a_running_merge_from_the_recording_sweep(tmp_path):
    """Test the recording sweep excludes pids of merges that are still running."""
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)
    session_key = _make_running_session(monitor, tmp_path)
    monitor._active_downloaders[session_key] = _FakeRecording(
        monitor, session_key, tmp_path
    )
    merge_pid = 424242
    monitor._merge_process_pids.add(merge_pid)
    captured = {}

    def fake_terminate(timeout_seconds=10.0, exclude_pid=None):
        captured["exclude_pid"] = exclude_pid
        return 0

    with (
        patch(
            "core.live_stream_monitor.terminate_child_processes",
            side_effect=fake_terminate,
        ),
        patch.object(
            monitor,
            "_merge_session_to_mp4",
            return_value=MergeCompleted(
                session_key=session_key,
                output_path=tmp_path / "final.mp4",
            ),
        ),
    ):
        monitor.shutdown()

    assert captured["exclude_pid"] == merge_pid


def test_shutdown_is_idempotent(tmp_path):
    """Test a second shutdown neither raises nor repeats the teardown."""
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)

    with patch("core.live_stream_monitor.terminate_child_processes"):
        monitor.shutdown()
        monitor.shutdown()

    mock_api.close.assert_called_once()


def test_shutdown_rejects_new_download_sessions(tmp_path):
    """Test no session is started once shutdown has begun."""
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)
    monitor.monitored_creators["creator1"] = CreatorProfile(
        creator_name="Creator1",
        creator_oid="creator1",
    )
    stream = MagicMock()
    stream.creator_oid = "creator1"
    stream.stream_start_time = datetime(2026, 3, 6, 12, 0, 0)
    stream.title = "Test Stream"

    with patch("core.live_stream_monitor.terminate_child_processes"):
        monitor.shutdown()
        monitor._start_download(stream)

    assert monitor.sessions == {}
    mock_api.get_stream_url.assert_not_called()


def test_late_terminal_event_after_drain_is_ignored_idempotently(tmp_path):
    """Test a recording reporting after the join budget warns instead of raising."""
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)

    with patch("core.live_stream_monitor.terminate_child_processes"):
        monitor.shutdown()

    session_key = _make_running_session(monitor, tmp_path)

    # What a recording that outlived the join budget actually does: report a
    # terminal event into a monitor whose merge executor is already closed.
    # The executor's RuntimeError must be absorbed here, not re-raised and not
    # re-queued for an executor that never reopens.
    with patch.object(monitor.logger, "warning") as mock_warning:
        monitor._handle_raw_download_completed(
            RawDownloadCompleted(session_key=session_key, output_dir=tmp_path)
        )
        # Idempotent: a repeat of the same late event changes nothing.
        monitor._handle_raw_download_completed(
            RawDownloadCompleted(session_key=session_key, output_dir=tmp_path)
        )

    assert monitor.sessions[session_key].state == SessionState.MERGE_FAILED
    # The raw .ts are only recoverable if the log says where they are.
    assert all(str(tmp_path) in call.args[0] for call in mock_warning.call_args_list)
    assert mock_warning.call_count == 2


def test_in_flight_poll_does_not_start_recording_after_shutdown(tmp_path, monkeypatch):
    """Test a poll blocked on the stream URL during shutdown starts nothing."""
    monkeypatch.chdir(tmp_path)
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)
    monitor.monitored_creators["creator1"] = CreatorProfile(
        creator_name="Creator1",
        creator_oid="creator1",
    )
    stream = MagicMock()
    stream.creator_oid = "creator1"
    stream.oid = "stream1"
    stream.stream_start_time = datetime(2026, 3, 6, 12, 0, 0)
    stream.title = "Test Stream"

    at_stream_url = ThreadEvent()
    resume_poll = ThreadEvent()

    def blocking_stream_url(creator_oid, stream_key=None):
        at_stream_url.set()
        assert resume_poll.wait(timeout=5)
        return "http://example.com/stream.m3u8"

    mock_api._get_stream_key.return_value = "cycle-key"
    mock_api.get_stream_url.side_effect = blocking_stream_url

    with patch.object(StreamDownloader, "download") as mock_download:
        poll = Thread(target=monitor._start_download, args=(stream,), daemon=True)
        poll.start()
        assert at_stream_url.wait(timeout=5)

        # Shutdown takes its recording snapshot while the poll is still inside
        # get_stream_url, so the recheck before registration is the only thing
        # standing between this and a recording nobody stops.
        with patch("core.live_stream_monitor.terminate_child_processes"):
            monitor.shutdown()

        resume_poll.set()
        poll.join(timeout=5)

    assert not poll.is_alive()
    mock_download.assert_not_called()
    assert monitor._active_downloaders == {}
    assert monitor.sessions == {}
    assert monitor._active_raw_session_by_creator == {}


class _StuckMergeExecutor:
    """Merge executor whose queued merge never finishes, only times out."""

    def __init__(self) -> None:
        self.shutdown_calls = []
        self._never = ThreadEvent()

    def submit_merge(self, task):
        raise AssertionError("no merge should be submitted in this test")

    def drain(self, timeout=None):
        # A merge stuck inside ffmpeg: the barrier can only run out of budget.
        self._never.wait(timeout=timeout)
        return False

    def shutdown(self, wait=False, cancel_futures=False):
        self.shutdown_calls.append((wait, cancel_futures))


def test_shutdown_returns_within_budget_when_merge_never_finishes(tmp_path):
    """Test the aggregate deadline bounds shutdown even with a wedged merge."""
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)
    monitor.SHUTDOWN_BUDGET_SECONDS = 0.5
    stuck_executor = _StuckMergeExecutor()
    monitor.merge_executor = stuck_executor

    finished = ThreadEvent()

    def run_shutdown():
        monitor.shutdown()
        finished.set()

    started_at = monotonic()
    shutdown_thread = Thread(target=run_shutdown, daemon=True)
    with patch("core.live_stream_monitor.terminate_child_processes"):
        shutdown_thread.start()
        # Generous ceiling: the point is that shutdown returns at all, and the
        # elapsed assertion below is what pins it to the budget.
        assert finished.wait(timeout=10)
        shutdown_thread.join(timeout=5)

    assert not shutdown_thread.is_alive()
    assert monotonic() - started_at < 5

    # wait=True here would hand the wedged merge veto power over process exit.
    assert stuck_executor.shutdown_calls == [(False, True)]
    mock_api.close.assert_called_once()


def _live_stream(creator_oid, stream_oid="stream-1"):
    """Build a live-stream stand-in the monitor will accept as recordable."""
    stream = MagicMock()
    stream.oid = stream_oid
    stream.creator_oid = creator_oid
    stream.stream_state = StreamState.LIVE
    stream.stream_start_time = datetime(2026, 3, 6, 12, 0, 0)
    stream.title = f"Stream {creator_oid}"
    return stream


@pytest.fixture
def repoll_env(tmp_path, monkeypatch):
    """Serve a fixed creator config and keep the real downloader off the network."""
    monkeypatch.chdir(tmp_path)
    with (
        patch("core.live_stream_monitor.read_config") as mock_read_config,
        patch.object(StreamDownloader, "download"),
    ):
        mock_read_config.return_value = AppConfig(
            api_base_url="https://api.rplay.live",
            creators=[
                CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
                CreatorProfile(creator_name="Creator2", creator_oid="creator2"),
            ],
        )
        yield


def test_failure_while_creator_still_live_earns_one_immediate_extra_poll(repoll_env):
    """Test a still-live failure buys exactly one extra poll, promptly, without blocking."""
    # The largest interval an operator may configure. Before this lane a
    # mid-stream failure cost that whole window; the re-poll must not scale
    # with it, so the interval only ever appears here as the bound to beat.
    largest_interval = EnvConfig(
        auth_token="token", user_oid="oid", interval=3600
    ).interval

    mock_api = MagicMock(spec=RPlayAPI)
    live_list_reads = []
    polled_again = ThreadEvent()

    def read_live_list():
        live_list_reads.append(monotonic())
        if len(live_list_reads) > 1:
            polled_again.set()
        return [_live_stream("creator1")]

    mock_api.get_livestream_status.side_effect = read_live_list
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)

    monitor.check_live_streams_and_start_download()
    session_key = next(iter(monitor.sessions))

    blocking_calls = []

    # Recorded rather than raised: the control loop logs exceptions, so raising
    # here would be swallowed and the test would pass for the wrong reason.
    with patch.object(
        monitor,
        "check_live_streams_and_start_download",
        side_effect=lambda: blocking_calls.append("called"),
    ):
        failed_at = monotonic()
        monitor._on_raw_download_failed(
            RawDownloadFailed(session_key=session_key, error_message="ffmpeg exited 1")
        )
        # Bounded wait, not a bare one: a mechanism that deadlocked hangs here
        # instead of failing the latency assertion below.
        assert polled_again.wait(timeout=5)
        # Quiescence barrier: the queue only empties once the failure and
        # everything it queued have been applied, so a second extra poll counts.
        monitor._event_queue.join()

    # V5: absolute bound, so it cannot pass by quietly tracking INTERVAL, and it
    # sits far under even the shortest interval the app accepts.
    assert live_list_reads[1] - failed_at < 1.0 < largest_interval
    # One re-poll re-reads the whole live list, so one failure earns one poll.
    assert len(live_list_reads) == 2
    # The public poll waits on the very loop that serves it: calling it from a
    # failure handler would stall the loop for POLL_WAIT_TIMEOUT_SECONDS.
    assert blocking_calls == []
    monitor.shutdown()


def test_failure_after_creator_went_offline_schedules_no_extra_poll(repoll_env):
    """Test a failure for a creator no longer in the live list re-polls nothing."""
    mock_api = MagicMock(spec=RPlayAPI)
    mock_api.get_livestream_status.side_effect = [[_live_stream("creator1")], []]
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)

    monitor.check_live_streams_and_start_download()
    session_key = next(iter(monitor.sessions))
    # The stream ended: the second poll sees an empty live list.
    monitor.check_live_streams_and_start_download()

    monitor._on_raw_download_failed(
        RawDownloadFailed(session_key=session_key, error_message="ffmpeg exited 1")
    )
    monitor._event_queue.join()

    # Still the two polls this test asked for. A third would also exhaust the
    # side_effect list, so a stray re-poll cannot hide here.
    assert mock_api.get_livestream_status.call_count == 2
    monitor.shutdown()


def test_concurrent_failures_merge_into_one_extra_poll(repoll_env):
    """Test failures landing together share a single extra poll instead of one each."""
    mock_api = MagicMock(spec=RPlayAPI)
    mock_api.get_livestream_status.return_value = [
        _live_stream("creator1", "stream-1"),
        _live_stream("creator2", "stream-2"),
    ]
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)

    monitor.check_live_streams_and_start_download()
    session_key_by_creator = {
        session.creator_oid: key for key, session in monitor.sessions.items()
    }
    assert len(session_key_by_creator) == 2

    reached_first_failure = ThreadEvent()
    release_first_failure = ThreadEvent()
    original_handle_monitor_event = monitor._handle_monitor_event

    def gated_handle(event):
        if isinstance(event, RawDownloadFailed) and not reached_first_failure.is_set():
            reached_first_failure.set()
            # Hold the control loop so the second failure is already queued
            # before the first one gets to request its re-poll.
            assert release_first_failure.wait(timeout=5)
        return original_handle_monitor_event(event)

    monitor._handle_monitor_event = gated_handle

    monitor._on_raw_download_failed(
        RawDownloadFailed(
            session_key=session_key_by_creator["creator1"],
            error_message="ffmpeg exited 1",
        )
    )
    assert reached_first_failure.wait(timeout=5)
    monitor._on_raw_download_failed(
        RawDownloadFailed(
            session_key=session_key_by_creator["creator2"],
            error_message="ffmpeg exited 1",
        )
    )
    release_first_failure.set()
    monitor._event_queue.join()

    # One re-poll re-reads the whole live list, so two failures must not buy
    # two polls on top of the initial one.
    assert mock_api.get_livestream_status.call_count == 2
    monitor.shutdown()


def _signal_shutdown_begun(monitor):
    """Wrap _drain_monitor_events so tests can see shutdown has set its flag."""
    shutdown_begun = ThreadEvent()
    original_drain = monitor._drain_monitor_events

    def signalling_drain(timeout):
        # Shutdown's first act after setting _shutdown_requested is this drain.
        shutdown_begun.set()
        return original_drain(timeout)

    monitor._drain_monitor_events = signalling_drain
    return shutdown_begun


def _gate_poll_enqueue(monitor, shutdown_begun):
    """
    Hold the next poll enqueue open and invite a shutdown into that gap.

    Returns the event signalling the gate was reached and the _shutdown_requested
    readings taken at the moment of each poll's put.
    """
    reached = ThreadEvent()
    shutdown_flags = []
    real_put = monitor._event_queue.put

    def gated_put(event, *args, **kwargs):
        if isinstance(event, _PollRequested):
            reached.set()
            # Serialized under one lock this wait can only time out: shutdown
            # cannot even set its flag until the put returns. Split across two
            # lock regions it returns early, with the poll about to be queued
            # behind the drain that was supposed to be the last one.
            shutdown_begun.wait(timeout=1.0)
            shutdown_flags.append(monitor._shutdown_requested)
        return real_put(event, *args, **kwargs)

    monitor._event_queue.put = gated_put
    return reached, shutdown_flags


def test_retry_enqueue_is_atomic_against_a_shutdown_landing_mid_request(repoll_env):
    """Test a shutdown cannot slip between the retry request's refusal check and its enqueue."""
    mock_api = MagicMock(spec=RPlayAPI)
    shutdown_flag_at_read = []

    def read_live_list():
        shutdown_flag_at_read.append(monitor._shutdown_requested)
        return [_live_stream("creator1")]

    mock_api.get_livestream_status.side_effect = read_live_list
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)
    shutdown_begun = _signal_shutdown_begun(monitor)

    monitor.check_live_streams_and_start_download()
    session_key = next(iter(monitor.sessions))

    reached_enqueue, shutdown_flag_at_enqueue = _gate_poll_enqueue(monitor, shutdown_begun)
    monitor._on_raw_download_failed(
        RawDownloadFailed(session_key=session_key, error_message="ffmpeg exited 1")
    )
    assert reached_enqueue.wait(timeout=5)

    shutdown_thread = Thread(target=monitor.shutdown, daemon=True)
    shutdown_thread.start()
    shutdown_thread.join(timeout=30)

    assert not shutdown_thread.is_alive()
    # The check that admitted this poll must still hold when it is queued;
    # otherwise the poll lands behind a drain that was meant to be the last one.
    assert shutdown_flag_at_enqueue == [False]
    # And no retry poll may read the live list once shutdown has begun: that
    # prolongs shutdown and can still be in flight when api.close() lands.
    assert True not in shutdown_flag_at_read


def test_scheduler_poll_enqueue_is_atomic_against_a_shutdown_landing_mid_request(repoll_env):
    """Test the refusal window is closed for every poll requester, not just the retry."""
    mock_api = MagicMock(spec=RPlayAPI)
    mock_api.get_livestream_status.return_value = [_live_stream("creator1")]
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)
    shutdown_begun = _signal_shutdown_begun(monitor)

    monitor.check_live_streams_and_start_download()

    reached_enqueue, shutdown_flag_at_enqueue = _gate_poll_enqueue(monitor, shutdown_begun)
    # The scheduler asking for its next poll at the worst possible moment. Both
    # requesters share one enqueue, which is where the window has to be closed:
    # guarding only the retry path leaves this one racing api.close().
    scheduler = Thread(target=monitor.check_live_streams_and_start_download, daemon=True)
    scheduler.start()
    assert reached_enqueue.wait(timeout=5)

    shutdown_thread = Thread(target=monitor.shutdown, daemon=True)
    shutdown_thread.start()
    shutdown_thread.join(timeout=30)
    scheduler.join(timeout=5)

    assert not shutdown_thread.is_alive()
    assert not scheduler.is_alive()
    assert shutdown_flag_at_enqueue == [False]


def test_retry_poll_queued_before_shutdown_is_skipped_when_dequeued(repoll_env):
    """Test the control loop drops an already-queued retry poll once shutdown has begun."""
    mock_api = MagicMock(spec=RPlayAPI)
    mock_api.get_livestream_status.return_value = [_live_stream("creator1")]
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)
    shutdown_begun = _signal_shutdown_begun(monitor)

    monitor.check_live_streams_and_start_download()

    loop_parked = ThreadEvent()
    release_loop = ThreadEvent()
    original_handle_monitor_event = monitor._handle_monitor_event

    def gated_handle(event):
        if not loop_parked.is_set():
            loop_parked.set()
            assert release_loop.wait(timeout=5)
        return original_handle_monitor_event(event)

    monitor._handle_monitor_event = gated_handle
    # Parks the control loop on an event it will discard, so the retry poll
    # queued below is still waiting when shutdown starts.
    monitor._on_raw_download_failed(
        RawDownloadFailed(session_key="no-such-session", error_message="parks the loop")
    )
    assert loop_parked.wait(timeout=5)

    # Queued while shutdown had not begun, so the enqueue is legitimate: only
    # the dequeue can tell that serving it is no longer wanted.
    assert monitor._request_retry_poll("creator1")

    shutdown_thread = Thread(target=monitor.shutdown, daemon=True)
    shutdown_thread.start()
    assert shutdown_begun.wait(timeout=5)
    release_loop.set()
    shutdown_thread.join(timeout=30)

    assert not shutdown_thread.is_alive()
    # Still only the poll this test asked for: the queued retry poll was
    # dropped rather than served after shutdown began.
    assert mock_api.get_livestream_status.call_count == 1
    # Dropped, not wedged: the marker reopens so a live monitor could retry.
    assert monitor._retry_poll_queued is False
