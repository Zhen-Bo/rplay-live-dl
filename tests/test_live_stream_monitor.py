"""Tests for live stream monitor module."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from core.config import ConfigError
from core.live_stream_monitor import LiveStreamMonitor
from core.rplay import RPlayAPI, RPlayAPIError, RPlayAuthError, RPlayConnectionError
from models.config import AppConfig, CreatorProfile
from models.download import (
    DownloadSession,
    MergeCompleted,
    RawDownloadBlocked,
    RawDownloadCompleted,
    SessionState,
)
from models.rplay import CreatorStreamState, StreamState


def _runtime_config(creators):
    """Build AppConfig test data with the default hot-reload API base URL."""
    return AppConfig(api_base_url="https://api.rplay.live", creators=creators)


@pytest.fixture
def mock_api():
    """Create a mock RPlayAPI."""
    return MagicMock(spec=RPlayAPI)


@pytest.fixture
def monitor(mock_api):
    """Create a LiveStreamMonitor with mock API."""
    return LiveStreamMonitor(
        auth_token="test_token",
        user_oid="test_oid",
        api=mock_api,
    )


class TestLiveStreamMonitorInit:
    """Tests for LiveStreamMonitor initialization."""

    @patch('core.live_stream_monitor.RPlayAPI')
    def test_init_creates_api_if_not_provided(self, mock_api_class):
        """Test that API is created when not provided."""
        mock_api_class.return_value = MagicMock()
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
        )
        mock_api_class.assert_called_once_with("test_token", "test_oid")

    def test_init_uses_injected_api(self, mock_api):
        """Test that injected API is used instead of creating new one."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        assert monitor.api is mock_api

    def test_init_empty_monitored_creators(self, monitor):
        """Test that monitored creators dict is empty on init."""
        assert monitor.monitored_creators == {}

    def test_init_sets_config_path(self, mock_api):
        """Test that config path is set correctly."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            config_path="/custom/path.yaml",
            api=mock_api,
        )
        assert monitor.config_path == "/custom/path.yaml"

    def test_init_default_config_path(self, monitor):
        """Test default config path value."""
        assert monitor.config_path == "./config/config.yaml"

    def test_is_healthy_initial_state(self, monitor):
        """Test that initial state is healthy."""
        assert monitor.is_healthy is True


class TestSessionAwareMonitoring:
    """Tests for session-aware monitor behavior."""

    @patch('core.live_stream_monitor.read_config')
    @patch('core.live_stream_monitor.StreamDownloader.download')
    def test_same_stream_oid_does_not_restart_download_when_start_time_changes(
        self, mock_download, mock_read_config, mock_api
    ):
        """Test the same live stream oid is only downloaded once across polls."""
        first_stream = MagicMock()
        first_stream.oid = "stream-1"
        first_stream.creator_oid = "creator1"
        first_stream.stream_state = StreamState.LIVE
        first_stream.stream_start_time = datetime(2026, 3, 7, 5, 3, 40)
        first_stream.title = "Same Stream"

        second_stream = MagicMock()
        second_stream.oid = "stream-1"
        second_stream.creator_oid = "creator1"
        second_stream.stream_state = StreamState.LIVE
        second_stream.stream_start_time = datetime(2026, 3, 7, 5, 4, 40)
        second_stream.title = "Same Stream"

        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.validate_m3u8_url.return_value = True
        mock_api.get_livestream_status.side_effect = [[first_stream], [second_stream]]
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        monitor.check_live_streams_and_start_download()
        monitor.check_live_streams_and_start_download()

        mock_download.assert_called_once_with(
            "http://example.com/stream.m3u8", "Same Stream"
        )

    @patch('core.live_stream_monitor.read_config')
    @patch('core.live_stream_monitor.StreamDownloader.download')
    def test_new_stream_oid_starts_second_stream_same_day(
        self, mock_download, mock_read_config, mock_api, tmp_path
    ):
        """Test a second same-day stream starts when the stream oid changes."""
        first_stream = MagicMock()
        first_stream.oid = "stream-1"
        first_stream.creator_oid = "creator1"
        first_stream.stream_state = StreamState.LIVE
        first_stream.stream_start_time = datetime(2026, 3, 7, 5, 3, 40)
        first_stream.title = "Repeated Title"

        second_stream = MagicMock()
        second_stream.oid = "stream-2"
        second_stream.creator_oid = "creator1"
        second_stream.stream_state = StreamState.LIVE
        second_stream.stream_start_time = datetime(2026, 3, 7, 6, 3, 40)
        second_stream.title = "Repeated Title"

        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.validate_m3u8_url.return_value = True
        mock_api.get_livestream_status.side_effect = [[first_stream], [second_stream]]
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        monitor.check_live_streams_and_start_download()
        monitor._handle_monitor_event(
            RawDownloadCompleted(
                session_key=monitor._make_session_key(first_stream),
                staging_dir=tmp_path / "first-staging",
            )
        )
        monitor.check_live_streams_and_start_download()

        assert mock_download.call_count == 2

    @patch('core.live_stream_monitor.read_config')
    @patch('core.live_stream_monitor.StreamDownloader.download')
    def test_same_stream_oid_starts_second_stream_when_started_at_changes_after_done(
        self, mock_download, mock_read_config, mock_api, tmp_path
    ):
        """Test a reused stream oid still starts a new download when started_at changes."""
        first_stream = MagicMock()
        first_stream.oid = "stream-1"
        first_stream.creator_oid = "creator1"
        first_stream.stream_state = StreamState.LIVE
        first_stream.stream_start_time = datetime(2026, 3, 5, 10, 47, 43)
        first_stream.title = "Repeated Title"

        second_stream = MagicMock()
        second_stream.oid = "stream-1"
        second_stream.creator_oid = "creator1"
        second_stream.stream_state = StreamState.LIVE
        second_stream.stream_start_time = datetime(2026, 3, 7, 10, 47, 52)
        second_stream.title = "Repeated Title"

        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.validate_m3u8_url.return_value = True
        mock_api.get_livestream_status.side_effect = [[first_stream], [second_stream]]
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        monitor.check_live_streams_and_start_download()

        first_session_key = next(iter(monitor.sessions))
        monitor.sessions[first_session_key].state = SessionState.DONE

        monitor.check_live_streams_and_start_download()

        assert mock_download.call_count == 2

    @patch('core.live_stream_monitor.read_config')
    @patch('core.live_stream_monitor.StreamDownloader.download')
    def test_marks_creator_raw_running_before_get_stream_url(
        self, mock_download, mock_read_config, mock_api
    ):
        """Test creator raw-running state is visible before stream URL fetching starts."""
        mock_stream = MagicMock()
        mock_stream.oid = "stream-1"
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = datetime(2026, 3, 7, 5, 3, 40)
        mock_stream.title = "Same Stream"

        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        def assert_eager_state(creator_oid: str) -> str:
            active_session_key = monitor._active_raw_session_by_creator[creator_oid]
            assert active_session_key in monitor.sessions
            assert monitor.sessions[active_session_key].state == SessionState.RAW_RUNNING
            assert (
                monitor._creator_states[creator_oid].last_stream_start_time
                == mock_stream.stream_start_time
            )
            return "http://example.com/stream.m3u8"

        mock_api.get_stream_url.side_effect = assert_eager_state
        mock_api.validate_m3u8_url.return_value = True

        monitor.check_live_streams_and_start_download()

        mock_download.assert_called_once_with(
            "http://example.com/stream.m3u8", "Same Stream"
        )

    @patch('core.live_stream_monitor.read_config')
    @patch('core.live_stream_monitor.StreamDownloader.download')
    def test_new_session_starts_while_old_session_is_merging(
        self, mock_download, mock_read_config, mock_api, tmp_path
    ):
        """Test a new session can start while an older session is merging."""
        old_time = datetime(2026, 3, 6, 12, 0, 0)
        new_time = datetime(2026, 3, 6, 12, 5, 0)
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = new_time
        mock_stream.title = "Test Stream"

        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.validate_m3u8_url.return_value = True
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.sessions["creator1:2026-03-06T12:00:00"] = DownloadSession(
            session_key="creator1:2026-03-06T12:00:00",
            creator_oid="creator1",
            creator_name="Creator1",
            title="Old Stream",
            stream_start_time=old_time,
            state=SessionState.MERGING,
            staging_dir=tmp_path / "old",
        )

        monitor.check_live_streams_and_start_download()

        mock_api.get_stream_url.assert_called_once_with("creator1")
        mock_download.assert_called_once_with(
            "http://example.com/stream.m3u8", "Test Stream"
        )

    def test_process_live_stream_prunes_superseded_terminal_sessions(
        self, mock_api, tmp_path
    ):
        """Test a newer session prunes older terminal sessions for the same creator."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        old_session_key = "creator1:1772798400"
        monitor.monitored_creators["creator1"] = CreatorProfile(
            creator_name="Creator1",
            creator_oid="creator1",
        )
        monitor.sessions[old_session_key] = DownloadSession(
            session_key=old_session_key,
            creator_oid="creator1",
            creator_name="Creator1",
            title="Old Stream",
            stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
            state=SessionState.DONE,
            staging_dir=tmp_path / "old",
        )
        mock_stream = MagicMock()
        mock_stream.oid = "stream-2"
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = datetime(2026, 3, 6, 12, 5, 0)
        mock_stream.title = "New Stream"

        with patch.object(monitor, "_start_download") as mock_start_download:
            monitor._process_live_stream(mock_stream)

        assert old_session_key not in monitor.sessions
        assert monitor.latest_stream_oid_by_creator["creator1"] == "stream-2"
        mock_start_download.assert_called_once_with(mock_stream)

    def test_cleanup_offline_creator_states_prunes_terminal_sessions(
        self, mock_api, tmp_path
    ):
        """Test offline creators drop terminal sessions during cleanup."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        offline_session_key = "creator1:2026-03-06T12:00:00"
        active_session_key = "creator2:2026-03-06T12:05:00"
        monitor.sessions[offline_session_key] = DownloadSession(
            session_key=offline_session_key,
            creator_oid="creator1",
            creator_name="Creator1",
            title="Finished Stream",
            stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
            state=SessionState.DONE,
            staging_dir=tmp_path / "creator1",
        )
        monitor.sessions[active_session_key] = DownloadSession(
            session_key=active_session_key,
            creator_oid="creator2",
            creator_name="Creator2",
            title="Active Stream",
            stream_start_time=datetime(2026, 3, 6, 12, 5, 0),
            state=SessionState.RAW_RUNNING,
            staging_dir=tmp_path / "creator2",
        )
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_oid="stream-1"
        )
        monitor._creator_states["creator2"] = CreatorStreamState(
            last_stream_oid="stream-2"
        )

        monitor._cleanup_offline_creator_states({"creator2"})

        assert offline_session_key not in monitor.sessions
        assert active_session_key in monitor.sessions

    @patch('core.live_stream_monitor.read_config')
    @patch('core.live_stream_monitor.StreamDownloader.download')
    def test_same_session_not_started_twice(
        self, mock_download, mock_read_config, mock_api, tmp_path
    ):
        """Test the same session is skipped when already running."""
        start_time = datetime(2026, 3, 6, 12, 0, 0)
        mock_stream = MagicMock()
        mock_stream.oid = "stream-1"
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = start_time
        mock_stream.title = "Test Stream"

        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.sessions["creator1:1772798400"] = DownloadSession(
            session_key="creator1:1772798400",
            creator_oid="creator1",
            creator_name="Creator1",
            title="Test Stream",
            stream_start_time=start_time,
            state=SessionState.RAW_RUNNING,
            staging_dir=tmp_path / "running",
        )

        monitor.check_live_streams_and_start_download()

        mock_api.get_stream_url.assert_not_called()
        mock_download.assert_not_called()

    def test_raw_completion_queues_merge(self, mock_api, tmp_path):
        """Test raw completion marks the session queued and submits merge work."""
        session_key = "creator1:2026-03-06T12:00:00"
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.sessions[session_key] = DownloadSession(
            session_key=session_key,
            creator_oid="creator1",
            creator_name="Creator1",
            title="Test Stream",
            stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
            state=SessionState.RAW_RUNNING,
            staging_dir=tmp_path,
        )
        result = RawDownloadCompleted(
            session_key=session_key,
            staging_dir=tmp_path,
        )

        with patch.object(monitor.merge_executor, 'submit_merge') as mock_submit:
            monitor._on_raw_download_complete(result)
            monitor._event_queue.join()

        assert monitor.sessions[session_key].state == SessionState.MERGE_QUEUED
        mock_submit.assert_called_once()


class TestUpdateDownloaders:
    """Tests for _update_downloaders method."""

    @patch('core.live_stream_monitor.read_config')
    def test_updates_api_base_url_from_config(self, mock_read_config, monitor, mock_api):
        """Test config reload pushes the latest apiBaseUrl into the API client."""
        mock_read_config.side_effect = [
            AppConfig(
                api_base_url="https://api.rplay.live",
                creators=[
                    CreatorProfile(creator_name="Creator1", creator_oid="oid1"),
                ],
            ),
            AppConfig(
                api_base_url="https://api-alt.rplay.live",
                creators=[
                    CreatorProfile(creator_name="Creator1", creator_oid="oid1"),
                ],
            ),
        ]

        monitor._update_downloaders()
        monitor._update_downloaders()

        assert mock_api.set_base_url.call_args_list[0].args == (
            "https://api.rplay.live",
        )
        assert mock_api.set_base_url.call_args_list[1].args == (
            "https://api-alt.rplay.live",
        )

    @patch('core.live_stream_monitor.read_config')
    def test_adds_new_creators(self, mock_read_config, monitor):
        """Test that monitored creators are refreshed from config."""
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="oid1"),
            CreatorProfile(creator_name="Creator2", creator_oid="oid2"),
        ])
        monitor._update_downloaders()
        assert "oid1" in monitor.monitored_creators
        assert "oid2" in monitor.monitored_creators
        assert len(monitor.monitored_creators) == 2

    @patch('core.live_stream_monitor.StreamDownloader')
    @patch('core.live_stream_monitor.read_config')
    def test_does_not_create_template_stream_downloaders(
        self, mock_read_config, mock_stream_downloader, monitor
    ):
        """Test creator refresh does not allocate unused template downloaders."""
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="oid1"),
        ])

        monitor._update_downloaders()

        mock_stream_downloader.assert_not_called()

    @patch('core.live_stream_monitor.read_config')
    def test_removes_inactive_not_in_config(self, mock_read_config, monitor):
        """Test that monitored creators not in config are removed."""
        monitor.monitored_creators["old_oid"] = CreatorProfile(
            creator_name="OldCreator",
            creator_oid="old_oid",
        )

        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="NewCreator", creator_oid="new_oid"),
        ])
        monitor._update_downloaders()
        assert "old_oid" not in monitor.monitored_creators
        assert "new_oid" in monitor.monitored_creators

    @patch('core.live_stream_monitor.read_config')
    def test_active_session_does_not_require_monitored_creator_entry(
        self, mock_read_config, monitor, tmp_path
    ):
        """Test active sessions keep their own metadata even after config removal."""
        monitor.sessions["active_oid:2026-03-06T12:00:00"] = DownloadSession(
            session_key="active_oid:2026-03-06T12:00:00",
            creator_oid="active_oid",
            creator_name="ActiveCreator",
            title="Test Stream",
            stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
            state=SessionState.RAW_RUNNING,
            staging_dir=tmp_path,
        )

        mock_read_config.return_value = _runtime_config([])
        monitor._update_downloaders()
        assert monitor.monitored_creators == {}
        assert "active_oid:2026-03-06T12:00:00" in monitor.sessions

    @patch('core.live_stream_monitor.read_config')
    def test_raises_config_error(self, mock_read_config, monitor):
        """Test that ConfigError is re-raised."""
        mock_read_config.side_effect = ConfigError("Config file not found")
        with pytest.raises(ConfigError):
            monitor._update_downloaders()

    @patch('core.live_stream_monitor.read_config')
    def test_updates_monitored_count(self, mock_read_config, monitor):
        """Test that monitored count is updated."""
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="C1", creator_oid="o1"),
            CreatorProfile(creator_name="C2", creator_oid="o2"),
            CreatorProfile(creator_name="C3", creator_oid="o3"),
        ])
        monitor._update_downloaders()
        assert monitor._monitored_count == 3


class TestStartDownload:
    """Tests for _start_download method."""

    def test_start_download_success(self, mock_api, monitor):
        """Test successful download start."""
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.validate_m3u8_url.return_value = True
        mock_stream = MagicMock()
        mock_stream.creator_oid = "test_oid"
        mock_stream.title = "Test Stream"
        mock_stream.stream_start_time = datetime(2026, 3, 6, 12, 0, 0)
        monitor.monitored_creators["test_oid"] = CreatorProfile(
            creator_name="TestCreator",
            creator_oid="test_oid",
        )

        with patch('core.live_stream_monitor.StreamDownloader.download') as mock_download:
            monitor._start_download(mock_stream)

        mock_api.get_stream_url.assert_called_once_with("test_oid")
        mock_download.assert_called_once_with(
            "http://example.com/stream.m3u8", "Test Stream"
        )

    def test_start_download_logs_live_emoji(self, mock_api, monitor):
        """Test the first live log preserves the visible red-circle marker."""
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.validate_m3u8_url.return_value = True
        mock_stream = MagicMock()
        mock_stream.creator_oid = "test_oid"
        mock_stream.title = "Test Stream"
        mock_stream.stream_start_time = datetime(2026, 3, 6, 12, 0, 0)
        monitor.monitored_creators["test_oid"] = CreatorProfile(
            creator_name="TestCreator",
            creator_oid="test_oid",
        )

        with (
            patch('core.live_stream_monitor.StreamDownloader.download'),
            patch.object(monitor.logger, 'info') as mock_info,
        ):
            monitor._start_download(mock_stream)

        assert mock_info.call_args_list[0].args[0] == "🔴 TestCreator is live: \"Test Stream\""

    def test_start_download_auth_error(self, mock_api, monitor):
        """Test auth error is logged."""
        mock_api.get_stream_url.side_effect = RPlayAuthError("Unauthorized")
        mock_stream = MagicMock()
        mock_stream.creator_oid = "test_oid"
        mock_stream.title = "Test Stream"
        monitor.monitored_creators["test_oid"] = CreatorProfile(
            creator_name="TestCreator",
            creator_oid="test_oid",
        )

        with patch('core.live_stream_monitor.StreamDownloader.download') as mock_download:
            monitor._start_download(mock_stream)

        mock_download.assert_not_called()

    def test_start_download_validation_401_logs_auth_error(self, mock_api, monitor):
        """Test playlist validation 401 is treated as a credential problem."""
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.validate_m3u8_url.side_effect = RPlayAuthError("Authentication failed")
        mock_stream = MagicMock()
        mock_stream.creator_oid = "test_oid"
        mock_stream.title = "Test Stream"
        monitor.monitored_creators["test_oid"] = CreatorProfile(
            creator_name="TestCreator",
            creator_oid="test_oid",
        )

        with (
            patch('core.live_stream_monitor.StreamDownloader.download') as mock_download,
            patch.object(monitor.logger, 'error') as mock_error,
        ):
            monitor._start_download(mock_stream)

        mock_download.assert_not_called()
        assert any(
            "credential" in call.args[0].lower() or "auth" in call.args[0].lower()
            for call in mock_error.call_args_list
        )

    def test_start_download_api_error(self, mock_api, monitor):
        """Test API error is logged as warning."""
        mock_api.get_stream_url.side_effect = RPlayAPIError("API Error")
        mock_stream = MagicMock()
        mock_stream.creator_oid = "test_oid"
        mock_stream.title = "Test Stream"
        monitor.monitored_creators["test_oid"] = CreatorProfile(
            creator_name="TestCreator",
            creator_oid="test_oid",
        )

        with patch('core.live_stream_monitor.StreamDownloader.download') as mock_download:
            monitor._start_download(mock_stream)

        mock_download.assert_not_called()

    def test_start_download_unexpected_error(self, mock_api, monitor):
        """Test unexpected error is caught and logged."""
        mock_api.get_stream_url.side_effect = RuntimeError("Unexpected")
        mock_stream = MagicMock()
        mock_stream.creator_oid = "test_oid"
        mock_stream.title = "Test Stream"
        monitor.monitored_creators["test_oid"] = CreatorProfile(
            creator_name="TestCreator",
            creator_oid="test_oid",
        )

        with patch('core.live_stream_monitor.StreamDownloader.download') as mock_download:
            monitor._start_download(mock_stream)

        mock_download.assert_not_called()


class TestCheckLiveStreams:
    """Tests for check_live_streams_and_start_download method."""

    @patch('core.live_stream_monitor.read_config')
    def test_no_live_streams(self, mock_read_config):
        """Test no download when no live streams."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_api.get_livestream_status.return_value = []
        mock_read_config.return_value = _runtime_config([])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.check_live_streams_and_start_download()
        assert monitor.is_healthy is True

    @patch('core.live_stream_monitor.read_config')
    def test_live_but_not_monitored(self, mock_read_config):
        """Test no download when creator is live but not monitored."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_stream = MagicMock()
        mock_stream.creator_oid = "unknown_oid"
        mock_stream.stream_state = StreamState.LIVE
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_read_config.return_value = _runtime_config([])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.check_live_streams_and_start_download()
        mock_api.get_stream_url.assert_not_called()

    @patch('core.live_stream_monitor.read_config')
    def test_live_and_monitored_starts_download(self, mock_read_config):
        """Test download starts when monitored creator is live."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator_oid"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.title = "Live Stream"
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator", creator_oid="creator_oid"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.check_live_streams_and_start_download()
        mock_api.get_stream_url.assert_called_once_with("creator_oid")

    @patch('core.live_stream_monitor.read_config')
    def test_already_downloading_no_restart(self, mock_read_config, tmp_path):
        """Test no restart if already downloading."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_stream = MagicMock()
        mock_stream.oid = "stream-1"
        mock_stream.creator_oid = "creator_oid"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = datetime(2026, 3, 6, 12, 0, 0)
        mock_stream.title = "Test Stream"
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator", creator_oid="creator_oid"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.sessions["creator_oid:1772798400"] = DownloadSession(
            session_key="creator_oid:1772798400",
            creator_oid="creator_oid",
            creator_name="Creator",
            title="Test Stream",
            stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
            state=SessionState.RAW_RUNNING,
            staging_dir=tmp_path,
        )

        mock_api.get_stream_url.reset_mock()
        monitor.check_live_streams_and_start_download()
        mock_api.get_stream_url.assert_not_called()

    @patch('core.live_stream_monitor.read_config')
    def test_stream_not_live_state(self, mock_read_config):
        """Test no download when stream state is not LIVE."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator_oid"
        mock_stream.stream_state = StreamState.TWITCH
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator", creator_oid="creator_oid"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.check_live_streams_and_start_download()
        mock_api.get_stream_url.assert_not_called()


