"""Tests for live stream monitor module."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from core.config import ConfigError
from core.live_stream_monitor import LiveStreamMonitor
from core.rplay import RPlayAPI, RPlayAPIError, RPlayAuthError, RPlayConnectionError
from models.config import CreatorProfile
from models.rplay import CreatorStreamState, StreamState


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

    def test_init_empty_downloaders(self, monitor):
        """Test that downloaders dict is empty on init."""
        assert monitor.downloaders == {}

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
        assert monitor.config_path == "./config.yaml"

    def test_is_healthy_initial_state(self, monitor):
        """Test that initial state is healthy."""
        assert monitor.is_healthy is True


class TestUpdateDownloaders:
    """Tests for _update_downloaders method."""

    @patch('core.live_stream_monitor.read_config')
    def test_adds_new_creators(self, mock_read_config, monitor):
        """Test that new creators from config are added."""
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator1", creator_oid="oid1"),
            CreatorProfile(creator_name="Creator2", creator_oid="oid2"),
        ]
        monitor._update_downloaders()
        assert "oid1" in monitor.downloaders
        assert "oid2" in monitor.downloaders
        assert len(monitor.downloaders) == 2

    @patch('core.live_stream_monitor.read_config')
    def test_removes_inactive_not_in_config(self, mock_read_config, monitor):
        """Test that inactive downloaders not in config are removed."""
        mock_downloader = MagicMock()
        mock_downloader.is_alive.return_value = False
        monitor.downloaders["old_oid"] = mock_downloader

        mock_read_config.return_value = [
            CreatorProfile(creator_name="NewCreator", creator_oid="new_oid"),
        ]
        monitor._update_downloaders()
        assert "old_oid" not in monitor.downloaders
        assert "new_oid" in monitor.downloaders

    @patch('core.live_stream_monitor.read_config')
    def test_keeps_active_downloader_not_in_config(self, mock_read_config, monitor):
        """Test that active downloaders are kept even if not in config."""
        mock_downloader = MagicMock()
        mock_downloader.is_alive.return_value = True
        monitor.downloaders["active_oid"] = mock_downloader

        mock_read_config.return_value = []
        monitor._update_downloaders()
        assert "active_oid" in monitor.downloaders

    @patch('core.live_stream_monitor.read_config')
    def test_raises_config_error(self, mock_read_config, monitor):
        """Test that ConfigError is re-raised."""
        mock_read_config.side_effect = ConfigError("Config file not found")
        with pytest.raises(ConfigError):
            monitor._update_downloaders()

    @patch('core.live_stream_monitor.read_config')
    def test_updates_monitored_count(self, mock_read_config, monitor):
        """Test that monitored count is updated."""
        mock_read_config.return_value = [
            CreatorProfile(creator_name="C1", creator_oid="o1"),
            CreatorProfile(creator_name="C2", creator_oid="o2"),
            CreatorProfile(creator_name="C3", creator_oid="o3"),
        ]
        monitor._update_downloaders()
        assert monitor._monitored_count == 3


class TestStartDownload:
    """Tests for _start_download method."""

    def test_start_download_success(self, mock_api, monitor):
        """Test successful download start."""
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_downloader = MagicMock()
        mock_downloader.creator_name = "TestCreator"
        mock_stream = MagicMock()
        mock_stream.creator_oid = "test_oid"
        mock_stream.title = "Test Stream"

        monitor._start_download(mock_stream, mock_downloader)

        mock_api.get_stream_url.assert_called_once_with("test_oid")
        mock_downloader.download.assert_called_once_with(
            "http://example.com/stream.m3u8", "Test Stream"
        )

    def test_start_download_auth_error(self, mock_api, monitor):
        """Test auth error is logged."""
        mock_api.get_stream_url.side_effect = RPlayAuthError("Unauthorized")
        mock_downloader = MagicMock()
        mock_downloader.creator_name = "TestCreator"
        mock_stream = MagicMock()
        mock_stream.creator_oid = "test_oid"
        mock_stream.title = "Test Stream"

        monitor._start_download(mock_stream, mock_downloader)
        mock_downloader.download.assert_not_called()

    def test_start_download_api_error(self, mock_api, monitor):
        """Test API error is logged as warning."""
        mock_api.get_stream_url.side_effect = RPlayAPIError("API Error")
        mock_downloader = MagicMock()
        mock_downloader.creator_name = "TestCreator"
        mock_stream = MagicMock()
        mock_stream.creator_oid = "test_oid"
        mock_stream.title = "Test Stream"

        monitor._start_download(mock_stream, mock_downloader)
        mock_downloader.download.assert_not_called()

    def test_start_download_unexpected_error(self, mock_api, monitor):
        """Test unexpected error is caught and logged."""
        mock_api.get_stream_url.side_effect = RuntimeError("Unexpected")
        mock_downloader = MagicMock()
        mock_downloader.creator_name = "TestCreator"
        mock_stream = MagicMock()
        mock_stream.creator_oid = "test_oid"
        mock_stream.title = "Test Stream"

        monitor._start_download(mock_stream, mock_downloader)
        mock_downloader.download.assert_not_called()


class TestCheckLiveStreams:
    """Tests for check_live_streams_and_start_download method."""

    @patch('core.live_stream_monitor.read_config')
    def test_no_live_streams(self, mock_read_config):
        """Test no download when no live streams."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_api.get_livestream_status.return_value = []
        mock_read_config.return_value = []
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
        mock_read_config.return_value = []
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
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator", creator_oid="creator_oid"),
        ]
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor.check_live_streams_and_start_download()
        mock_api.get_stream_url.assert_called_once_with("creator_oid")

    @patch('core.live_stream_monitor.read_config')
    def test_already_downloading_no_restart(self, mock_read_config):
        """Test no restart if already downloading."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator_oid"
        mock_stream.stream_state = StreamState.LIVE
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator", creator_oid="creator_oid"),
        ]
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        # First call to add downloader
        monitor.check_live_streams_and_start_download()
        # Mark as alive
        monitor.downloaders["creator_oid"].download_thread = MagicMock()
        monitor.downloaders["creator_oid"].download_thread.is_alive.return_value = True

        # Second call should not start new download
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
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator", creator_oid="creator_oid"),
        ]
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
        mock_read_config.return_value = []
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
        mock_read_config.return_value = []
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
        mock_read_config.return_value = []
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
        mock_read_config.return_value = []
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
        mock_read_config.return_value = []
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

    def test_empty_list_when_all_inactive(self):
        """Test returns empty list when all downloaders inactive."""
        mock_api = MagicMock(spec=RPlayAPI)
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        mock_downloader = MagicMock()
        mock_downloader.is_alive.return_value = False
        mock_downloader.creator_name = "Inactive"
        monitor.downloaders["oid1"] = mock_downloader

        assert monitor.get_active_downloads() == []

    def test_returns_active_creator_names(self):
        """Test returns list of creator names with active downloads."""
        mock_api = MagicMock(spec=RPlayAPI)
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        # Add active downloader
        active_downloader = MagicMock()
        active_downloader.is_alive.return_value = True
        active_downloader.creator_name = "ActiveCreator"
        monitor.downloaders["active"] = active_downloader

        # Add inactive downloader
        inactive_downloader = MagicMock()
        inactive_downloader.is_alive.return_value = False
        inactive_downloader.creator_name = "InactiveCreator"
        monitor.downloaders["inactive"] = inactive_downloader

        result = monitor.get_active_downloads()
        assert result == ["ActiveCreator"]

    def test_returns_multiple_active(self):
        """Test returns multiple active creator names."""
        mock_api = MagicMock(spec=RPlayAPI)
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        for i in range(3):
            downloader = MagicMock()
            downloader.is_alive.return_value = True
            downloader.creator_name = f"Creator{i}"
            monitor.downloaders[f"oid{i}"] = downloader

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

    def test_is_new_stream_session_no_previous_state(self, mock_api):
        """Test new session detection when no previous state exists."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_start_time = datetime(2026, 1, 26, 12, 0, 0)

        result = monitor._is_new_stream_session(mock_stream)

        assert result is True

    def test_is_new_stream_session_same_start_time(self, mock_api):
        """Test returns False when stream start time unchanged."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        start_time = datetime(2026, 1, 26, 12, 0, 0)
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=start_time
        )
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_start_time = start_time

        result = monitor._is_new_stream_session(mock_stream)

        assert result is False

    def test_is_new_stream_session_different_start_time(self, mock_api):
        """Test returns True when stream start time changed."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        old_time = datetime(2026, 1, 26, 12, 0, 0)
        new_time = datetime(2026, 1, 26, 14, 0, 0)
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=old_time
        )
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_start_time = new_time

        result = monitor._is_new_stream_session(mock_stream)

        assert result is True

    def test_update_creator_state_creates_new_state(self, mock_api):
        """Test that updating state creates new entry if none exists."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        start_time = datetime(2026, 1, 26, 12, 0, 0)
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_start_time = start_time

        monitor._update_creator_state(mock_stream)

        assert "creator1" in monitor._creator_states
        assert monitor._creator_states["creator1"].last_stream_start_time == start_time
        assert monitor._creator_states["creator1"].is_current_stream_blocked is False

    def test_update_creator_state_updates_existing(self, mock_api):
        """Test that updating state modifies existing entry."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        old_time = datetime(2026, 1, 26, 12, 0, 0)
        new_time = datetime(2026, 1, 26, 14, 0, 0)
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=old_time,
            is_current_stream_blocked=True,
        )
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_start_time = new_time

        monitor._update_creator_state(mock_stream)

        assert monitor._creator_states["creator1"].last_stream_start_time == new_time
        assert monitor._creator_states["creator1"].is_current_stream_blocked is False

    def test_clear_creator_state(self, mock_api):
        """Test clearing state for a creator."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
            is_current_stream_blocked=True,
        )

        monitor._clear_creator_state("creator1")

        assert "creator1" not in monitor._creator_states

    def test_clear_creator_state_nonexistent(self, mock_api):
        """Test clearing state for nonexistent creator does not raise."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        # Should not raise
        monitor._clear_creator_state("nonexistent")

    def test_get_or_create_creator_state_creates(self, mock_api):
        """Test get_or_create creates state if not exists."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        state = monitor._get_or_create_creator_state("creator1")

        assert state is not None
        assert state.last_stream_start_time is None
        assert state.is_current_stream_blocked is False
        assert "creator1" in monitor._creator_states

    def test_get_or_create_creator_state_returns_existing(self, mock_api):
        """Test get_or_create returns existing state."""
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        existing_state = CreatorStreamState(
            last_stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
            is_current_stream_blocked=True,
        )
        monitor._creator_states["creator1"] = existing_state

        state = monitor._get_or_create_creator_state("creator1")

        assert state is existing_state


class TestM3u8ValidationIntegration:
    """Tests for M3U8 validation integration in download flow."""

    @patch('core.live_stream_monitor.read_config')
    def test_skips_blocked_stream_same_session(self, mock_read_config, mock_api):
        """Test that blocked streams are skipped in same session."""
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = datetime(2026, 1, 26, 12, 0, 0)
        mock_stream.title = "Test Stream"
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ]
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        # Pre-populate state as blocked
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
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
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ]
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        # Pre-populate state as blocked with OLD start time
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
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
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ]
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
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ]
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
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ]
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        # Pre-populate state
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
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
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ]
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


class TestDownloadErrorCallback:
    """Tests for download error callback integration."""

    @patch('core.live_stream_monitor.read_config')
    def test_downloaders_created_with_error_callback(self, mock_read_config, mock_api):
        """Test that downloaders are created with on_download_error callback."""
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ]
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor._update_downloaders()

        downloader = monitor.downloaders["creator1"]
        assert downloader._on_download_error is not None

    @patch('core.live_stream_monitor.read_config')
    def test_callback_marks_stream_as_blocked(self, mock_read_config, mock_api):
        """Test that error callback marks stream as blocked."""
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ]
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor._update_downloaders()

        # Create state first (normally done in _start_download)
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
        )

        # Invoke the callback
        downloader = monitor.downloaders["creator1"]
        downloader._on_download_error("HTTP Error 404")

        assert monitor._creator_states["creator1"].is_current_stream_blocked is True

    @patch('core.live_stream_monitor.read_config')
    def test_callback_only_marks_blocked_once(self, mock_read_config, mock_api):
        """Test that callback logs warning only on first block."""
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ]
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )
        monitor._update_downloaders()
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
        )

        downloader = monitor.downloaders["creator1"]

        with patch.object(monitor.logger, 'warning') as mock_warning:
            downloader._on_download_error("HTTP Error 404")
            downloader._on_download_error("HTTP Error 404")

        # Warning logged only once (second call sees already blocked)
        assert mock_warning.call_count == 1

    @patch('core.live_stream_monitor.read_config')
    def test_blocked_by_callback_prevents_next_download(self, mock_read_config, mock_api):
        """Test that stream blocked by callback is skipped on next check cycle."""
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        mock_stream.stream_start_time = datetime(2026, 1, 26, 12, 0, 0)
        mock_stream.title = "Test Stream"
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.validate_m3u8_url.return_value = True
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ]
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        # First check: starts download
        monitor.check_live_streams_and_start_download()
        mock_api.get_stream_url.assert_called_once()

        # Simulate download thread finishing
        monitor.downloaders["creator1"].download_thread = MagicMock()
        monitor.downloaders["creator1"].download_thread.is_alive.return_value = False

        # Simulate callback marking blocked (from download thread)
        monitor._creator_states["creator1"].mark_blocked()

        # Second check: should skip because blocked
        mock_api.get_stream_url.reset_mock()
        monitor.check_live_streams_and_start_download()
        mock_api.get_stream_url.assert_not_called()


class TestHeartbeatLogOptimization:
    """Tests for heartbeat log optimization (state-change only logging)."""

    @patch('core.live_stream_monitor.read_config')
    def test_no_status_log_when_state_unchanged(self, mock_read_config, mock_api):
        """Test that status log is not emitted when state is unchanged."""
        mock_api.get_livestream_status.return_value = []
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ]
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
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ]
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
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ]
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
        mock_read_config.return_value = [
            CreatorProfile(creator_name="Creator1", creator_oid="creator1"),
        ]
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
