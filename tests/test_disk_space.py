import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.disk_space import (
    GIB,
    DiskSpaceMonitor,
    InsufficientMergeSpaceError,
    ensure_merge_space,
)
from core.orphan_recovery import recover_orphaned_sessions
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


def test_merge_preflight_rejects_without_touching_inputs(tmp_path, monkeypatch):
    raw = tmp_path / "raw.ts"
    raw.write_bytes(b"raw")
    monkeypatch.setattr(
        "core.disk_space.shutil.disk_usage", lambda _: SimpleNamespace(free=0)
    )
    with pytest.raises(InsufficientMergeSpaceError, match="Insufficient merge space"):
        ensure_merge_space([raw], tmp_path / "out.mp4")
    assert raw.read_bytes() == b"raw"


@pytest.mark.parametrize("free_gib,ok", [(1.6, True), (1.4, False)])
def test_default_merge_budget_covers_one_temp_copy_plus_reserve(
    tmp_path, monkeypatch, free_gib, ok
):
    raw = tmp_path / "raw.ts"
    raw.write_bytes(b"raw")
    monkeypatch.setattr(
        "core.disk_space.Path.stat", lambda _: SimpleNamespace(st_size=GIB // 2)
    )
    monkeypatch.setattr(
        "core.disk_space.shutil.disk_usage",
        lambda _: SimpleNamespace(free=int(free_gib * GIB)),
    )
    # 0.5 GiB input x 1.1 + 1 GiB reserve = 1.55 GiB.
    if ok:
        ensure_merge_space([raw], tmp_path / "out.mp4")
    else:
        with pytest.raises(InsufficientMergeSpaceError):
            ensure_merge_space([raw], tmp_path / "out.mp4")


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
