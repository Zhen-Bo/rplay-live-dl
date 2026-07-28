"""Tests for startup recovery of orphaned .ts recordings."""

import logging
import subprocess
import sys
from pathlib import Path

import pytest

from core.orphan_recovery import _run_recovery_ffmpeg, recover_orphaned_sessions


@pytest.fixture
def archive(tmp_path, monkeypatch):
    """Point recovery at a temporary archive/Creator directory."""
    monkeypatch.chdir(tmp_path)
    creator_dir = tmp_path / "archive" / "Creator"
    creator_dir.mkdir(parents=True)
    return creator_dir


def _fake_successful_merge(monkeypatch, captured=None):
    """Replace the ffmpeg seam with a writer that never spawns a process."""

    def fake_merge(ts_files, output_path, run_command):
        if captured is not None:
            captured.append((list(ts_files), output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"mp4")

    monkeypatch.setattr("core.orphan_recovery.merge_ts_files_to_mp4", fake_merge)


def _fake_failing_merge(monkeypatch, error=None):
    """Replace the ffmpeg seam with one that writes a partial mp4 then fails."""

    def fake_merge(ts_files, output_path, run_command):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"partial")
        raise error or RuntimeError("boom")

    monkeypatch.setattr("core.orphan_recovery.merge_ts_files_to_mp4", fake_merge)


