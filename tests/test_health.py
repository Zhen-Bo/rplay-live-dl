"""Tests for Docker heartbeat health probe CLI."""

import os
import time

from core import health
from core.constants import DEFAULT_INTERVAL
from core.health import main, touch_heartbeat


class TestHealthCLI:
    """Probe CLI surface: main() exit codes and reasons."""

    def test_fresh_file_is_healthy(self, tmp_path, monkeypatch):
        """A just-touched heartbeat file reports healthy."""
        path = tmp_path / "heartbeat"
        monkeypatch.setattr(health, "HEARTBEAT_FILE", str(path))
        touch_heartbeat()

        assert main() == 0

    def test_missing_file_is_unhealthy(self, tmp_path, monkeypatch, capsys):
        """A missing heartbeat file reports unhealthy with a reason."""
        path = tmp_path / "missing-heartbeat"
        monkeypatch.setattr(health, "HEARTBEAT_FILE", str(path))

        assert main() == 1
        assert "missing" in capsys.readouterr().err

    def test_stale_mtime_is_unhealthy(self, tmp_path, monkeypatch, capsys):
        """A heartbeat older than 3×INTERVAL reports unhealthy."""
        path = tmp_path / "heartbeat"
        monkeypatch.setattr(health, "HEARTBEAT_FILE", str(path))
        monkeypatch.delenv("INTERVAL", raising=False)
        touch_heartbeat()

        max_age = 3 * DEFAULT_INTERVAL
        stale_mtime = time.time() - max_age - 1
        os.utime(path, (stale_mtime, stale_mtime))

        assert main() == 1
        assert "stale" in capsys.readouterr().err

    def test_exact_threshold_is_unhealthy(self, tmp_path, monkeypatch, capsys):
        """age == max_age (3×INTERVAL) is unhealthy; fresher means strictly less."""
        path = tmp_path / "heartbeat"
        monkeypatch.setattr(health, "HEARTBEAT_FILE", str(path))
        monkeypatch.delenv("INTERVAL", raising=False)
        touch_heartbeat()

        now = 1_700_000_000.0
        max_age = 3 * DEFAULT_INTERVAL
        monkeypatch.setattr(time, "time", lambda: now)
        os.utime(path, (now - max_age, now - max_age))

        assert main() == 1
        err = capsys.readouterr().err
        assert "stale" in err
        assert f"max={max_age}" in err

    def test_future_mtime_is_unhealthy(self, tmp_path, monkeypatch, capsys):
        """A future mtime (clock stepped backwards) is unhealthy, not forever-healthy."""
        path = tmp_path / "heartbeat"
        monkeypatch.setattr(health, "HEARTBEAT_FILE", str(path))
        touch_heartbeat()

        future = time.time() + 3600
        os.utime(path, (future, future))

        assert main() == 1
        assert "clock skew" in capsys.readouterr().err

    def test_threshold_respects_interval_env(self, tmp_path, monkeypatch, capsys):
        """Staleness threshold scales with INTERVAL from the environment."""
        path = tmp_path / "heartbeat"
        monkeypatch.setattr(health, "HEARTBEAT_FILE", str(path))
        monkeypatch.setenv("INTERVAL", "10")
        touch_heartbeat()

        # age 25s is fresh at INTERVAL=10 (max=30) but would be stale at 60.
        now = 1_700_000_000.0
        monkeypatch.setattr(time, "time", lambda: now)
        os.utime(path, (now - 25, now - 25))
        assert main() == 0

        # Drop INTERVAL so the same age exceeds 3×interval.
        monkeypatch.setenv("INTERVAL", "5")
        assert main() == 1
        err = capsys.readouterr().err
        assert "stale" in err
        assert "max=15" in err