class TestCheckLiveStreamsErrorHandling:
    """Tests for error handling in check_live_streams_and_start_download."""

    @patch('core.live_stream_monitor.read_config')
    def test_config_error_sets_unhealthy(self, mock_read_config):
        """Test ConfigError sets healthy=False."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_read_config.side_effect = ConfigError("Config error")
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.check_live_streams_and_start_download()
        assert monitor.is_healthy is False

    @patch('core.live_stream_monitor.read_config')
    def test_auth_error_sets_unhealthy(self, mock_read_config):
        """Test RPlayAuthError sets healthy=False."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_read_config.return_value = _runtime_config([])
        mock_api.get_livestream_status.side_effect = RPlayAuthError("Unauthorized")
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.check_live_streams_and_start_download()
        assert monitor.is_healthy is False

    @patch('core.live_stream_monitor.read_config')
    def test_connection_error_sets_unhealthy(self, mock_read_config):
        """Test RPlayConnectionError sets healthy=False."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_read_config.return_value = _runtime_config([])
        mock_api.get_livestream_status.side_effect = RPlayConnectionError("Network error")
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.check_live_streams_and_start_download()
        assert monitor.is_healthy is False

    @patch('core.live_stream_monitor.read_config')
    def test_api_error_sets_unhealthy(self, mock_read_config):
        """Test RPlayAPIError sets healthy=False."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_read_config.return_value = _runtime_config([])
        mock_api.get_livestream_status.side_effect = RPlayAPIError("API error")
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.check_live_streams_and_start_download()
        assert monitor.is_healthy is False

    @patch('core.live_stream_monitor.read_config')
    def test_unexpected_error_sets_unhealthy(self, mock_read_config):
        """Test unexpected exception sets healthy=False."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_read_config.return_value = _runtime_config([])
        mock_api.get_livestream_status.side_effect = RuntimeError("Unexpected")
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.check_live_streams_and_start_download()
        assert monitor.is_healthy is False

    @patch('core.live_stream_monitor.read_config')
    def test_success_restores_healthy(self, mock_read_config):
        """Test successful check restores healthy=True after failure."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_read_config.return_value = _runtime_config([])
        mock_api.get_livestream_status.return_value = []
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        # Force unhealthy state
        monitor._last_check_success = False
        assert monitor.is_healthy is False

        # Successful check should restore health
        monitor.check_live_streams_and_start_download()
        assert monitor.is_healthy is True

    @patch('core.live_stream_monitor.read_config')
    def test_config_error_logged_once(self, mock_read_config):
        """Test config failures are logged once at the poll-cycle boundary."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_read_config.side_effect = ConfigError("Config file not found")
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        with patch.object(monitor.logger, 'warning') as mock_warning:
            monitor.check_live_streams_and_start_download()

        assert mock_warning.call_count == 1
        assert mock_warning.call_args.args[0] == 'Skipping check due to config file error'

    @patch('core.live_stream_monitor.read_config')
    def test_unexpected_update_error_logged_once(self, mock_read_config):
        """Test unexpected update failures are logged once at the poll-cycle boundary."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_read_config.side_effect = RuntimeError("boom")
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        with patch.object(monitor.logger, 'error') as mock_error:
            monitor.check_live_streams_and_start_download()

        assert mock_error.call_count == 1
        assert mock_error.call_args.args[0] == 'Unexpected error during monitoring: boom'


