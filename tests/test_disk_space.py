import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.disk_space import DiskSpaceMonitor, GIB
from core.orphan_recovery import recover_orphaned_sessions
from core.utils import merge_ts_files_to_mp4
from models.env import EnvConfig


def test_alert_transitions_hysteresis_and_reminders(tmp_path, monkeypatch):
    clock = [0]
    free = [40]
    monkeypatch.setattr("core.disk_space.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(
        "core.disk_space.shutil.disk_usage",
        lambda _: SimpleNamespace(free=free[0] * GIB),
    )
    monitor = DiskSpaceMonitor()
    logger = logging.getLogger(__name__)

    def check(value):
        free[0] = value
        alert = monitor.check(tmp_path, logger)
        return alert.level if alert else None

    assert check(40) is None
    assert check(29) == "warning"
    assert check(28) is None
    assert check(9) == "critical"
    assert check(11) is None
    assert check(12) == "warning"
    assert check(31) is None
    assert check(32) == "recovered"
    assert check(40) is None
    assert check(9) == "critical"
    clock[0] += 3600
    assert check(9) == "critical"


def test_merge_preflight_does_not_run_or_delete_inputs(tmp_path, monkeypatch):
    raw = tmp_path / "raw.ts"
    raw.write_bytes(b"raw")
    run = Mock()
    monkeypatch.setattr(
        "core.disk_space.shutil.disk_usage", lambda _: SimpleNamespace(free=0)
    )
    with pytest.raises(OSError, match="Insufficient merge space"):
        merge_ts_files_to_mp4([raw], tmp_path / "out.mp4", run)
    run.assert_not_called()
    assert raw.read_bytes() == b"raw"
    assert not (tmp_path / "merge-inputs.txt").exists()


def test_recovery_retains_raw_when_space_is_insufficient(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "archive" / "creator"
    folder.mkdir(parents=True)
    raw = folder / "20260930_120000_title.ts"
    raw.write_bytes(b"raw")
    monkeypatch.setattr(
        "core.disk_space.shutil.disk_usage", lambda _: SimpleNamespace(free=0)
    )
    run = Mock()
    monkeypatch.setattr("core.orphan_recovery.subprocess.run", run)
    recover_orphaned_sessions(logging.getLogger(__name__))
    run.assert_not_called()
    assert raw.read_bytes() == b"raw"


def test_invalid_threshold_order_is_rejected():
    with pytest.raises(ValueError, match="DISK_CRITICAL_GB"):
        EnvConfig(
            user_oid="u", refresh_token="t", disk_warning_gb=10, disk_critical_gb=20
        )
