"""Tests for live stream monitor module."""

import logging
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core import health
from core.config import ConfigError
from core.live_stream_monitor import LiveStreamMonitor
from core.rplay import RPlayAPI, RPlayAPIError, RPlayAuthError, RPlayConnectionError
from models.config import AppConfig, CreatorProfile
from models.download import (
    DownloadSession,
    MergeCompleted,
    RawDownloadAuthFailed,
    RawDownloadBlocked,
    RawDownloadCompleted,
    SessionState,
)
from models.rplay import CreatorStreamState, StreamState

_GIB = 1024**3


def _runtime_config(creators):
    """Build AppConfig test data with the default hot-reload API base URL."""
    return AppConfig(api_base_url="https://api.rplay.live", creators=creators)


@pytest.fixture(autouse=True)
def plenty_of_free_disk(monkeypatch):
    """Keep this module independent of real free disk (default guard is 5 GiB)."""
    usage = SimpleNamespace(total=100 * _GIB, used=10 * _GIB, free=90 * _GIB)
    monkeypatch.setattr("shutil.disk_usage", lambda path: usage)


@pytest.fixture
def mock_api():
    """Create a mock RPlayAPI."""
    return MagicMock(spec=RPlayAPI)


@pytest.fixture
def monitor(mock_api):
    """Create a LiveStreamMonitor with mock API."""
    return LiveStreamMonitor(api_client=mock_api)


@pytest.fixture
def mock_read_config():
    """Patch config loading for the monitor."""
    with patch("core.live_stream_monitor.read_config") as mock:
        yield mock


@pytest.fixture
def mock_download():
    """Patch the real yt-dlp download so no thread hits the network."""
    with patch("core.live_stream_monitor.StreamDownloader.download") as mock:
        yield mock


def _creator1_config():
    """Runtime config that monitors only Creator1."""
    return _runtime_config(
        [CreatorProfile(creator_name="Creator1", creator_oid="creator1")]
    )


def _register_creator(monitor, creator_oid="creator1", creator_name="Creator1"):
    """Add a monitored creator profile."""
    monitor.monitored_creators[creator_oid] = CreatorProfile(
        creator_name=creator_name, creator_oid=creator_oid
    )


def _live_stream(
    creator_oid="creator1",
    *,
    oid=None,
    start=datetime(2026, 3, 7, 5, 3, 40),
    title="Test Stream",
    state=StreamState.LIVE,
):
    """Build a mock live stream. The oid is only set when given."""
    stream = MagicMock()
    if oid is not None:
        stream.oid = oid
    stream.creator_oid = creator_oid
    stream.stream_state = state
    stream.stream_start_time = start
    stream.title = title
    return stream


def _start_stream(creator_oid="test_oid", title="Test Stream"):
    """Build a mock stream for direct _start_download calls."""
    stream = MagicMock()
    stream.creator_oid = creator_oid
    stream.title = title
    stream.stream_start_time = datetime(2026, 3, 6, 12, 0, 0)
    return stream


def _seed_session(
    monitor,
    session_key,
    *,
    state,
    output_dir,
    creator_oid="creator1",
    creator_name="Creator1",
    title="Test Stream",
    start=datetime(2026, 3, 6, 12, 0, 0),
    prefix=None,
    **extra,
):
    """Register a DownloadSession. The prefix defaults to the start time."""
    monitor.sessions[session_key] = DownloadSession(
        session_key=session_key,
        creator_oid=creator_oid,
        creator_name=creator_name,
        title=title,
        stream_start_time=start,
        state=state,
        output_dir=output_dir,
        session_prefix=prefix or start.strftime("%Y%m%d_%H%M%S_"),
        **extra,
    )


def _clear_sessions(monitor):
    """Drop session state so the start path runs again."""
    monitor.sessions.clear()
    monitor._active_raw_session_by_creator.clear()


class TestLiveStreamMonitorInit:
    """Tests for LiveStreamMonitor initialization."""

    def test_init_uses_injected_api(self, mock_api):
        """Test that injected API is used instead of creating new one."""
        monitor = LiveStreamMonitor(api_client=mock_api)
        assert monitor.api_client is mock_api

    def test_init_empty_monitored_creators(self, monitor):
        """Test that monitored creators dict is empty on init."""
        assert monitor.monitored_creators == {}
        assert monitor._creator_states == {}

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"config_path": "/custom/path.yaml"}, "/custom/path.yaml"),
            ({}, "./config/config.yaml"),
        ],
        ids=["custom", "default"],
    )
    def test_init_sets_config_path(self, mock_api, kwargs, expected):
        """Test that config path is set from the argument or defaults."""
        monitor = LiveStreamMonitor(api_client=mock_api, **kwargs)
        assert monitor.config_path == expected