class TestGetActiveDownloads:
    """Tests for get_active_downloads method."""

    def test_empty_list_when_no_downloads(self):
        """Test returns empty list when no downloaders."""
        mock_api = MagicMock(spec=RPlayAPI)
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        assert monitor.get_active_downloads() == []

    def test_empty_list_when_all_inactive(self, tmp_path):
        """Test returns empty list when no raw-running sessions exist."""
        mock_api = MagicMock(spec=RPlayAPI)
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.sessions["oid1:2026-03-06T12:00:00"] = DownloadSession(
            session_key="oid1:2026-03-06T12:00:00",
            creator_oid="oid1",
            creator_name="Inactive",
            title="Test Stream",
            stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
            state=SessionState.MERGING,
            staging_dir=tmp_path,
        )

        assert monitor.get_active_downloads() == []

    def test_returns_active_creator_names(self, tmp_path):
        """Test returns list of creator names with active raw sessions."""
        mock_api = MagicMock(spec=RPlayAPI)
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.sessions["active:2026-03-06T12:00:00"] = DownloadSession(
            session_key="active:2026-03-06T12:00:00",
            creator_oid="active",
            creator_name="ActiveCreator",
            title="Test Stream",
            stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
            state=SessionState.RAW_RUNNING,
            staging_dir=tmp_path,
        )
        monitor.sessions["inactive:2026-03-06T11:00:00"] = DownloadSession(
            session_key="inactive:2026-03-06T11:00:00",
            creator_oid="inactive",
            creator_name="InactiveCreator",
            title="Old Stream",
            stream_start_time=datetime(2026, 3, 6, 11, 0, 0),
            state=SessionState.MERGING,
            staging_dir=tmp_path,
        )

        result = monitor.get_active_downloads()
        assert result == ["ActiveCreator"]

    def test_returns_multiple_active(self, tmp_path):
        """Test returns multiple active creator names from raw-running sessions."""
        mock_api = MagicMock(spec=RPlayAPI)
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        for i in range(3):
            monitor.sessions[f"oid{i}:2026-03-06T12:0{i}:00"] = DownloadSession(
                session_key=f"oid{i}:2026-03-06T12:0{i}:00",
                creator_oid=f"oid{i}",
                creator_name=f"Creator{i}",
                title="Test Stream",
                stream_start_time=datetime(2026, 3, 6, 12, i, 0),
                state=SessionState.RAW_RUNNING,
                staging_dir=tmp_path,
            )

        result = monitor.get_active_downloads()
        assert len(result) == 3
        assert "Creator0" in result
        assert "Creator1" in result
        assert "Creator2" in result


