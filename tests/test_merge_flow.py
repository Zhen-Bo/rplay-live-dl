"""Tests for session merge flow."""

import subprocess
from pathlib import Path
from unittest.mock import patch
from datetime import datetime

from core.live_stream_monitor import LiveStreamMonitor
from models.download import MergeCompleted, MergeFailed, MergeJobSpec


class TestMergeFlow:
    """Tests for merging raw ts outputs into final mp4 files."""

    def test_merge_uses_old_visible_mp4_name(self, tmp_path, monkeypatch):
        """Test one session merges to the legacy visible mp4 file name."""
        monkeypatch.chdir(tmp_path)
        monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=None)
        session_key = "creator1:2026-03-06T12:00:00"
        staging_dir = monitor._build_staging_dir("Creator", session_key)
        staging_dir.mkdir(parents=True)
        ts_file = staging_dir / "#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")

        def fake_merge(ts_files, output_path):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"mp4")

        monitor._run_ffmpeg_merge = fake_merge

        event = monitor._merge_session_to_mp4(
            MergeJobSpec(
                session_key=session_key,
                creator_name="Creator",
                title="123",
                stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
                staging_dir=staging_dir,
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
        session_key = "creator1:2026-03-06T12:30:00"
        staging_dir = monitor._build_staging_dir("Creator", session_key)
        staging_dir.mkdir(parents=True)
        (staging_dir / "#Creator 2026-03-06 123.ts").write_bytes(b"ts")

        def fake_merge(ts_files, output_path):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"mp4")

        monitor._run_ffmpeg_merge = fake_merge

        event = monitor._merge_session_to_mp4(
            MergeJobSpec(
                session_key=session_key,
                creator_name="Creator",
                title="123",
                stream_start_time=datetime(2026, 3, 6, 12, 30, 0),
                staging_dir=staging_dir,
            )
        )

        assert isinstance(event, MergeCompleted)
        assert event.output_path == final_dir / "#Creator 2026-03-06 123_1.mp4"
        monitor.shutdown()

    def test_failed_merge_moves_ts_files_to_failed_directory(self, tmp_path, monkeypatch):
        """Test failed merges move raw files into the visible failed directory."""
        monkeypatch.chdir(tmp_path)
        monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=None)
        session_key = "creator1:2026-03-06T12:00:00"
        staging_dir = monitor._build_staging_dir("Creator", session_key)
        staging_dir.mkdir(parents=True)
        ts_file = staging_dir / "#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")

        def fake_merge(ts_files, output_path):
            raise RuntimeError("merge failed")

        monitor._run_ffmpeg_merge = fake_merge

        event = monitor._merge_session_to_mp4(
            MergeJobSpec(
                session_key=session_key,
                creator_name="Creator",
                title="123",
                stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
                staging_dir=staging_dir,
            )
        )

        assert isinstance(event, MergeFailed)
        assert event.failed_staging_dir == (
            tmp_path / "archive" / "Creator" / "_failed" / monitor._make_session_dir_name(session_key)
        )
        assert (event.failed_staging_dir / "#Creator 2026-03-06 123.ts").exists()
        assert not ts_file.exists()
        monitor.shutdown()

    def test_merge_timeout_returns_failure_event(self, tmp_path, monkeypatch):
        """Test ffmpeg timeout becomes a merge failure event."""
        monkeypatch.chdir(tmp_path)
        monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=None)
        session_key = "creator1:2026-03-06T12:00:00"
        staging_dir = monitor._build_staging_dir("Creator", session_key)
        staging_dir.mkdir(parents=True)
        (staging_dir / "#Creator 2026-03-06 123.ts").write_bytes(b"ts")

        def fake_merge(ts_files, output_path):
            raise subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1)

        monitor._run_ffmpeg_merge = fake_merge

        event = monitor._merge_session_to_mp4(
            MergeJobSpec(
                session_key=session_key,
                creator_name="Creator",
                title="123",
                stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
                staging_dir=staging_dir,
            )
        )

        assert isinstance(event, MergeFailed)
        assert "timeout" in event.error_message.lower()
        monitor.shutdown()


    def test_run_ffmpeg_merge_escapes_single_quotes_in_concat_paths(self, tmp_path, monkeypatch):
        """Test concat input escapes apostrophes in fragment paths."""
        monkeypatch.chdir(tmp_path)
        monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=None)
        staging_dir = tmp_path / "staging"
        staging_dir.mkdir()
        ts_file = staging_dir / "#Creator 2026-03-06 it's live.ts"
        ts_file.write_bytes(b"ts")
        output_path = tmp_path / "archive" / "Creator" / "final.mp4"
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["content"] = Path(cmd[7]).read_text(encoding="utf-8")

        with patch("core.live_stream_monitor.subprocess.run", side_effect=fake_run):
            monitor._run_ffmpeg_merge([ts_file], output_path)

        escaped = ts_file.resolve().as_posix().replace("'", "'\''")
        assert captured["content"] == f"file '{escaped}'"
        monitor.shutdown()