class TestSessionAwareMonitoring:
    """Tests for session-aware monitor behavior."""

    def test_same_stream_oid_does_not_restart_download_when_start_time_changes(
        self, mock_download, mock_read_config, mock_api, monitor
    ):
        """Test the same live stream oid is only downloaded once across polls."""
        first_stream = _live_stream(
            oid="stream-1", start=datetime(2026, 3, 7, 5, 3, 40), title="Same Stream"
        )
        second_stream = _live_stream(
            oid="stream-1", start=datetime(2026, 3, 7, 5, 4, 40), title="Same Stream"
        )

        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.get_livestream_status.side_effect = [[first_stream], [second_stream]]
        mock_read_config.return_value = _creator1_config()

        monitor.check_live_streams_and_start_download()
        monitor.check_live_streams_and_start_download()

        mock_download.assert_called_once_with(
            "http://example.com/stream.m3u8", "Same Stream"
        )

    def test_same_live_creator_starts_second_segment_after_raw_completion(
        self, mock_download, mock_read_config, mock_api, monitor, tmp_path
    ):
        """Test raw completion immediately allows the same live creator to start a new segment."""
        first_stream = _live_stream(oid="stream-1", title="Repeated Title")
        second_stream = _live_stream(oid="stream-1", title="Repeated Title")

        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.get_livestream_status.side_effect = [[first_stream], [second_stream]]
        mock_read_config.return_value = _creator1_config()

        monitor.check_live_streams_and_start_download()
        first_session_key = next(iter(monitor.sessions))
        monitor._handle_monitor_event(
            RawDownloadCompleted(
                session_key=first_session_key,
                output_dir=tmp_path / "first-staging",
            )
        )
        monitor.check_live_streams_and_start_download()

        assert mock_download.call_count == 2

    def test_done_session_does_not_block_new_segment_for_same_live_creator(
        self, mock_download, mock_read_config, mock_api, monitor
    ):
        """Test a finished session never blocks a fresh segment for the same creator."""
        start = datetime(2026, 3, 5, 10, 47, 43)
        first_stream = _live_stream(oid="stream-1", start=start, title="Repeated Title")
        second_stream = _live_stream(
            oid="stream-1", start=start, title="Repeated Title"
        )

        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_api.get_livestream_status.side_effect = [[first_stream], [second_stream]]
        mock_read_config.return_value = _creator1_config()

        monitor.check_live_streams_and_start_download()

        first_session_key = next(iter(monitor.sessions))
        monitor.sessions[first_session_key].state = SessionState.DONE

        monitor.check_live_streams_and_start_download()

        assert mock_download.call_count == 2

    def test_marks_creator_raw_running_before_get_stream_url(
        self, mock_download, mock_read_config, mock_api, monitor
    ):
        """Test creator raw-running state is visible before stream URL fetching starts."""
        mock_stream = _live_stream(oid="stream-1", title="Same Stream")

        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_read_config.return_value = _creator1_config()

        def assert_eager_state(creator_oid: str, stream_key: str) -> str:
            active_session_key = monitor._active_raw_session_by_creator[creator_oid]
            assert active_session_key in monitor.sessions
            active_session = monitor.sessions[active_session_key]
            assert active_session.state == SessionState.RAW_RUNNING
            assert active_session.recording_started_at == fixed_now
            return "http://example.com/stream.m3u8"

        fixed_now = datetime(2026, 3, 7, 5, 3, 41)
        mock_api._get_stream_key.return_value = "cycle-key"
        mock_api.get_stream_url.side_effect = assert_eager_state

        with patch("core.live_stream_monitor.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            monitor.check_live_streams_and_start_download()

        mock_download.assert_called_once_with(
            "http://example.com/stream.m3u8", "Same Stream"
        )

    def test_new_session_starts_while_old_session_is_merging(
        self, mock_download, mock_read_config, mock_api, monitor, tmp_path
    ):
        """Test a new session can start while an older session is merging."""
        old_time = datetime(2026, 3, 6, 12, 0, 0)
        new_time = datetime(2026, 3, 6, 12, 5, 0)
        mock_stream = _live_stream(start=new_time)

        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_read_config.return_value = _creator1_config()
        _seed_session(
            monitor,
            "creator1:2026-03-06T12:00:00",
            title="Old Stream",
            start=old_time,
            state=SessionState.MERGING,
            output_dir=tmp_path / "old",
        )

        monitor.check_live_streams_and_start_download()

        mock_download.assert_called_once_with(
            "http://example.com/stream.m3u8", "Test Stream"
        )

    def test_process_live_stream_prunes_superseded_terminal_sessions(
        self, monitor, tmp_path
    ):
        """Test a newer session prunes older terminal sessions for the same creator."""
        old_session_key = "creator1:1772798400"
        _register_creator(monitor)
        _seed_session(
            monitor,
            old_session_key,
            title="Old Stream",
            state=SessionState.DONE,
            output_dir=tmp_path / "old",
        )
        mock_stream = _live_stream(
            oid="stream-2", start=datetime(2026, 3, 6, 12, 5, 0), title="New Stream"
        )

        with patch.object(monitor, "_start_download") as mock_start_download:
            monitor._process_live_stream(mock_stream)

        assert old_session_key not in monitor.sessions
        assert monitor.latest_stream_oid_by_creator["creator1"] == "stream-2"
        mock_start_download.assert_called_once_with(mock_stream)

    def test_cleanup_offline_creator_states_prunes_terminal_sessions(
        self, monitor, tmp_path
    ):
        """Test offline creators drop terminal sessions during cleanup."""
        offline_session_key = "creator1:2026-03-06T12:00:00"
        active_session_key = "creator2:2026-03-06T12:05:00"
        _seed_session(
            monitor,
            offline_session_key,
            title="Finished Stream",
            state=SessionState.DONE,
            output_dir=tmp_path / "creator1",
        )
        _seed_session(
            monitor,
            active_session_key,
            creator_oid="creator2",
            creator_name="Creator2",
            title="Active Stream",
            start=datetime(2026, 3, 6, 12, 5, 0),
            state=SessionState.RAW_RUNNING,
            output_dir=tmp_path / "creator2",
        )
        monitor._creator_states["creator1"] = CreatorStreamState()
        monitor._creator_states["creator2"] = CreatorStreamState()

        monitor._cleanup_offline_creator_states({"creator2"})

        assert offline_session_key not in monitor.sessions
        assert active_session_key in monitor.sessions

    def test_same_session_not_started_twice(
        self, mock_download, mock_read_config, mock_api, monitor, tmp_path
    ):
        """Test the same session is skipped when already running."""
        mock_stream = _live_stream(oid="stream-1", start=datetime(2026, 3, 6, 12, 0, 0))

        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_read_config.return_value = _creator1_config()
        _seed_session(
            monitor,
            "creator1:1772798400",
            state=SessionState.RAW_RUNNING,
            output_dir=tmp_path / "running",
        )
        monitor._active_raw_session_by_creator["creator1"] = "creator1:1772798400"

        monitor.check_live_streams_and_start_download()

        mock_api.get_stream_url.assert_not_called()
        mock_download.assert_not_called()

    def test_raw_completion_queues_merge(self, monitor, tmp_path):
        """Test raw completion marks the session queued and submits merge work."""
        session_key = "creator1:2026-03-06T12:00:00"
        _seed_session(
            monitor,
            session_key,
            state=SessionState.RAW_RUNNING,
            output_dir=tmp_path,
        )
        result = RawDownloadCompleted(
            session_key=session_key,
            output_dir=tmp_path,
        )

        with patch.object(monitor.merge_executor, "submit_merge") as mock_submit:
            monitor._on_raw_download_complete(result)
            monitor._event_queue.join()

        assert monitor.sessions[session_key].state == SessionState.MERGE_QUEUED
        mock_submit.assert_called_once()


class TestUpdateDownloaders:
    """Tests for _update_downloaders method."""

    def test_updates_api_base_url_from_config(
        self, mock_read_config, monitor, mock_api
    ):
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

    def test_adds_new_creators(self, mock_read_config, monitor):
        """Test that monitored creators are refreshed from config."""
        mock_read_config.return_value = _runtime_config(
            [
                CreatorProfile(creator_name="Creator1", creator_oid="oid1"),
                CreatorProfile(creator_name="Creator2", creator_oid="oid2"),
                CreatorProfile(creator_name="Creator3", creator_oid="oid3"),
            ]
        )
        monitor._update_downloaders()
        assert "oid1" in monitor.monitored_creators
        assert "oid2" in monitor.monitored_creators
        assert "oid3" in monitor.monitored_creators
        assert len(monitor.monitored_creators) == 3
        assert monitor._monitored_count == 3

    @patch("core.live_stream_monitor.StreamDownloader")
    def test_does_not_create_template_stream_downloaders(
        self, mock_stream_downloader, mock_read_config, monitor
    ):
        """Test creator refresh does not allocate unused template downloaders."""
        mock_read_config.return_value = _runtime_config(
            [
                CreatorProfile(creator_name="Creator1", creator_oid="oid1"),
            ]
        )

        monitor._update_downloaders()

        mock_stream_downloader.assert_not_called()

    def test_removes_inactive_not_in_config(self, mock_read_config, monitor):
        """Test that monitored creators not in config are removed."""
        _register_creator(monitor, "old_oid", "OldCreator")

        mock_read_config.return_value = _runtime_config(
            [
                CreatorProfile(creator_name="NewCreator", creator_oid="new_oid"),
            ]
        )
        monitor._update_downloaders()
        assert "old_oid" not in monitor.monitored_creators
        assert "new_oid" in monitor.monitored_creators

    def test_logs_added_and_removed_creator_names(self, mock_read_config, monitor):
        """Test config refresh logs concise added/removed creator summaries."""
        _register_creator(monitor, "old_oid", "OldCreator")
        mock_read_config.return_value = _runtime_config(
            [
                CreatorProfile(creator_name="NewCreator1", creator_oid="new_oid_1"),
                CreatorProfile(creator_name="NewCreator2", creator_oid="new_oid_2"),
            ]
        )

        with patch.object(monitor.logger, "info") as mock_info:
            monitor._update_downloaders()

        assert any(
            "Added 2 new creator(s) to monitor: NewCreator1, NewCreator2" in str(call)
            for call in mock_info.call_args_list
        )
        assert any(
            "Removed 1 creator(s) from monitor: OldCreator" in str(call)
            for call in mock_info.call_args_list
        )

    def test_active_session_does_not_require_monitored_creator_entry(
        self, mock_read_config, monitor, tmp_path
    ):
        """Test active sessions keep their own metadata even after config removal."""
        _seed_session(
            monitor,
            "active_oid:2026-03-06T12:00:00",
            creator_oid="active_oid",
            creator_name="ActiveCreator",
            state=SessionState.RAW_RUNNING,
            output_dir=tmp_path,
        )

        mock_read_config.return_value = _runtime_config([])
        monitor._update_downloaders()
        assert monitor.monitored_creators == {}
        assert "active_oid:2026-03-06T12:00:00" in monitor.sessions

    def test_raises_config_error(self, mock_read_config, monitor):
        """Test that ConfigError is re-raised."""
        mock_read_config.side_effect = ConfigError("Config file not found")
        with pytest.raises(ConfigError):
            monitor._update_downloaders()


class TestStartDownload:
    """Tests for _start_download method."""

    def test_start_download_logs_live_line_with_creator_prefix(
        self, mock_api, monitor, mock_download, caplog
    ):
        """Test the first live log carries the creator-prefixed context line."""
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_stream = _start_stream()
        _register_creator(monitor, "test_oid", "TestCreator")

        monitor._start_download(mock_stream)

        messages = [record.getMessage() for record in caplog.records]
        assert '[TestCreator] 🔴 Live: "Test Stream"' in messages

    def test_start_download_auth_error(self, mock_api, monitor, mock_download, caplog):
        """First auth error is ERROR; a repeat is DEBUG-only; failed key is not cached."""
        mock_api._get_stream_key.side_effect = RPlayAuthError("Unauthorized")
        mock_stream = _start_stream()
        _register_creator(monitor, "test_oid", "TestCreator")

        with caplog.at_level(logging.DEBUG, logger="Monitor"):
            monitor._start_download(mock_stream)
            # Second attempt: clear session state so the start path runs again.
            _clear_sessions(monitor)
            monitor._start_download(mock_stream)

        mock_download.assert_not_called()
        mock_api.get_stream_url.assert_not_called()
        # Failure is not cached: each start retries the real key2 fetch.
        assert mock_api._get_stream_key.call_count == 2
        error_auth = [
            r
            for r in caplog.records
            if r.levelno >= logging.ERROR and "Auth error for" in r.getMessage()
        ]
        debug_auth = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "Auth error for" in r.getMessage()
        ]
        assert len(error_auth) == 1
        assert len(debug_auth) >= 1
        assert monitor._cycle_stream_key is None
        assert monitor._cycle_key_fetch_auth_failed is True

    @pytest.mark.parametrize(
        "error",
        [RPlayAPIError("API Error"), RuntimeError("Unexpected")],
        ids=["api_error", "unexpected_error"],
    )
    def test_start_download_api_error(self, mock_api, monitor, mock_download, error):
        """Test API and unexpected errors are caught without starting a download."""
        mock_api._get_stream_key.side_effect = error
        mock_stream = MagicMock()
        mock_stream.creator_oid = "test_oid"
        mock_stream.title = "Test Stream"
        _register_creator(monitor, "test_oid", "TestCreator")

        monitor._start_download(mock_stream)

        mock_download.assert_not_called()
        assert monitor._cycle_stream_key is None


