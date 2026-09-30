"""Tests for session merge flow."""

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.live_stream_monitor import LiveStreamMonitor
from core.rplay import RPlayAPI
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


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    """Build a monitor rooted in tmp_path and shut it down afterwards."""
    monkeypatch.chdir(tmp_path)
    monitor = LiveStreamMonitor(api_client=MagicMock(spec=RPlayAPI))
    yield monitor
    monitor.shutdown()


@pytest.fixture
def output_dir(tmp_path):
    """Create the creator archive directory the merge writes into."""
    path = tmp_path / "archive" / "Creator"
    path.mkdir(parents=True)
    return path


def _merge_job(output_dir, **overrides):
    """Build a merge job for the default 12:00 session, with keyword overrides."""
    fields = {
        "session_key": "creator1:2026-03-06T12:00:00",
        "creator_name": "Creator",
        "title": "123",
        "stream_start_time": datetime(2026, 3, 6, 12, 0, 0),
        "output_dir": output_dir,
        "session_prefix": "20260306_120000_",
    }
    fields.update(overrides)
    return MergeJobSpec(**fields)


def _write_mp4(ts_files, output_path):
    """Stand in for ffmpeg by writing a small mp4."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"mp4")


class TestMergeFlow:
    """Tests for merging raw ts outputs into final mp4 files."""

    def test_merge_uses_stream_start_time_for_mp4_name(
        self, tmp_path, monitor, output_dir
    ):
        """Test one session merges to a mp4 file named after stream start time."""
        prefix = "20260306_120000_"
        ts_file = output_dir / f"{prefix}#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        monitor._run_ffmpeg_merge = _write_mp4

        event = monitor._merge_session_to_mp4(
            _merge_job(
                output_dir,
                session_prefix=prefix,
            )
        )

        assert isinstance(event, MergeCompleted)
        assert (
            event.output_path
            == tmp_path / "archive" / "Creator" / "#Creator 2026-03-06 123.mp4"
        )
        assert not ts_file.exists()

    def test_second_session_same_title_increments_mp4_suffix(
        self, monitor, output_dir
    ):
        """Test a second session with the same title gets a suffixed mp4 name."""
        (output_dir / "#Creator 2026-03-06 123.mp4").write_bytes(b"existing")
        prefix = "20260306_123000_"
        ts_file = output_dir / f"{prefix}#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        monitor._run_ffmpeg_merge = _write_mp4

        event = monitor._merge_session_to_mp4(
            _merge_job(
                output_dir,
                session_key="creator1:2026-03-06T12:30:00",
                stream_start_time=datetime(2026, 3, 6, 12, 30, 0),
                session_prefix=prefix,
            )
        )

        assert isinstance(event, MergeCompleted)
        assert event.output_path == output_dir / "#Creator 2026-03-06 123_1.mp4"

    def test_merge_installs_next_suffix_when_name_is_claimed_mid_merge(
        self, monitor, output_dir
    ):
        """Test a concurrent claimant cannot be overwritten after ffmpeg finishes."""
        prefix = "20260306_123000_"
        ts_file = output_dir / f"{prefix}#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        final_path = output_dir / "#Creator 2026-03-06 123.mp4"
        captured = []

        def fake_merge(ts_files, output_path):
            captured.append(output_path)
            output_path.write_bytes(b"ours")
            # Simulate another merge completing while this ffmpeg invocation
            # is still in flight. The install must keep this claimant intact.
            final_path.write_bytes(b"claimant")

        monitor._run_ffmpeg_merge = fake_merge

        event = monitor._merge_session_to_mp4(
            _merge_job(
                output_dir,
                session_key="creator1:2026-03-06T12:30:00",
                stream_start_time=datetime(2026, 3, 6, 12, 30, 0),
                session_prefix=prefix,
            )
        )

        assert isinstance(event, MergeCompleted)
        assert captured == [output_dir / ".#Creator 2026-03-06 123.merging.mp4"]
        assert captured[0] != final_path
        assert final_path.read_bytes() == b"claimant"
        assert (output_dir / "#Creator 2026-03-06 123_1.mp4").read_bytes() == b"ours"
        assert not captured[0].exists()
        assert not ts_file.exists()

    def test_merge_long_title_fits_final_and_temp_names(self, monitor, output_dir):
        """Test a long Unicode title still merges within the filename byte limit."""
        prefix = "20260306_120000_"
        ts_file = output_dir / f"{prefix}#Creator recording.ts"
        ts_file.write_bytes(b"ts")
        captured = []
        title = "標題" * 200

        def fake_merge(ts_files, output_path):
            captured.append(output_path)
            output_path.write_bytes(b"mp4")

        monitor._run_ffmpeg_merge = fake_merge

        event = monitor._merge_session_to_mp4(
            _merge_job(
                output_dir,
                title=title,
                session_prefix=prefix,
            )
        )

        assert isinstance(event, MergeCompleted)
        assert len(event.output_path.name.encode("utf-8")) <= 255
        assert captured and len(captured[0].name.encode("utf-8")) <= 255
        assert event.output_path.read_bytes() == b"mp4"
        assert not ts_file.exists()

    def test_stale_merge_temp_is_cleared_before_noop_merge(self, monitor, output_dir):
        """Test a stale temp cannot be mistaken for bytes from a new merge."""
        prefix = "20260306_120000_"
        ts_file = output_dir / f"{prefix}#Creator recording.ts"
        ts_file.write_bytes(b"ts")
        base_path = monitor._build_final_output_base_path(
            creator_name="Creator",
            title="Recording",
            stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
        )
        temp_path = monitor._build_merge_temp_path(base_path)
        temp_path.write_bytes(b"stale bytes from a killed merge")

        def fake_merge(ts_files, output_path):
            # Simulate ffmpeg returning successfully without producing output.
            return None

        monitor._run_ffmpeg_merge = fake_merge

        event = monitor._merge_session_to_mp4(
            _merge_job(
                output_dir,
                title="Recording",
                session_prefix=prefix,
            )
        )

        assert isinstance(event, MergeFailed)
        assert "merge produced no output" in event.error_message
        assert ts_file.exists()
        assert not temp_path.exists()
        assert not list(output_dir.glob("*.mp4"))

    @pytest.mark.parametrize(
        "installed_bytes",
        [b"", None],
        ids=["empty-installed-output", "missing-installed-output"],
    )
    def test_invalid_installed_output_keeps_raw_inputs(
        self, monkeypatch, monitor, output_dir, installed_bytes
    ):
        """Test raw inputs survive when the final install is not usable."""
        prefix = "20260306_120000_"
        ts_file = output_dir / f"{prefix}#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")

        def fake_merge(ts_files, output_path):
            output_path.write_bytes(b"mp4")

        def fake_install(logger, temp_path, base_path):
            if installed_bytes is not None:
                base_path.write_bytes(installed_bytes)
            temp_path.unlink()
            return base_path

        monitor._run_ffmpeg_merge = fake_merge
        monkeypatch.setattr(
            "core.live_stream_monitor.install_merge_output_without_overwrite",
            fake_install,
        )

        event = monitor._merge_session_to_mp4(
            _merge_job(
                output_dir,
                session_prefix=prefix,
            )
        )

        assert isinstance(event, MergeFailed)
        assert "installed merge output is invalid" in event.error_message
        assert ts_file.exists()
        assert not (output_dir / "#Creator 2026-03-06 123.mp4").exists()

    def test_failed_merge_discards_partial_mp4_and_keeps_ts(
        self, monitor, output_dir
    ):
        """Test a failed merge removes partial mp4 output but keeps raw ts input."""
        prefix = "20260306_120000_"
        ts_file = output_dir / f"{prefix}#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        partial_mp4 = output_dir / "#Creator 2026-03-06 123.mp4"
        captured = []

        def fake_merge(ts_files, output_path):
            captured.append(output_path)
            output_path.write_bytes(b"partial")
            raise RuntimeError("boom")

        monitor._run_ffmpeg_merge = fake_merge

        event = monitor._merge_session_to_mp4(
            _merge_job(
                output_dir,
                session_prefix=prefix,
            )
        )

        assert isinstance(event, MergeFailed)
        assert event.error_message == "boom"
        assert not partial_mp4.exists()
        assert captured and not captured[0].exists()
        assert ts_file.exists()
        assert not (output_dir / "_failed").exists()

    def test_ts_cleanup_failure_keeps_the_merged_mp4(
        self, monkeypatch, monitor, output_dir
    ):
        """Test a locked .ts after a successful merge never deletes the mp4.

        Regression guard: the cleanup loop used to share the failure handler,
        so a locked .ts made the discard step delete a perfectly good mp4.
        """
        prefix = "20260306_120000_"
        ts_file = output_dir / f"{prefix}#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")

        def fake_merge(ts_files, output_path):
            output_path.write_bytes(b"mp4")

        monitor._run_ffmpeg_merge = fake_merge

        real_unlink = Path.unlink

        def deny_ts_unlink(self, missing_ok=False):
            if self.suffix == ".ts":
                raise OSError("locked")
            return real_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", deny_ts_unlink)

        event = monitor._merge_session_to_mp4(
            _merge_job(
                output_dir,
                session_prefix=prefix,
            )
        )

        assert isinstance(event, MergeCompleted)
        assert event.output_path.exists()
        assert ts_file.exists()

    def test_merge_timeout_returns_failure_event(self, monitor, output_dir):
        """Test ffmpeg timeout becomes a merge failure event."""
        prefix = "20260306_120000_"
        (output_dir / f"{prefix}#Creator 2026-03-06 123.ts").write_bytes(b"ts")

        def fake_merge(ts_files, output_path):
            raise subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1)

        monitor._run_ffmpeg_merge = fake_merge

        event = monitor._merge_session_to_mp4(
            _merge_job(
                output_dir,
                session_prefix=prefix,
            )
        )

        assert isinstance(event, MergeFailed)
        assert "timeout" in event.error_message.lower()

    def test_merge_only_picks_up_ts_files_matching_session_prefix(
        self, monitor, output_dir
    ):
        """Test merge globs only ts files with the correct session prefix."""
        prefix = "20260306_120000_"
        target_ts = output_dir / f"{prefix}#Creator 2026-03-06 123.ts"
        target_ts.write_bytes(b"ts")

        # A ts file from a different session — must NOT be picked up
        other_ts = output_dir / "20260305_090000_#Creator 2026-03-05 Old.ts"
        other_ts.write_bytes(b"ts")

        captured_files = []

        def fake_merge(ts_files, output_path):
            captured_files.extend(ts_files)
            _write_mp4(ts_files, output_path)

        monitor._run_ffmpeg_merge = fake_merge

        monitor._merge_session_to_mp4(
            _merge_job(
                output_dir,
                session_prefix=prefix,
            )
        )

        assert captured_files == [target_ts]
        assert other_ts.exists()  # untouched

    def test_run_ffmpeg_merge_escapes_single_quotes_in_concat_paths(
        self, tmp_path, monitor, output_dir
    ):
        """Test concat input escapes apostrophes in fragment paths."""
        ts_file = output_dir / "#Creator 2026-03-06 it's live.ts"
        ts_file.write_bytes(b"ts")
        output_path = tmp_path / "archive" / "Creator" / "final.mp4"
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["content"] = Path(cmd[7]).read_text(encoding="utf-8")

        # Patched at the tracked-subprocess seam: the merge spawns ffmpeg through
        # Popen now so shutdown can spare it by pid.
        with patch.object(monitor, "_run_merge_subprocess", side_effect=fake_run):
            monitor._run_ffmpeg_merge([ts_file], output_path)

        assert r"it'\''s live.ts" in captured["content"]

    def test_run_merge_subprocess_times_out_and_releases_its_pid(self, monitor):
        """Test a timed-out merge child raises with its timeout and leaves no tracked pid."""
        # _merge_session_to_mp4 reads exc.timeout, so it has to survive the
        # switch from subprocess.run to Popen.communicate.
        monitor.merge_timeout_seconds = 0.3
        with pytest.raises(subprocess.TimeoutExpired) as timeout_error:
            monitor._run_merge_subprocess(
                [sys.executable, "-c", "import time; time.sleep(30)"]
            )

        assert timeout_error.value.timeout == 0.3
        # A leaked pid would make a later shutdown spare a recording forever.
        assert monitor._merge_process_pids == set()

    def test_final_output_base_path_uses_local_timezone_for_date(
        self, taipei_timezone, monitor
    ):
        """Test the mp4 filename date uses local time, not raw UTC (tz regression guard)."""
        # 2026-03-06 23:50 UTC is 2026-03-07 07:50 in Asia/Taipei (UTC+8).
        stream_start_time = datetime(2026, 3, 6, 23, 50, 0, tzinfo=timezone.utc)

        output_path = monitor._build_final_output_base_path(
            creator_name="Creator",
            title="Title",
            stream_start_time=stream_start_time,
        )

        assert "2026-03-07" in output_path.name
        assert "2026-03-06" not in output_path.name
