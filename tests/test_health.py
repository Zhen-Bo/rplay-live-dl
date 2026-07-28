"""Tests for Docker heartbeat health probe and monitor touch."""

import os
import time
from unittest.mock import MagicMock, patch

import pytest

from core import health
from core.constants import DEFAULT_INTERVAL, HEARTBEAT_STALE_MULTIPLIER
from core.health import check_heartbeat, main, touch_heartbeat
from core.live_stream_monitor import LiveStreamMonitor
from core.rplay import RPlayAPI
from models.config import AppConfig


def _runtime_config(creators=None):
    return AppConfig(api_base_url="https://api.rplay.live", creators=creators or [])


class TestTouchAndCheck:
    """Unit tests for heartbeat file helpers."""

    def test_fresh_file_is_healthy(self, tmp_path, monkeypatch):
        """A just-touched heartbeat file reports healthy."""
        path = tmp_path / "heartbeat"
        monkeypatch.setattr(health, "HEARTBEAT_FILE_PATH", str(path))
        touch_heartbeat(str(path))

        ok, reason = check_heartbeat(str(path))

        assert ok is True
        assert reason == ""
        assert main() == 0

    def test_missing_file_is_unhealthy(self, tmp_path, monkeypatch, capsys):
        """A missing heartbeat file reports unhealthy with a reason."""
        path = tmp_path / "missing-heartbeat"
        monkeypatch.setattr(health, "HEARTBEAT_FILE_PATH", str(path))

        ok, reason = check_heartbeat(str(path))

        assert ok is False
        assert "missing" in reason
        assert main() == 1
        assert "missing" in capsys.readouterr().err

    def test_stale_mtime_is_unhealthy(self, tmp_path, monkeypatch):
        """A heartbeat older than 3×INTERVAL reports unhealthy."""
        path = tmp_path / "heartbeat"
        monkeypatch.setattr(health, "HEARTBEAT_FILE_PATH", str(path))
        monkeypatch.delenv("INTERVAL", raising=False)
        touch_heartbeat(str(path))

        max_age = HEARTBEAT_STALE_MULTIPLIER * DEFAULT_INTERVAL
        stale_mtime = time.time() - max_age - 1
        os.utime(path, (stale_mtime, stale_mtime))

        ok, reason = check_heartbeat(str(path), now=time.time())

        assert ok is False
        assert "stale" in reason
        assert main() == 1

    def test_threshold_respects_interval_env(self, tmp_path, monkeypatch):
        """Staleness threshold scales with INTERVAL from the environment."""
        path = tmp_path / "heartbeat"
        monkeypatch.setenv("INTERVAL", "10")
        touch_heartbeat(str(path))

        # age 25s is fresh at INTERVAL=10 (max=30) but would be stale at 60.
        age = 25
        mtime = time.time() - age
        os.utime(path, (mtime, mtime))
        now = time.time()

        ok, _ = check_heartbeat(str(path), now=now)
        assert ok is True

        # Drop INTERVAL so the same age exceeds 3×interval.
        monkeypatch.setenv("INTERVAL", "5")
        ok, reason = check_heartbeat(str(path), now=now)
        assert ok is False
        assert "stale" in reason
        assert "max=15" in reason


class TestMonitorHeartbeat:
    """Monitor poll cycle must touch the heartbeat without breaking on errors."""

    @patch("core.live_stream_monitor.read_config")
    def test_poll_cycle_touches_heartbeat(self, mock_read_config, tmp_path, monkeypatch):
        """A completed poll cycle creates/updates the heartbeat file."""
        path = tmp_path / "heartbeat"
        monkeypatch.setattr("core.health.HEARTBEAT_FILE_PATH", str(path))
        monkeypatch.setattr(
            "core.live_stream_monitor.touch_heartbeat",
            lambda: touch_heartbeat(str(path)),
        )
        mock_api = MagicMock(spec=RPlayAPI)
        mock_api.get_livestream_status.return_value = []
        mock_read_config.return_value = _runtime_config()
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        assert not path.exists()
        monitor.check_live_streams_and_start_download()
        assert path.exists()

    @patch("core.live_stream_monitor.read_config")
    def test_oserror_on_touch_does_not_break_cycle(
        self, mock_read_config, monkeypatch, caplog
    ):
        """OSError writing the heartbeat is logged once and does not fail the poll."""
        mock_api = MagicMock(spec=RPlayAPI)
        mock_api.get_livestream_status.return_value = []
        mock_read_config.return_value = _runtime_config()

        def boom() -> None:
            raise OSError("disk full")

        monkeypatch.setattr("core.live_stream_monitor.touch_heartbeat", boom)
        monitor = LiveStreamMonitor(
            auth_token="test_token",
            user_oid="test_oid",
            api=mock_api,
        )

        with caplog.at_level("WARNING"):
            monitor.check_live_streams_and_start_download()
            monitor.check_live_streams_and_start_download()

        assert monitor.is_healthy is True
        warnings = [
            r for r in caplog.records if "Failed to write heartbeat file" in r.message
        ]
        assert len(warnings) == 1