class TestCycleStreamKeyCache:
    @staticmethod
    def _live_stream(creator_oid: str, title: str = "Live"):
        return _live_stream(creator_oid, oid=f"stream-{creator_oid}", title=title)

    def test_two_creators_share_one_key2_fetch_and_reset_next_cycle(
        self, mock_download, mock_read_config, mock_api, monitor
    ):
        streams = [self._live_stream("c1"), self._live_stream("c2")]
        mock_api.get_livestream_status.return_value = streams
        mock_api._get_stream_key.return_value = "shared-key"
        mock_api.get_stream_url.side_effect = (
            lambda oid, stream_key: f"http://example.com/{oid}.m3u8"
        )
        mock_read_config.return_value = _runtime_config(
            [
                CreatorProfile(creator_name="C1", creator_oid="c1"),
                CreatorProfile(creator_name="C2", creator_oid="c2"),
            ]
        )

        monitor.check_live_streams_and_start_download()
        assert mock_api._get_stream_key.call_count == 1
        assert mock_api.get_stream_url.call_count == 2
        for call, oid in zip(mock_api.get_stream_url.call_args_list, ("c1", "c2")):
            assert call.args == (oid,)
            assert call.kwargs == {"stream_key": "shared-key"}
        assert mock_download.call_count == 2

        # Next cycle: clear sessions so downloads start again; cache must reset.
        monitor.sessions.clear()
        monitor._active_raw_session_by_creator.clear()
        monitor._active_downloaders.clear()
        monitor.check_live_streams_and_start_download()
        assert mock_api._get_stream_key.call_count == 2

    def test_cache_hit_does_not_rearm_auth_dedup(
        self, mock_api, monitor, mock_download
    ):
        mock_api._get_stream_key.return_value = "shared-key"
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        _register_creator(monitor, "c1", "C1")
        _register_creator(monitor, "c2", "C2")
        monitor._auth_error_notified = True

        monitor._start_download(self._live_stream("c1"))
        assert monitor._auth_error_notified is False
        monitor._auth_error_notified = True  # simulate a later notify
        monitor._start_download(self._live_stream("c2"))

        assert monitor._auth_error_notified is True
        assert mock_api._get_stream_key.call_count == 1


