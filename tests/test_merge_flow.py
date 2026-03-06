"""Tests for session merge flow."""

from datetime import datetime

from core.live_stream_monitor import LiveStreamMonitor
from models.download import DownloadResult, DownloadSession, SessionState


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
        monitor.sessions[session_key] = DownloadSession(
            session_key=session_key,
            creator_oid="creator1",
            creator_name="Creator",
            title="123",
            stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
            state=SessionState.MERGE_QUEUED,
            staging_dir=staging_dir,
        )
        result = DownloadResult(
            session_key=session_key,
            staging_dir=staging_dir,
            base_output_stem="#Creator 2026-03-06 123",
            creator_name="Creator",
            title="123",
            stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
        )

        def fake_merge(ts_files, output_path):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"mp4")

        monitor._run_ffmpeg_merge = fake_merge

        monitor._merge_session_to_mp4(session_key, result)

        assert (tmp_path / "archive" / "Creator" / "#Creator 2026-03-06 123.mp4").exists()
        assert not ts_file.exists()
        assert monitor.sessions[session_key].state == SessionState.DONE

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
        monitor.sessions[session_key] = DownloadSession(
            session_key=session_key,
            creator_oid="creator1",
            creator_name="Creator",
            title="123",
            stream_start_time=datetime(2026, 3, 6, 12, 30, 0),
            state=SessionState.MERGE_QUEUED,
            staging_dir=staging_dir,
        )
        result = DownloadResult(
            session_key=session_key,
            staging_dir=staging_dir,
            base_output_stem="#Creator 2026-03-06 123",
            creator_name="Creator",
            title="123",
            stream_start_time=datetime(2026, 3, 6, 12, 30, 0),
        )

        def fake_merge(ts_files, output_path):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"mp4")

        monitor._run_ffmpeg_merge = fake_merge

        monitor._merge_session_to_mp4(session_key, result)

        assert (final_dir / "#Creator 2026-03-06 123_1.mp4").exists()

    def test_failed_merge_moves_ts_files_to_failed_directory(self, tmp_path, monkeypatch):
        """Test failed merges move raw files into the visible failed directory."""
        monkeypatch.chdir(tmp_path)
        monitor = LiveStreamMonitor(auth_token="token", user_oid="oid", api=None)
        session_key = "creator1:2026-03-06T12:00:00"
        staging_dir = monitor._build_staging_dir("Creator", session_key)
        staging_dir.mkdir(parents=True)
        ts_file = staging_dir / "#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        monitor.sessions[session_key] = DownloadSession(
            session_key=session_key,
            creator_oid="creator1",
            creator_name="Creator",
            title="123",
            stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
            state=SessionState.MERGE_QUEUED,
            staging_dir=staging_dir,
        )
        result = DownloadResult(
            session_key=session_key,
            staging_dir=staging_dir,
            base_output_stem="#Creator 2026-03-06 123",
            creator_name="Creator",
            title="123",
            stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
        )

        def fake_merge(ts_files, output_path):
            raise RuntimeError("merge failed")

        monitor._run_ffmpeg_merge = fake_merge

        monitor._merge_session_to_mp4(session_key, result)

        failed_ts = (
            tmp_path
            / "archive"
            / "Creator"
            / "_failed"
            / monitor._make_session_dir_name(session_key)
            / "#Creator 2026-03-06 123.ts"
        )
        assert failed_ts.exists()
        assert monitor.sessions[session_key].state == SessionState.MERGE_FAILED