class TestOrphanRecovery:
    """Recovery of session .ts files left behind by an interrupted run."""

    def test_canonical_orphan_is_merged_and_inputs_deleted(self, archive, monkeypatch, caplog):
        """Test a canonical orphan merges to mp4 and its inputs are removed."""
        ts_file = archive / "20260306_120000_#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        _fake_successful_merge(monkeypatch)

        caplog.set_level(logging.INFO)
        recovered = recover_orphaned_sessions(logging.getLogger("test_recovery"))

        assert recovered == 1
        merged = archive / "#Creator 2026-03-06 123.mp4"
        assert merged.exists()
        assert not ts_file.exists()
        assert any(
            "Recovered interrupted recording" in record.getMessage()
            for record in caplog.records
            if record.levelno == logging.INFO
        )

    def test_sibling_fragments_of_one_session_merge_in_order(self, archive, monkeypatch):
        """Test numbered siblings of a session merge together, sorted, into one mp4."""
        prefix = "20260306_120000_"
        base = archive / f"{prefix}#Creator 2026-03-06 123.ts"
        sibling = archive / f"{prefix}#Creator 2026-03-06 123_1.ts"
        base.write_bytes(b"ts")
        sibling.write_bytes(b"ts")
        captured = []
        _fake_successful_merge(monkeypatch, captured)

        recovered = recover_orphaned_sessions(logging.getLogger("test_recovery"))

        assert recovered == 1
        assert captured == [([base, sibling], archive / "#Creator 2026-03-06 123.mp4")]
        assert not base.exists()
        assert not sibling.exists()

    def test_failed_merge_keeps_all_inputs_and_discards_partial_output(
        self, archive, monkeypatch, caplog
    ):
        """Test a failed recovery keeps every input and leaves no partial mp4."""
        prefix = "20260306_120000_"
        base = archive / f"{prefix}#Creator 2026-03-06 123.ts"
        sibling = archive / f"{prefix}#Creator 2026-03-06 123_1.ts"
        base.write_bytes(b"ts")
        sibling.write_bytes(b"ts")
        _fake_failing_merge(monkeypatch)

        caplog.set_level(logging.WARNING)
        recovered = recover_orphaned_sessions(logging.getLogger("test_recovery"))

        assert recovered == 0
        assert base.exists()
        assert sibling.exists()
        assert not (archive / "#Creator 2026-03-06 123.mp4").exists()
        assert any(
            "Orphan recovery merge failed" in record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING
        )

    def test_merge_timeout_keeps_inputs(self, archive, monkeypatch):
        """Test an ffmpeg timeout is a failure that preserves the recording."""
        ts_file = archive / "20260306_120000_#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        _fake_failing_merge(
            monkeypatch, error=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1)
        )

        recovered = recover_orphaned_sessions(logging.getLogger("test_recovery"))

        assert recovered == 0
        assert ts_file.exists()
        assert not (archive / "#Creator 2026-03-06 123.mp4").exists()

    def test_part_and_ytdl_files_are_never_touched(self, archive, monkeypatch):
        """Test in-flight yt-dlp artifacts are ignored and survive a mixed recovery."""
        prefix = "20260306_120000_"
        ts_file = archive / f"{prefix}#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        untouchable = {
            archive / f"{prefix}#Creator 2026-03-06 123.ts.part": b"part",
            archive / f"{prefix}#Creator 2026-03-06 123.ts.ytdl": b"ytdl",
            archive / f"{prefix}#Creator 2026-03-06 123.ts.part-Frag3": b"frag",
        }
        for path, payload in untouchable.items():
            path.write_bytes(payload)

        captured = []
        _fake_successful_merge(monkeypatch, captured)

        recovered = recover_orphaned_sessions(logging.getLogger("test_recovery"))

        assert recovered == 1
        # Only the .ts payload may ever reach the merge inputs.
        assert captured == [([ts_file], archive / "#Creator 2026-03-06 123.mp4")]
        for path, payload in untouchable.items():
            assert path.read_bytes() == payload

    @pytest.mark.parametrize(
        "filename",
        [
            "no-session-prefix.ts",
            "2026030_120000_#Creator short date.ts",
            "20260306_1200_#Creator short time.ts",
            "20260306120000_#Creator no separators.ts",
            "x20260306_120000_#Creator leading junk.ts",
        ],
    )
    def test_non_canonical_names_are_ignored(self, archive, monkeypatch, filename, caplog):
        """Test only the canonical YYYYMMDD_HHMMSS_ session pattern is recovered."""
        stray = archive / filename
        stray.write_bytes(b"ts")
        _fake_successful_merge(monkeypatch)

        caplog.set_level(logging.INFO)
        recovered = recover_orphaned_sessions(logging.getLogger("test_recovery"))

        assert recovered == 0
        assert stray.exists()
        assert list(archive.glob("*.mp4")) == []
        assert not caplog.records

    def test_existing_output_collision_is_skipped_and_inputs_kept(
        self, archive, monkeypatch, caplog
    ):
        """Test recovery never overwrites an existing mp4; it skips and keeps inputs."""
        ts_file = archive / "20260306_120000_#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        existing = archive / "#Creator 2026-03-06 123.mp4"
        existing.write_bytes(b"already merged")
        _fake_successful_merge(monkeypatch)

        caplog.set_level(logging.WARNING)
        recovered = recover_orphaned_sessions(logging.getLogger("test_recovery"))

        assert recovered == 0
        assert existing.read_bytes() == b"already merged"
        assert ts_file.exists()
        assert any(
            "already exists" in record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING
        )

    def test_second_run_after_failure_recovers_the_same_session(self, archive, monkeypatch):
        """Test a failed recovery stays retryable: the next startup merges it."""
        ts_file = archive / "20260306_120000_#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        logger = logging.getLogger("test_recovery")

        _fake_failing_merge(monkeypatch)
        assert recover_orphaned_sessions(logger) == 0
        # Filesystem is no worse than before: input intact, no partial artifact.
        assert ts_file.exists()
        assert list(archive.glob("*.mp4")) == []

        _fake_successful_merge(monkeypatch)
        assert recover_orphaned_sessions(logger) == 1
        assert (archive / "#Creator 2026-03-06 123.mp4").exists()
        assert not ts_file.exists()

    def test_successful_run_is_idempotent(self, archive, monkeypatch):
        """Test re-running recovery after success finds nothing left to do."""
        ts_file = archive / "20260306_120000_#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        _fake_successful_merge(monkeypatch)
        logger = logging.getLogger("test_recovery")

        assert recover_orphaned_sessions(logger) == 1
        merged = archive / "#Creator 2026-03-06 123.mp4"
        assert merged.read_bytes() == b"mp4"

        assert recover_orphaned_sessions(logger) == 0
        assert merged.read_bytes() == b"mp4"

    def test_cleanup_failure_keeps_the_merged_mp4(self, archive, monkeypatch, caplog):
        """Test a locked input after a good merge never discards the mp4."""
        ts_file = archive / "20260306_120000_#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        _fake_successful_merge(monkeypatch)

        real_unlink = Path.unlink

        def deny_ts_unlink(self, missing_ok=False):
            if self.suffix == ".ts":
                raise OSError("locked")
            return real_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", deny_ts_unlink)

        caplog.set_level(logging.INFO)
        recovered = recover_orphaned_sessions(logging.getLogger("test_recovery"))

        assert recovered == 1
        assert (archive / "#Creator 2026-03-06 123.mp4").exists()
        assert ts_file.exists()
        assert any(
            "could not remove" in record.getMessage() for record in caplog.records
        )

    def test_separate_sessions_and_creators_recover_independently(
        self, tmp_path, monkeypatch
    ):
        """Test each session merges to its own mp4 across creator directories."""
        monkeypatch.chdir(tmp_path)
        first = tmp_path / "archive" / "Creator"
        second = tmp_path / "archive" / "Other"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        (first / "20260306_120000_#Creator 2026-03-06 A.ts").write_bytes(b"ts")
        (first / "20260306_133000_#Creator 2026-03-06 B.ts").write_bytes(b"ts")
        (second / "20260306_140000_#Other 2026-03-06 C.ts").write_bytes(b"ts")
        _fake_successful_merge(monkeypatch)

        recovered = recover_orphaned_sessions(logging.getLogger("test_recovery"))

        assert recovered == 3
        assert (first / "#Creator 2026-03-06 A.mp4").exists()
        assert (first / "#Creator 2026-03-06 B.mp4").exists()
        assert (second / "#Other 2026-03-06 C.mp4").exists()
        assert list(first.glob("*.ts")) == []
        assert list(second.glob("*.ts")) == []

    def test_quiet_and_safe_when_nothing_to_recover(self, archive, caplog):
        """Test a clean archive logs nothing and reports no recoveries."""
        (archive / "#Creator 2026-03-06 done.mp4").write_bytes(b"complete")

        caplog.set_level(logging.DEBUG)
        recovered = recover_orphaned_sessions(logging.getLogger("test_recovery"))

        assert recovered == 0
        assert not caplog.records

    def test_missing_archive_directory_is_ignored(self, tmp_path, monkeypatch, caplog):
        """Test a first run with no archive directory is a quiet no-op."""
        monkeypatch.chdir(tmp_path)

        caplog.set_level(logging.DEBUG)

        assert recover_orphaned_sessions(logging.getLogger("test_recovery")) == 0
        assert not caplog.records

    def test_recovery_invokes_the_shared_ffmpeg_concat_command(self, archive, monkeypatch):
        """Test recovery reaches ffmpeg through the same concat command as the live merge.

        Exercises the real merge helper, stubbing only the subprocess call, so
        the argv and concat list are the ones ffmpeg would actually receive.
        """
        ts_file = archive / "20260306_120000_#Creator 2026-03-06 it's live.ts"
        ts_file.write_bytes(b"ts")
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            captured["list_content"] = Path(command[7]).read_text(encoding="utf-8")
            # Stand in for ffmpeg actually producing the output.
            Path(command[-1]).write_bytes(b"mp4")

        monkeypatch.setattr("core.orphan_recovery.subprocess.run", fake_run)

        assert recover_orphaned_sessions(logging.getLogger("test_recovery")) == 1

        assert captured["command"][:7] == [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i",
        ]
        assert captured["command"][8:10] == ["-c", "copy"]
        assert captured["command"][-1] == str(archive / "#Creator 2026-03-06 it's live.mp4")
        # Apostrophes must survive into the concat list, or ffmpeg reads a
        # truncated path and the recording is lost.
        assert r"it'\''s live.ts" in captured["list_content"]
        # Without check/timeout a wedged or failing ffmpeg would look successful
        # and the raw inputs would be deleted.
        assert captured["kwargs"]["check"] is True
        assert captured["kwargs"]["timeout"] > 0
        # The temporary concat list must not survive as a new leftover.
        assert not (archive / "merge-inputs.txt").exists()


class TestRecoveryFfmpegRunner:
    """The subprocess contract the recovery failure handling depends on."""

    def test_non_zero_exit_raises(self):
        """Test a failing merge child raises so inputs are never deleted."""
        with pytest.raises(subprocess.CalledProcessError):
            _run_recovery_ffmpeg([sys.executable, "-c", "raise SystemExit(1)"], 30)

    def test_timeout_raises(self):
        """Test a wedged merge child is killed and reported, not waited on forever."""
        with pytest.raises(subprocess.TimeoutExpired):
            _run_recovery_ffmpeg(
                [sys.executable, "-c", "import time; time.sleep(30)"], 1
            )