class TestMinFreeDiskGuard:
    """Tests for the minimum free-disk guard before session creation."""

    def _stream_and_profile(self, monitor):
        _register_creator(monitor, "test_oid", "TestCreator")
        return _start_stream()

    def test_below_threshold_blocks_session_and_logs_error(
        self, mock_api, monitor, mock_download, caplog
    ):
        """Below-threshold free space skips session creation and logs one error."""
        monitor.min_free_disk_gb = 5
        mock_stream = self._stream_and_profile(monitor)
        free_bytes = 2 * _GIB
        usage = MagicMock(free=free_bytes, total=100 * _GIB, used=98 * _GIB)

        with (
            patch(
                "core.live_stream_monitor.shutil.disk_usage", return_value=usage
            ) as mock_usage,
            caplog.at_level(logging.ERROR, logger="Monitor"),
        ):
            monitor._start_download(mock_stream)

        mock_usage.assert_called_once()
        mock_download.assert_not_called()
        mock_api.get_stream_url.assert_not_called()
        assert monitor.sessions == {}
        error_msgs = [
            r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.ERROR
            and "Insufficient free disk space" in r.getMessage()
        ]
        assert len(error_msgs) == 1
        assert "path=" in error_msgs[0]
        assert f"({free_bytes} bytes)" in error_msgs[0]
        assert "required=5" in error_msgs[0]

    def test_zero_disables_guard(self, mock_api, monitor, mock_download):
        """MIN_FREE_DISK_GB=0 skips disk_usage and still starts the session."""
        monitor.min_free_disk_gb = 0
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_stream = self._stream_and_profile(monitor)

        with patch("core.live_stream_monitor.shutil.disk_usage") as mock_usage:
            monitor._start_download(mock_stream)

        mock_usage.assert_not_called()
        mock_download.assert_called_once()
        assert len(monitor.sessions) == 1

    def test_disk_usage_oserror_allows_session_with_warning(
        self, mock_api, monitor, mock_download, caplog
    ):
        """OSError from disk_usage logs a warning and allows the session."""
        monitor.min_free_disk_gb = 5
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_stream = self._stream_and_profile(monitor)

        with (
            patch(
                "core.live_stream_monitor.shutil.disk_usage",
                side_effect=OSError("statvfs failed"),
            ),
            caplog.at_level(logging.WARNING, logger="Monitor"),
        ):
            monitor._start_download(mock_stream)

        mock_download.assert_called_once()
        assert len(monitor.sessions) == 1
        warnings = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "Could not check free disk space" in r.getMessage()
        ]
        assert len(warnings) == 1


