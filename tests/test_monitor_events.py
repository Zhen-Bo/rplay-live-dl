"""Tests for event-driven monitor behavior."""

import inspect

from threading import Event as ThreadEvent, Thread

from models.download import MergeCompleted

from datetime import datetime
from unittest.mock import MagicMock, patch

from core.live_stream_monitor import LiveStreamMonitor
from models.config import CreatorProfile
from core.rplay import RPlayAPI
from models.download import (
    DownloadSession,
    RawDownloadCompleted,
    RawDownloadFailed,
    SessionState,
)


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
        shutdown_thread.join(timeout=2)
        monitor._event_queue.join()

    assert monitor.sessions[session_key].state == SessionState.DONE


class _FakeRecording:
    """
    Stand-in for a StreamDownloader whose thread is still recording.

    Mirrors the parts of the real downloader that shutdown drives: it is alive
    until asked to stop, and only then reports its terminal event.
    """

    def __init__(self, monitor, session_key, output_dir, terminal_event=None):
        self.creator_name = "Creator1"
        self._monitor = monitor
        self._session_key = session_key
        self._output_dir = output_dir
        self._terminal_event = terminal_event
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
        event = self._terminal_event
        if event is None:
            event = RawDownloadCompleted(
                session_key=self._session_key,
                output_dir=self._output_dir,
            )
        self._monitor._on_raw_download_complete(event)


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

    def fake_terminate(timeout_seconds=10.0, exclude_pids=None):
        captured["exclude_pids"] = set(exclude_pids or ())
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

    assert merge_pid in captured["exclude_pids"]


def test_shutdown_does_not_sweep_when_no_recording_is_active(tmp_path):
    """Test an idle monitor shuts down without reaping unrelated child processes."""
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)

    with patch("core.live_stream_monitor.terminate_child_processes") as mock_terminate:
        monitor.shutdown()

    mock_terminate.assert_not_called()


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


def test_merge_submission_after_shutdown_is_refused_not_raised(tmp_path):
    """Test a straggler raw completion is recorded instead of crashing the loop."""
    mock_api = MagicMock(spec=RPlayAPI)
    monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=mock_api)

    with patch("core.live_stream_monitor.terminate_child_processes"):
        monitor.shutdown()

    session_key = _make_running_session(monitor, tmp_path)

    # Straight through the handler: the state-lock gate must turn this into a
    # recorded failure, not the RuntimeError the control loop used to swallow.
    monitor._handle_raw_download_completed(
        RawDownloadCompleted(session_key=session_key, output_dir=tmp_path)
    )

    assert monitor.sessions[session_key].state == SessionState.MERGE_FAILED
