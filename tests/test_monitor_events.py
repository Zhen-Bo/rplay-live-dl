"""Tests for event-driven monitor behavior."""

import inspect

from threading import Event as ThreadEvent, Thread

from models.download import MergeCompleted

from datetime import datetime
from unittest.mock import MagicMock, patch

from core.live_stream_monitor import LiveStreamMonitor
from core.rplay import RPlayAPI
from models.download import DownloadSession, RawDownloadCompleted, SessionState


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
        staging_dir=tmp_path,
    )

    with patch.object(monitor.merge_executor, "submit_merge") as mock_submit:
        monitor._on_raw_download_complete(
            RawDownloadCompleted(session_key=session_key, staging_dir=tmp_path)
        )
        monitor._event_queue.join()

    assert monitor.sessions[session_key].state == SessionState.MERGE_QUEUED
    mock_submit.assert_called_once()
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
        staging_dir=tmp_path,
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
        staging_dir=tmp_path,
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
        staging_dir=tmp_path,
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
            RawDownloadCompleted(session_key=session_key, staging_dir=tmp_path)
        )
        assert handle_started.wait(timeout=1)

        shutdown_thread = Thread(target=monitor.shutdown)
        shutdown_thread.start()
        release_handle.set()
        shutdown_thread.join(timeout=2)
        monitor._event_queue.join()

    assert monitor.sessions[session_key].state == SessionState.DONE