class TestCheckLiveStreams:
    """Tests for check_live_streams_and_start_download method."""

    def test_live_but_not_monitored(self, mock_read_config, mock_api, monitor):
        """Test no download when creator is live but not monitored."""
        mock_api.get_livestream_status.return_value = [_live_stream("unknown_oid")]
        mock_read_config.return_value = _runtime_config([])
        monitor.check_live_streams_and_start_download()
        mock_api.get_stream_url.assert_not_called()

    def test_live_and_monitored_starts_download(
        self, mock_read_config, mock_api, monitor, mock_download
    ):
        """Test download starts when monitored creator is live."""
        mock_api.get_livestream_status.return_value = [
            _live_stream("creator_oid", title="Live Stream")
        ]
        mock_api._get_stream_key.return_value = "cycle-key"
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_read_config.return_value = _runtime_config(
            [
                CreatorProfile(creator_name="Creator", creator_oid="creator_oid"),
            ]
        )
        # Without the mock_download fixture the monitor spawns a real yt-dlp
        # thread against example.com. Its failure callback lands after this
        # test returns and logs through the process-wide "Monitor" logger,
        # which is how it used to pollute whichever test happened to be
        # patching that logger.
        monitor.check_live_streams_and_start_download()
        mock_download.assert_called_once_with(
            "http://example.com/stream.m3u8", "Live Stream"
        )

    def test_stream_not_live_state(self, mock_read_config, mock_api, monitor):
        """Test no download when stream state is not LIVE."""
        mock_api.get_livestream_status.return_value = [
            _live_stream("creator_oid", state=StreamState.TWITCH)
        ]
        mock_read_config.return_value = _runtime_config(
            [
                CreatorProfile(creator_name="Creator", creator_oid="creator_oid"),
            ]
        )
        monitor.check_live_streams_and_start_download()
        mock_api.get_stream_url.assert_not_called()


class TestCheckLiveStreamsErrorHandling:
    """Tests for error handling in check_live_streams_and_start_download."""

    def test_config_error_sets_unhealthy(self, mock_read_config, monitor):
        """Test ConfigError sets healthy=False."""
        mock_read_config.side_effect = ConfigError("Config error")
        monitor.check_live_streams_and_start_download()
        assert monitor.is_healthy is False

    def test_all_key2_auth_failures_mark_cycle_unhealthy(
        self, mock_download, mock_read_config, mock_api, monitor
    ):
        stream = _live_stream("c1", oid="stream-c1", title="Live")
        mock_api.get_livestream_status.return_value = [stream]
        mock_api._get_stream_key.side_effect = RPlayAuthError("Unauthorized")
        mock_read_config.return_value = _runtime_config(
            [
                CreatorProfile(creator_name="C1", creator_oid="c1"),
            ]
        )

        monitor.check_live_streams_and_start_download()

        mock_download.assert_not_called()
        assert monitor.is_healthy is False

    def test_key2_auth_failure_then_success_marks_cycle_healthy(
        self, mock_download, mock_read_config, mock_api, monitor
    ):
        streams = [
            _live_stream(c, oid=f"stream-{c}", title="Live") for c in ("c1", "c2")
        ]
        mock_api.get_livestream_status.return_value = streams
        mock_api._get_stream_key.side_effect = [
            RPlayAuthError("Unauthorized"),
            "recovered-key",
        ]
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_read_config.return_value = _runtime_config(
            [
                CreatorProfile(creator_name="C1", creator_oid="c1"),
                CreatorProfile(creator_name="C2", creator_oid="c2"),
            ]
        )

        monitor.check_live_streams_and_start_download()

        assert mock_api._get_stream_key.call_count == 2
        mock_download.assert_called_once()
        assert monitor.is_healthy is True

    @pytest.mark.parametrize(
        "error",
        [
            RPlayAuthError("Unauthorized"),
            RPlayConnectionError("Network error"),
            RPlayAPIError("API error"),
            RuntimeError("Unexpected"),
        ],
        ids=["auth", "connection", "api", "unexpected"],
    )
    def test_poll_error_sets_unhealthy(
        self, mock_read_config, mock_api, monitor, error
    ):
        """Test poll-time errors set healthy=False."""
        mock_read_config.return_value = _runtime_config([])
        mock_api.get_livestream_status.side_effect = error
        monitor.check_live_streams_and_start_download()
        assert monitor.is_healthy is False

    def test_success_restores_healthy(self, mock_read_config, mock_api, monitor):
        """Test successful check restores healthy=True after failure."""
        mock_read_config.return_value = _runtime_config([])
        mock_api.get_livestream_status.return_value = []
        # Force unhealthy state
        monitor._last_check_success = False
        assert monitor.is_healthy is False

        # Successful check should restore health
        monitor.check_live_streams_and_start_download()
        assert monitor.is_healthy is True

    def test_config_error_logged_once(self, mock_read_config, monitor):
        """Test config failures are logged once at the poll-cycle boundary."""
        mock_read_config.side_effect = ConfigError("Config file not found")

        with patch.object(monitor.logger, "warning") as mock_warning:
            monitor.check_live_streams_and_start_download()

        assert mock_warning.call_count == 1
        assert (
            mock_warning.call_args.args[0] == "Skipping check due to config file error"
        )

    def test_unexpected_update_error_logged_once(self, mock_read_config, monitor):
        """Test unexpected update failures are logged once at the poll-cycle boundary."""
        mock_read_config.side_effect = RuntimeError("boom")

        with patch.object(monitor.logger, "exception") as mock_exception:
            monitor.check_live_streams_and_start_download()

        assert mock_exception.call_count == 1
        assert (
            mock_exception.call_args.args[0]
            == "Unexpected error during monitoring: boom"
        )


