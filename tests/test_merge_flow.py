"""Tests for session merge flow."""

import subprocess
import time
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone

import pytest

from core.live_stream_monitor import LiveStreamMonitor
from models.download import MergeCompleted, MergeFailed, MergeJobSpec


@pytest.fixture
def taipei_timezone(monkeypatch):
    """Pin the process timezone to UTC+8 so date-boundary assertions are host independent."""
    # time.tzset() is POSIX-only; on Windows the process timezone cannot be rebound.
    tzset = getattr(time, "tzset", None)
    if tzset is None:
        pytest.skip("pinning the process timezone requires POSIX time.tzset()")

    monkeypatch.setenv("TZ", "Asia/Taipei")
    tzset()
    try:
        yield
    finally:
        monkeypatch.undo()
        tzset()


class TestMergeFlow:
    """Tests for merging raw ts outputs into final mp4 files."""

    def test_merge_uses_stream_start_time_for_mp4_name(self, tmp_path, monkeypatch):
        """Test one session merges to a mp4 file named after stream start time."""
        monkeypatch.chdir(tmp_path)
        monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=None)
        output_dir = tmp_path / "archive" / "Creator"
        output_dir.mkdir(parents=True)
        prefix = "20260306_120000_"
        ts_file = output_dir / f"{prefix}#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")

        def fake_merge(ts_files, output_path):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"mp4")

        monitor._run_ffmpeg_merge = fake_merge

        event = monitor._merge_session_to_mp4(
            MergeJobSpec(
                session_key="creator1:2026-03-06T12:00:00",
                creator_name="Creator",
                title="123",
                stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
                output_dir=output_dir,
                session_prefix=prefix,
            )
        )

        assert isinstance(event, MergeCompleted)
        assert event.output_path == tmp_path / "archive" / "Creator" / "#Creator 2026-03-06 123.mp4"
        assert not ts_file.exists()
        monitor.shutdown()

    def test_second_session_same_title_increments_mp4_suffix(self, tmp_path, monkeypatch):
        """Test a second session with the same title gets a suffixed mp4 name."""
        monkeypatch.chdir(tmp_path)
        final_dir = tmp_path / "archive" / "Creator"
        final_dir.mkdir(parents=True)
        (final_dir / "#Creator 2026-03-06 123.mp4").write_bytes(b"existing")

        monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=None)
        prefix = "20260306_123000_"
        ts_file = final_dir / f"{prefix}#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")

        def fake_merge(ts_files, output_path):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"mp4")

        monitor._run_ffmpeg_merge = fake_merge

        event = monitor._merge_session_to_mp4(
            MergeJobSpec(
                session_key="creator1:2026-03-06T12:30:00",
                creator_name="Creator",
                title="123",
                stream_start_time=datetime(2026, 3, 6, 12, 30, 0),
                output_dir=final_dir,
                session_prefix=prefix,
            )
        )

        assert isinstance(event, MergeCompleted)
        assert event.output_path == final_dir / "#Creator 2026-03-06 123_1.mp4"
        monitor.shutdown()

    def test_failed_merge_leaves_ts_files_in_output_dir(self, tmp_path, monkeypatch):
        """Test failed merges leave raw ts files in place — no _failed/ directory."""
        monkeypatch.chdir(tmp_path)
        monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=None)
        output_dir = tmp_path / "archive" / "Creator"
        output_dir.mkdir(parents=True)
        prefix = "20260306_120000_"
        ts_file = output_dir / f"{prefix}#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")

        def fake_merge(ts_files, output_path):
            raise RuntimeError("merge failed")

        monitor._run_ffmpeg_merge = fake_merge

        event = monitor._merge_session_to_mp4(
            MergeJobSpec(
                session_key="creator1:2026-03-06T12:00:00",
                creator_name="Creator",
                title="123",
                stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
                output_dir=output_dir,
                session_prefix=prefix,
            )
        )

        assert isinstance(event, MergeFailed)
        assert ts_file.exists()
        assert not (tmp_path / "archive" / "Creator" / "_failed").exists()
        monitor.shutdown()

    def test_failed_merge_discards_partial_mp4_and_keeps_ts(self, tmp_path, monkeypatch):
        """Test a failed merge removes partial mp4 output but keeps raw ts input."""
        monkeypatch.chdir(tmp_path)
        monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=None)
        output_dir = tmp_path / "archive" / "Creator"
        output_dir.mkdir(parents=True)
        prefix = "20260306_120000_"
        ts_file = output_dir / f"{prefix}#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        partial_mp4 = output_dir / "#Creator 2026-03-06 123.mp4"

        def fake_merge(ts_files, output_path):
            output_path.write_bytes(b"partial")
            raise RuntimeError("boom")

        monitor._run_ffmpeg_merge = fake_merge

        event = monitor._merge_session_to_mp4(
            MergeJobSpec(
                session_key="creator1:2026-03-06T12:00:00",
                creator_name="Creator",
                title="123",
                stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
                output_dir=output_dir,
                session_prefix=prefix,
            )
        )

        assert isinstance(event, MergeFailed)
        assert event.error_message == "boom"
        assert not partial_mp4.exists()
        assert ts_file.exists()
        monitor.shutdown()

    def test_merge_timeout_returns_failure_event(self, tmp_path, monkeypatch):
        """Test ffmpeg timeout becomes a merge failure event."""
        monkeypatch.chdir(tmp_path)
        monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=None)
        output_dir = tmp_path / "archive" / "Creator"
        output_dir.mkdir(parents=True)
        prefix = "20260306_120000_"
        (output_dir / f"{prefix}#Creator 2026-03-06 123.ts").write_bytes(b"ts")

        def fake_merge(ts_files, output_path):
            raise subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1)

        monitor._run_ffmpeg_merge = fake_merge

        event = monitor._merge_session_to_mp4(
            MergeJobSpec(
                session_key="creator1:2026-03-06T12:00:00",
                creator_name="Creator",
                title="123",
                stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
                output_dir=output_dir,
                session_prefix=prefix,
            )
        )

        assert isinstance(event, MergeFailed)
        assert "timeout" in event.error_message.lower()
        monitor.shutdown()

    def test_merge_only_picks_up_ts_files_matching_session_prefix(self, tmp_path, monkeypatch):
        """Test merge globs only ts files with the correct session prefix."""
        monkeypatch.chdir(tmp_path)
        monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=None)
        output_dir = tmp_path / "archive" / "Creator"
        output_dir.mkdir(parents=True)

        prefix = "20260306_120000_"
        target_ts = output_dir / f"{prefix}#Creator 2026-03-06 123.ts"
        target_ts.write_bytes(b"ts")

        # A ts file from a different session — must NOT be picked up
        other_ts = output_dir / "20260305_090000_#Creator 2026-03-05 Old.ts"
        other_ts.write_bytes(b"ts")

        captured_files = []

        def fake_merge(ts_files, output_path):
            captured_files.extend(ts_files)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"mp4")

        monitor._run_ffmpeg_merge = fake_merge

        monitor._merge_session_to_mp4(
            MergeJobSpec(
                session_key="creator1:2026-03-06T12:00:00",
                creator_name="Creator",
                title="123",
                stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
                output_dir=output_dir,
                session_prefix=prefix,
            )
        )

        assert captured_files == [target_ts]
        assert other_ts.exists()  # untouched
        monitor.shutdown()

    def test_run_ffmpeg_merge_escapes_single_quotes_in_concat_paths(self, tmp_path, monkeypatch):
        """Test concat input escapes apostrophes in fragment paths."""
        monkeypatch.chdir(tmp_path)
        monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=None)
        output_dir = tmp_path / "archive" / "Creator"
        output_dir.mkdir(parents=True)
        ts_file = output_dir / "#Creator 2026-03-06 it's live.ts"
        ts_file.write_bytes(b"ts")
        output_path = tmp_path / "archive" / "Creator" / "final.mp4"
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["content"] = Path(cmd[7]).read_text(encoding="utf-8")

        with patch("core.live_stream_monitor.subprocess.run", side_effect=fake_run):
            monitor._run_ffmpeg_merge([ts_file], output_path)

        assert r"it'\''s live.ts" in captured["content"]
        monitor.shutdown()

    def test_reserve_final_output_path_uses_local_timezone_for_date(
        self, tmp_path, monkeypatch, taipei_timezone
    ):
        """Test the mp4 filename date uses local time, not raw UTC (tz regression guard)."""
        monkeypatch.chdir(tmp_path)
        monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=None)

        # 2026-03-06 23:50 UTC is 2026-03-07 07:50 in Asia/Taipei (UTC+8).
        stream_start_time = datetime(2026, 3, 6, 23, 50, 0, tzinfo=timezone.utc)

        output_path = monitor._reserve_final_output_path(
            creator_name="Creator",
            title="Title",
            stream_start_time=stream_start_time,
        )

        assert "2026-03-07" in output_path.name
        assert "2026-03-06" not in output_path.name
        monitor.shutdown()