class TestCreatorStateTracking:
    """Tests for creator stream state tracking functionality."""

    def test_init_has_empty_creator_states(self, mock_api):
        """Test that creator states dict is empty on init."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        assert monitor._creator_states == {}

    def test_is_new_stream_for_creator_no_previous_state(self, mock_api):
        """Test new session detection when no previous state exists."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        mock_stream = MagicMock()
        mock_stream.oid = "stream-1"
        mock_stream.creator_oid = "creator1"

        result = monitor._is_new_stream_for_creator(mock_stream)

        assert result is True

    def test_is_new_stream_for_creator_same_start_time(self, mock_api):
        """Test returns False when the creator is still on the same stream start time."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
            last_stream_oid="stream-1",
        )
        mock_stream = MagicMock()
        mock_stream.oid = "stream-1"
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_start_time = datetime(2026, 1, 26, 12, 0, 0)

        result = monitor._is_new_stream_for_creator(mock_stream)

        assert result is False

    def test_is_new_stream_for_creator_different_start_time(self, mock_api):
        """Test returns True when the creator switches to a new stream start time."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
            last_stream_oid="stream-1",
        )
        mock_stream = MagicMock()
        mock_stream.oid = "stream-2"
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_start_time = datetime(2026, 1, 26, 14, 0, 0)

        result = monitor._is_new_stream_for_creator(mock_stream)

        assert result is True

    def test_update_creator_stream_state_creates_new_state(self, mock_api):
        """Test that updating state creates new entry if none exists."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        mock_stream = MagicMock()
        mock_stream.oid = "stream-1"
        mock_stream.creator_oid = "creator1"

        monitor._update_creator_stream_state(mock_stream)

        assert "creator1" in monitor._creator_states
        assert monitor._creator_states["creator1"].last_stream_oid == "stream-1"
        assert monitor._creator_states["creator1"].is_current_stream_blocked is False

    def test_update_creator_stream_state_updates_existing(self, mock_api):
        """Test that updating state modifies existing entry."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_oid="stream-1",
            is_current_stream_blocked=True,
        )
        mock_stream = MagicMock()
        mock_stream.oid = "stream-2"
        mock_stream.creator_oid = "creator1"

        monitor._update_creator_stream_state(mock_stream)

        assert monitor._creator_states["creator1"].last_stream_oid == "stream-2"
        assert monitor._creator_states["creator1"].is_current_stream_blocked is False

    def test_clear_creator_stream_state(self, mock_api):
        """Test clearing state for a creator."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_oid="stream-1",
            is_current_stream_blocked=True,
        )

        monitor._clear_creator_stream_state("creator1")

        assert "creator1" not in monitor._creator_states

    def test_clear_creator_stream_state_nonexistent(self, mock_api):
        """Test clearing state for nonexistent creator does not raise."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        # Should not raise
        monitor._clear_creator_stream_state("nonexistent")

    def test_handle_raw_download_blocked_creates_state_if_missing(self, mock_api, tmp_path):
        """Test blocked raw downloads create creator state when absent."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.sessions["creator1:2026-01-26T12:00:00"] = DownloadSession(
            session_key="creator1:2026-01-26T12:00:00",
            creator_oid="creator1",
            creator_name="Creator1",
            title="Test Stream",
            stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
            state=SessionState.RAW_RUNNING,
            staging_dir=tmp_path,
        )

        monitor._handle_raw_download_blocked(
            RawDownloadBlocked(
                session_key="creator1:2026-01-26T12:00:00",
                error_message="HTTP Error 404",
            )
        )

        assert monitor._creator_states["creator1"].is_current_stream_blocked is True


class TestM3u8ValidationIntegration:
    """Tests for M3U8 validation integration in download flow."""

    @patch('core.live_stream_monitor.read_config')
    def test_skips_blocked_stream_same_session(self, mock_read_config, mock_api):
        """Test that blocked streams are skipped in same session."""
        mock_stream = MagicMock()
        mock_stream.oid = "stream-1"
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = datetime(2026, 1, 26, 12, 0, 0)
        mock_stream.title = "Test Stream"
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        # Pre-populate state as blocked
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
            last_stream_oid="stream-1",
            is_current_stream_blocked=True,
        )

        monitor.check_live_streams_and_start_download()

        # Should not attempt to get stream URL
        mock_api.get_stream_url.assert_not_called()

    @patch('core.live_stream_monitor.read_config')
    def test_retries_blocked_stream_new_session(self, mock_read_config, mock_api):
        """Test that blocked streams are retried when new session starts."""
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = datetime(2026, 1, 26, 14, 0, 0)  # New time
        mock_stream.title = "New Stream"
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.validate_m3u8_url.return_value = True
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        # Pre-populate state as blocked with OLD start time
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
            last_stream_oid="stream-1",
            is_current_stream_blocked=True,
        )

        monitor.check_live_streams_and_start_download()

        # Should attempt to get stream URL (new session)
        mock_api.get_stream_url.assert_called_once()

    @patch('core.live_stream_monitor.read_config')
    def test_marks_blocked_on_m3u8_validation_failure(self, mock_read_config, mock_api):
        """Test that stream is marked blocked when M3U8 validation fails."""
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = datetime(2026, 1, 26, 12, 0, 0)
        mock_stream.title = "Test Stream"
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.validate_m3u8_url.return_value = False
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        monitor.check_live_streams_and_start_download()

        # Should mark as blocked
        assert monitor._creator_states["creator1"].is_current_stream_blocked is True

    @patch('core.live_stream_monitor.read_config')
    def test_starts_download_on_m3u8_validation_success(self, mock_read_config, mock_api):
        """Test that download starts when M3U8 validation succeeds."""
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = datetime(2026, 1, 26, 12, 0, 0)
        mock_stream.title = "Test Stream"
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.validate_m3u8_url.return_value = True
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        monitor.check_live_streams_and_start_download()

        # Should validate M3U8 URL
        mock_api.validate_m3u8_url.assert_called_once_with(
            "http://example.com/stream.m3u8"
        )
        # State should be updated (not blocked)
        assert monitor._creator_states["creator1"].is_current_stream_blocked is False

    @patch('core.live_stream_monitor.read_config')
    def test_clears_state_when_creator_not_in_list(self, mock_read_config, mock_api):
        """Test that creator state is cleared when not in live list."""
        mock_api.get_livestream_status.return_value = []  # No live streams
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        # Pre-populate state
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_oid="stream-1",
            is_current_stream_blocked=True,
        )

        monitor.check_live_streams_and_start_download()

        # State should be cleared
        assert "creator1" not in monitor._creator_states

    @patch('core.live_stream_monitor.read_config')
    def test_logs_warning_for_blocked_stream(self, mock_read_config, mock_api):
        """Test that warning is logged when stream is marked blocked."""
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = datetime(2026, 1, 26, 12, 0, 0)
        mock_stream.title = "Test Stream"
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.validate_m3u8_url.return_value = False
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        with patch.object(monitor.logger, 'warning') as mock_warning:
            monitor.check_live_streams_and_start_download()

        # Should log warning about blocked stream
        mock_warning.assert_called()
        warning_msg = mock_warning.call_args[0][0]
        assert "Creator1" in warning_msg or "creator1" in warning_msg


class TestSessionDownloadBlockedHandling:
    """Tests for session-scoped blocked download handling."""

    def test_blocked_event_marks_stream_as_blocked(self, mock_api, tmp_path):
        """Test that a blocked raw download updates session and creator state."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.sessions["creator1:2026-01-26T12:00:00"] = DownloadSession(
            session_key="creator1:2026-01-26T12:00:00",
            creator_oid="creator1",
            creator_name="Creator1",
            title="Test Stream",
            stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
            state=SessionState.RAW_RUNNING,
            staging_dir=tmp_path,
        )
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_oid="stream-1",
        )

        monitor._handle_raw_download_blocked(
            RawDownloadBlocked(
                session_key="creator1:2026-01-26T12:00:00",
                error_message="HTTP Error 404",
            )
        )

        assert monitor.sessions["creator1:2026-01-26T12:00:00"].state == SessionState.BLOCKED
        assert monitor._creator_states["creator1"].is_current_stream_blocked is True

    def test_blocked_event_logs_warning_only_once(self, mock_api, tmp_path):
        """Test repeated blocked events log only once for the same creator state."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.sessions["creator1:2026-01-26T12:00:00"] = DownloadSession(
            session_key="creator1:2026-01-26T12:00:00",
            creator_oid="creator1",
            creator_name="Creator1",
            title="Test Stream",
            stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
            state=SessionState.RAW_RUNNING,
            staging_dir=tmp_path,
        )
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_oid="stream-1",
        )

        with patch.object(monitor.logger, 'warning') as mock_warning:
            blocked_event = RawDownloadBlocked(
                session_key="creator1:2026-01-26T12:00:00",
                error_message="HTTP Error 404",
            )
            monitor._handle_raw_download_blocked(blocked_event)
            monitor._handle_raw_download_blocked(blocked_event)

        assert mock_warning.call_count == 1

    @patch('core.live_stream_monitor.read_config')
    def test_blocked_session_prevents_next_download(self, mock_read_config, mock_api, tmp_path):
        """Test blocked session state prevents another download in the same session."""
        mock_stream = MagicMock()
        mock_stream.oid = "stream-1"
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = datetime(2026, 1, 26, 12, 0, 0)
        mock_stream.title = "Test Stream"
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.validate_m3u8_url.return_value = True
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.sessions["creator1:1769428800"] = DownloadSession(
            session_key="creator1:1769428800",
            creator_oid="creator1",
            creator_name="Creator1",
            title="Test Stream",
            stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
            state=SessionState.BLOCKED,
            staging_dir=tmp_path,
        )
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
            last_stream_oid="stream-1",
            is_current_stream_blocked=True,
        )

        mock_api.get_stream_url.reset_mock()
        monitor.check_live_streams_and_start_download()
        mock_api.get_stream_url.assert_not_called()


class TestHeartbeatLogOptimization:
    """Tests for heartbeat log optimization (state-change only logging)."""

    @patch('core.live_stream_monitor.read_config')
    def test_no_status_log_when_state_unchanged(self, mock_read_config, mock_api):
        """Test that status log is not emitted when state is unchanged."""
        mock_api.get_livestream_status.return_value = []
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        with patch.object(monitor.logger, 'info') as mock_info:
            monitor.check_live_streams_and_start_download()
            first_call_count = mock_info.call_count

            monitor.check_live_streams_and_start_download()
            second_call_count = mock_info.call_count

        # Second call should not add new status logs
        assert second_call_count == first_call_count

    @patch('core.live_stream_monitor.read_config')
    def test_status_log_when_download_starts(self, mock_read_config, mock_api):
        """Test that status log is emitted when download count changes."""
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = datetime(2026, 1, 26, 12, 0, 0)
        mock_stream.title = "Test Stream"
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.validate_m3u8_url.return_value = True
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        with patch.object(monitor.logger, 'info') as mock_info:
            monitor.check_live_streams_and_start_download()

        # Should log status when download starts
        status_logs = [c for c in mock_info.call_args_list if "Status" in str(c)]
        assert len(status_logs) >= 1

    @patch('core.live_stream_monitor.read_config')
    def test_status_log_when_download_stops(self, mock_read_config, mock_api):
        """Test that status log is emitted when download count changes to zero."""
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        # Simulate previous state with active downloads
        monitor._last_status = {"active_downloads": 1, "monitored_live": 1}
        mock_api.get_livestream_status.return_value = []

        with patch.object(monitor.logger, 'info') as mock_info:
            monitor.check_live_streams_and_start_download()

        # Should log status when downloads stop
        status_logs = [c for c in mock_info.call_args_list if "Status" in str(c)]
        assert len(status_logs) >= 1

    @patch('core.live_stream_monitor.read_config')
    def test_periodic_heartbeat_every_n_checks(self, mock_read_config, mock_api):
        """Test that periodic heartbeat is logged every N checks even if state unchanged."""
        mock_api.get_livestream_status.return_value = []
        mock_read_config.return_value = _runtime_config([
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ])
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        with patch.object(monitor.logger, 'debug') as mock_debug:
            # Run multiple checks
            for _ in range(10):
                monitor.check_live_streams_and_start_download()

        # Should have at least one periodic heartbeat (debug level)
        heartbeat_logs = [c for c in mock_debug.call_args_list if "Checked" in str(c) or "heartbeat" in str(c).lower()]
        assert len(heartbeat_logs) >= 1


class TestSessionLifecycleLogging:
    """Tests for session lifecycle observability logs."""

    def test_process_live_stream_logs_debug_when_same_session_exists(self, mock_api, tmp_path):
        """Test same-session polls log an explicit existing-session skip reason."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.monitored_creators["creator1"] = CreatorProfile(
            creator_name="Creator1",
            creator_oid="creator1",
        )
        monitor.sessions["creator1:1772859660"] = DownloadSession(
            session_key="creator1:1772859660",
            creator_oid="creator1",
            creator_name="Creator1",
            title="Test Stream",
            stream_start_time=datetime(2026, 3, 7, 5, 1, 0),
            state=SessionState.RAW_RUNNING,
            staging_dir=tmp_path / "running",
        )

        mock_stream = MagicMock()
        mock_stream.oid = "stream-1"
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = datetime(2026, 3, 7, 5, 1, 0)
        mock_stream.title = "Test Stream"

        with patch.object(monitor.logger, "debug") as mock_debug:
            monitor._process_live_stream(mock_stream)

        assert any(
            "reason=existing_session_state" in str(call)
            for call in mock_debug.call_args_list
        )

    def test_process_live_stream_logs_skip_reason_when_creator_has_raw_running(
        self, mock_api, tmp_path
    ):
        """Test skip debug logs include a concrete reason for creator-level raw gating."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.monitored_creators["creator1"] = CreatorProfile(
            creator_name="Creator1",
            creator_oid="creator1",
        )
        monitor.sessions["creator1:1741323600"] = DownloadSession(
            session_key="creator1:1741323600",
            creator_oid="creator1",
            creator_name="Creator1",
            title="Test Stream",
            stream_start_time=datetime(2026, 3, 7, 5, 0, 0),
            state=SessionState.RAW_RUNNING,
            staging_dir=tmp_path / "running",
        )
        monitor._active_raw_session_by_creator["creator1"] = "creator1:1741323600"

        mock_stream = MagicMock()
        mock_stream.oid = "stream-1"
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = datetime(2026, 3, 7, 5, 1, 0)
        mock_stream.title = "Test Stream"

        with patch.object(monitor.logger, "debug") as mock_debug:
            monitor._process_live_stream(mock_stream)

        assert any(
            "reason=active_raw_running" in str(call)
            for call in mock_debug.call_args_list
        )

    def test_raw_download_completion_logs_merge_queue(self, mock_api, tmp_path):
        """Test raw completion logs that merge work has been queued."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.sessions["creator1:stream-1"] = DownloadSession(
            session_key="creator1:stream-1",
            creator_oid="creator1",
            creator_name="Creator1",
            title="Test Stream",
            stream_start_time=datetime(2026, 3, 7, 5, 0, 0),
            state=SessionState.RAW_RUNNING,
            staging_dir=tmp_path / "staging",
        )

        with patch.object(monitor.merge_executor, "submit_merge") as mock_submit_merge:
            with patch.object(monitor.logger, "info") as mock_info:
                monitor._handle_raw_download_completed(
                    RawDownloadCompleted(
                        session_key="creator1:stream-1",
                        staging_dir=tmp_path / "staging",
                    )
                )

        mock_submit_merge.assert_called_once()
        assert any("Queued merge" in str(call) for call in mock_info.call_args_list)

    def test_merge_completed_logs_final_output_path(self, mock_api, tmp_path):
        """Test merge completion logs the final output path."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.sessions["creator1:stream-1"] = DownloadSession(
            session_key="creator1:stream-1",
            creator_oid="creator1",
            creator_name="Creator1",
            title="Test Stream",
            stream_start_time=datetime(2026, 3, 7, 5, 0, 0),
            state=SessionState.MERGING,
            staging_dir=tmp_path / "staging",
        )
        output_path = tmp_path / "archive" / "Creator1" / "#Creator1 2026-03-07 Test Stream.mp4"

        with patch.object(monitor.logger, "info") as mock_info:
            monitor._handle_monitor_event(
                MergeCompleted(
                    session_key="creator1:stream-1",
                    output_path=output_path,
                )
            )

        assert any("Merge completed" in str(call) for call in mock_info.call_args_list)