class TestAuthErrorDedup:
    """Tests for auth-error log deduplication across poll failures."""

    def test_repeat_auth_failures_log_error_once(
        self, mock_read_config, mock_api, monitor, caplog
    ):
        """Test repeated auth failures log ERROR once; repeats are DEBUG."""
        mock_read_config.return_value = _runtime_config([])
        mock_api.get_livestream_status.side_effect = RPlayAuthError("Unauthorized")

        with caplog.at_level(logging.DEBUG, logger="Monitor"):
            monitor.check_live_streams_and_start_download()
            monitor.check_live_streams_and_start_download()

        error_auth = [
            r
            for r in caplog.records
            if r.levelno >= logging.ERROR and "Authentication error" in r.getMessage()
        ]
        debug_auth = [
            r
            for r in caplog.records
            if r.levelno == logging.DEBUG and "Authentication error" in r.getMessage()
        ]
        assert len(error_auth) == 1
        assert len(debug_auth) >= 1

    def test_successful_key2_rearms_auth_error_logging(
        self, mock_api, monitor, mock_download, caplog
    ):
        """Test a successful key2 fetch re-arms the next auth-failure ERROR log."""
        mock_stream = _start_stream()
        _register_creator(monitor, "test_oid", "TestCreator")
        mock_api._get_stream_key.side_effect = [
            RPlayAuthError("Unauthorized"),
            "cycle-key",
            RPlayAuthError("Unauthorized again"),
        ]
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"

        with caplog.at_level(logging.DEBUG, logger="Monitor"):
            monitor._start_download(mock_stream)  # auth fail → ERROR
            _clear_sessions(monitor)
            monitor._start_download(mock_stream)  # key2 success → re-arm
            _clear_sessions(monitor)
            # New cycle (or explicit clear): cache must not hide the next real fetch.
            monitor._cycle_stream_key = None
            monitor._start_download(mock_stream)  # auth fail → ERROR again

        error_auth = [
            r
            for r in caplog.records
            if r.levelno >= logging.ERROR and "Auth error for" in r.getMessage()
        ]
        assert len(error_auth) == 2
        assert monitor._auth_error_notified is True


class TestCreatorStateTracking:
    """Tests for creator stream state tracking functionality."""

    def test_update_creator_stream_state_creates_new_state(self, monitor):
        """Test that updating state creates new entry if none exists."""
        mock_stream = MagicMock()
        mock_stream.oid = "stream-1"
        mock_stream.creator_oid = "creator1"

        monitor._update_creator_stream_state(mock_stream)

        assert "creator1" in monitor._creator_states
        assert monitor._creator_states["creator1"].is_current_stream_blocked is False

    def test_update_creator_stream_state_updates_existing(self, monitor):
        """Test that updating state modifies existing entry."""
        monitor._creator_states["creator1"] = CreatorStreamState(
            is_current_stream_blocked=True,
        )
        mock_stream = MagicMock()
        mock_stream.oid = "stream-2"
        mock_stream.creator_oid = "creator1"

        monitor._update_creator_stream_state(mock_stream)

        assert monitor._creator_states["creator1"].is_current_stream_blocked is False

    def test_clear_creator_stream_state(self, monitor):
        """Test clearing state for a creator."""
        monitor._creator_states["creator1"] = CreatorStreamState(
            is_current_stream_blocked=True,
        )

        monitor._clear_creator_stream_state("creator1")

        assert "creator1" not in monitor._creator_states

    def test_clear_creator_stream_state_nonexistent(self, monitor):
        """Test clearing state for nonexistent creator does not raise."""

        # Should not raise
        monitor._clear_creator_stream_state("nonexistent")

    def test_clear_creator_stream_state_logs_offline_cleanup(self, monitor, tmp_path):
        """Test offline cleanup logs what state was released for the creator."""
        _register_creator(monitor)
        monitor._creator_states["creator1"] = CreatorStreamState(
            is_current_stream_blocked=True,
        )
        _seed_session(
            monitor,
            "creator1:session",
            state=SessionState.DONE,
            output_dir=tmp_path,
            start=datetime(2026, 1, 26, 12, 0, 0),
        )

        with patch.object(monitor.logger, "info") as mock_info:
            monitor._clear_creator_stream_state("creator1")

        assert any(
            "Cleared creator state for Creator1" in str(call)
            and "blocked=True" in str(call)
            and "pruned_terminal_sessions=1" in str(call)
            for call in mock_info.call_args_list
        )

    def test_handle_raw_download_blocked_creates_state_if_missing(
        self, monitor, tmp_path
    ):
        """Test blocked raw downloads create creator state when absent."""
        _seed_session(
            monitor,
            "creator1:2026-01-26T12:00:00",
            state=SessionState.RAW_RUNNING,
            output_dir=tmp_path,
            start=datetime(2026, 1, 26, 12, 0, 0),
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

    @pytest.mark.parametrize(
        "stream_attrs",
        [
            {
                "oid": "stream-1",
                "stream_start_time": datetime(2026, 1, 26, 12, 0, 0),
                "title": "Test Stream",
            },
            {
                "stream_start_time": datetime(2026, 1, 26, 14, 0, 0),
                "title": "New Stream",
            },
        ],
        ids=["same_session", "changed_metadata"],
    )
    def test_blocked_stream_stays_suppressed_while_creator_remains_live(
        self, mock_read_config, mock_api, monitor, stream_attrs
    ):
        """Test blocked creators are not retried mid-live even if stream metadata changes."""
        mock_stream = MagicMock()
        mock_stream.creator_oid = "creator1"
        mock_stream.stream_state = StreamState.LIVE
        for name, value in stream_attrs.items():
            setattr(mock_stream, name, value)
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_read_config.return_value = _creator1_config()
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
            is_current_stream_blocked=True,
        )

        monitor.check_live_streams_and_start_download()

        mock_api.get_stream_url.assert_not_called()

    def test_clears_state_when_creator_not_in_list(
        self, mock_read_config, mock_api, monitor
    ):
        """Test that creator state is cleared when not in live list."""
        mock_api.get_livestream_status.return_value = []  # No live streams
        mock_read_config.return_value = _creator1_config()
        # Pre-populate state
        monitor._creator_states["creator1"] = CreatorStreamState(
            is_current_stream_blocked=True,
        )

        monitor.check_live_streams_and_start_download()

        # State should be cleared
        assert "creator1" not in monitor._creator_states


class TestSessionDownloadBlockedHandling:
    """Tests for session-scoped blocked download handling."""

    def test_blocked_event_logs_warning_only_once(self, monitor, tmp_path):
        """Test repeated blocked events log only once for the same creator state."""
        _seed_session(
            monitor,
            "creator1:2026-01-26T12:00:00",
            state=SessionState.RAW_RUNNING,
            output_dir=tmp_path,
            start=datetime(2026, 1, 26, 12, 0, 0),
        )
        monitor._creator_states["creator1"] = CreatorStreamState()

        with patch.object(monitor.logger, "warning") as mock_warning:
            blocked_event = RawDownloadBlocked(
                session_key="creator1:2026-01-26T12:00:00",
                error_message="HTTP Error 404",
            )
            monitor._handle_raw_download_blocked(blocked_event)
            monitor._handle_raw_download_blocked(blocked_event)

        assert mock_warning.call_count == 1

    def test_blocked_session_prevents_next_download(
        self, mock_read_config, mock_api, monitor, tmp_path
    ):
        """Test blocked session state prevents another download in the same session."""
        mock_stream = _live_stream(
            oid="stream-1", start=datetime(2026, 1, 26, 12, 0, 0)
        )
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_read_config.return_value = _creator1_config()
        _seed_session(
            monitor,
            "creator1:1769428800",
            state=SessionState.BLOCKED,
            output_dir=tmp_path,
            start=datetime(2026, 1, 26, 12, 0, 0),
        )
        monitor._creator_states["creator1"] = CreatorStreamState(
            last_stream_start_time=datetime(2026, 1, 26, 12, 0, 0),
            is_current_stream_blocked=True,
        )

        mock_api.get_stream_url.reset_mock()
        monitor.check_live_streams_and_start_download()
        mock_api.get_stream_url.assert_not_called()


class TestPlaylistHttpAuthRouting:
    """Pin playlist HTTP status → auth vs blocked event path and health impact.

    Binding domain facts (playlist fetch):
    - 401 → AUTH (invalid/expired key2): auth-failure path, marks unhealthy
    - 403 / 404 → BLOCKED (paid/restricted): blocked path, does not touch health
    Downloader worker callback routing for these statuses is covered in
    test_downloader.py; these tests pin the monitor-side outcomes.
    """

    SESSION_KEY = "creator1:2026-01-26T12:00:00"

    def _monitor_with_raw_session(self, monitor, tmp_path):
        _seed_session(
            monitor,
            self.SESSION_KEY,
            state=SessionState.RAW_RUNNING,
            output_dir=tmp_path,
            start=datetime(2026, 1, 26, 12, 0, 0),
        )
        monitor._creator_states["creator1"] = CreatorStreamState()
        return monitor

    def test_playlist_401_auth_failed_marks_unhealthy(self, monitor, tmp_path):
        """Playlist 401 surfaces as auth failure and marks health unhealthy."""
        monitor = self._monitor_with_raw_session(monitor, tmp_path)
        assert monitor.is_healthy is True

        monitor._handle_raw_download_auth_failed(
            RawDownloadAuthFailed(
                session_key=self.SESSION_KEY,
                error_message="HTTP Error 401: Unauthorized",
            )
        )

        assert self.SESSION_KEY not in monitor.sessions
        assert monitor.is_healthy is False
        assert monitor._creator_states["creator1"].is_current_stream_blocked is False

    @pytest.mark.parametrize(
        "error_message",
        ["HTTP Error 403: Forbidden", "HTTP Error 404: Not Found"],
        ids=["403", "404"],
    )
    def test_playlist_404_blocked_does_not_touch_health(
        self, monitor, tmp_path, error_message
    ):
        """Playlist 403/404 (paid content with valid key2) is blocked, not unhealthy."""
        monitor = self._monitor_with_raw_session(monitor, tmp_path)
        assert monitor.is_healthy is True

        monitor._handle_raw_download_blocked(
            RawDownloadBlocked(
                session_key=self.SESSION_KEY,
                error_message=error_message,
            )
        )

        assert monitor.sessions[self.SESSION_KEY].state == SessionState.BLOCKED
        assert monitor._creator_states["creator1"].is_current_stream_blocked is True
        assert monitor.is_healthy is True


class TestHeartbeatLogOptimization:
    """Tests for heartbeat log optimization (state-change only logging)."""

    def test_no_status_log_when_state_unchanged(
        self, mock_read_config, mock_api, monitor
    ):
        """Test that status log is not emitted when state is unchanged."""
        mock_api.get_livestream_status.return_value = []
        mock_read_config.return_value = _creator1_config()

        with patch.object(monitor.logger, "info") as mock_info:
            monitor.check_live_streams_and_start_download()
            first_call_count = mock_info.call_count

            monitor.check_live_streams_and_start_download()
            second_call_count = mock_info.call_count

        # Second call should not add new status logs
        assert second_call_count == first_call_count

    def test_status_log_when_download_starts(self, mock_read_config, mock_api, monitor):
        """Test that status log is emitted when download count changes."""
        mock_stream = _live_stream(start=datetime(2026, 1, 26, 12, 0, 0))
        mock_api.get_livestream_status.return_value = [mock_stream]
        mock_api.get_stream_url.return_value = "http://example.com/stream.m3u8"
        mock_read_config.return_value = _creator1_config()

        with patch.object(monitor.logger, "info") as mock_info:
            monitor.check_live_streams_and_start_download()

        # Should log status when download starts
        status_logs = [c for c in mock_info.call_args_list if "Status" in str(c)]
        assert len(status_logs) >= 1

    def test_status_log_when_download_stops(self, mock_read_config, mock_api, monitor):
        """Test that status log is emitted when download count changes to zero."""
        mock_read_config.return_value = _creator1_config()
        # Simulate previous state with active downloads
        monitor._last_status = {"active_downloads": 1, "monitored_live": 1}
        mock_api.get_livestream_status.return_value = []

        with patch.object(monitor.logger, "info") as mock_info:
            monitor.check_live_streams_and_start_download()

        # Should log status when downloads stop
        status_logs = [c for c in mock_info.call_args_list if "Status" in str(c)]
        assert len(status_logs) >= 1

    def test_periodic_heartbeat_every_n_checks(
        self, mock_read_config, mock_api, monitor
    ):
        """Test that periodic heartbeat is logged every N checks even if state unchanged."""
        mock_api.get_livestream_status.return_value = []
        mock_read_config.return_value = _creator1_config()

        with patch.object(monitor.logger, "debug") as mock_debug:
            # Run multiple checks
            for _ in range(10):
                monitor.check_live_streams_and_start_download()

        # Should have at least one periodic heartbeat (debug level; quiet
        # nights only show life when DEBUG logging is enabled)
        heartbeat_logs = [c for c in mock_debug.call_args_list if "Checked" in str(c)]
        assert len(heartbeat_logs) >= 1


class TestSessionLifecycleLogging:
    """Tests for session lifecycle observability logs."""

    def test_process_live_stream_logs_local_recording_start_when_creator_lock_exists(
        self, monitor, tmp_path
    ):
        """Test creator-lock skip logs include the local recording start timestamp."""
        _register_creator(monitor)
        recording_started_at = datetime(2026, 3, 7, 5, 0, 5)
        _seed_session(
            monitor,
            "creator1:1772859660",
            start=datetime(2026, 3, 7, 5, 1, 0),
            state=SessionState.RAW_RUNNING,
            output_dir=tmp_path / "running",
            prefix="20260307_050005_",
            recording_started_at=recording_started_at,
        )
        monitor._active_raw_session_by_creator["creator1"] = "creator1:1772859660"

        mock_stream = _live_stream(oid="stream-1", start=datetime(2026, 3, 7, 5, 1, 0))

        with patch.object(monitor.logger, "debug") as mock_debug:
            monitor._process_live_stream(mock_stream)

        assert any(
            f"active_recording_started_at={recording_started_at.isoformat()}"
            in str(call)
            for call in mock_debug.call_args_list
        )
        assert any(
            "reason=active_raw_running" in str(call)
            for call in mock_debug.call_args_list
        )

    def test_raw_download_completion_logs_merge_queue(self, monitor, tmp_path):
        """Test raw completion logs that merge work has been queued."""
        _seed_session(
            monitor,
            "creator1:stream-1",
            start=datetime(2026, 3, 7, 5, 0, 0),
            state=SessionState.RAW_RUNNING,
            output_dir=tmp_path / "staging",
        )

        with patch.object(monitor.merge_executor, "submit_merge") as mock_submit_merge:
            with patch.object(monitor.logger, "info") as mock_info:
                monitor._handle_raw_download_completed(
                    RawDownloadCompleted(
                        session_key="creator1:stream-1",
                        output_dir=tmp_path / "staging",
                    )
                )

        mock_submit_merge.assert_called_once()
        assert any("Queued merge" in str(call) for call in mock_info.call_args_list)

    def test_merge_completed_logs_final_output_path(self, monitor, tmp_path):
        """Test merge completion logs the final output path."""
        _seed_session(
            monitor,
            "creator1:stream-1",
            start=datetime(2026, 3, 7, 5, 0, 0),
            state=SessionState.MERGING,
            output_dir=tmp_path / "staging",
        )
        output_path = (
            tmp_path / "archive" / "Creator1" / "#Creator1 2026-03-07 Test Stream.mp4"
        )

        with patch.object(monitor.logger, "info") as mock_info:
            monitor._handle_monitor_event(
                MergeCompleted(
                    session_key="creator1:stream-1",
                    output_path=output_path,
                )
            )

        assert any("Merge completed" in str(call) for call in mock_info.call_args_list)


class TestMonitorHeartbeatFile:
    """Poll-cycle Docker heartbeat file: touch, OSError guard, finally on raise."""

    def test_poll_cycle_touches_heartbeat(
        self, mock_read_config, monitor, mock_api, tmp_path, monkeypatch
    ):
        """A completed poll cycle creates/updates the heartbeat file."""
        path = tmp_path / "heartbeat"
        monkeypatch.setattr(health, "HEARTBEAT_FILE", str(path))
        mock_api.get_livestream_status.return_value = []
        mock_read_config.return_value = _runtime_config([])

        assert not path.exists()
        monitor.check_live_streams_and_start_download()
        assert path.exists()

    def test_oserror_on_touch_does_not_break_cycle(
        self, mock_read_config, monitor, mock_api, monkeypatch, caplog
    ):
        """OSError writing the heartbeat is logged once and does not fail the poll."""
        mock_api.get_livestream_status.return_value = []
        mock_read_config.return_value = _runtime_config([])

        def boom() -> None:
            raise OSError("disk full")

        monkeypatch.setattr("core.live_stream_monitor.touch_heartbeat", boom)

        with caplog.at_level("WARNING"):
            monitor.check_live_streams_and_start_download()
            monitor.check_live_streams_and_start_download()

        assert monitor.is_healthy is True
        warnings = [
            r for r in caplog.records if "Failed to write heartbeat file" in r.message
        ]
        assert len(warnings) == 1

    def test_raising_cycle_still_touches_heartbeat(
        self, mock_read_config, monitor, mock_api, tmp_path, monkeypatch
    ):
        """A poll body that raises still updates the heartbeat in finally."""
        path = tmp_path / "heartbeat"
        monkeypatch.setattr(health, "HEARTBEAT_FILE", str(path))
        mock_read_config.return_value = _runtime_config([])
        mock_api.get_livestream_status.side_effect = RPlayAPIError("API down")

        assert not path.exists()
        monitor.check_live_streams_and_start_download()
        assert path.exists()
        assert monitor.is_healthy is False
