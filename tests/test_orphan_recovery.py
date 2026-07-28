"""Tests for startup recovery of orphaned .ts recordings."""

import logging
import subprocess
from pathlib import Path

import pytest

from core.orphan_recovery import recover_orphaned_sessions

LOGGER = logging.getLogger("test_recovery")


@pytest.fixture
def archive(tmp_path, monkeypatch):
    """Point recovery at a temporary archive/Creator directory."""
    monkeypatch.chdir(tmp_path)
    creator_dir = tmp_path / "archive" / "Creator"
    creator_dir.mkdir(parents=True)
    return creator_dir


def _fake_merge(monkeypatch, *, writes=b"mp4", error=None, captured=None):
    """
    Replace the ffmpeg seam with a writer that never spawns a process.

    ``writes=None`` stands in for a merge that returns without producing
    anything, ``writes=b""`` for one that produces an empty file.
    """

    def fake_merge(ts_files, output_path, run_command):
        if captured is not None:
            captured.append((list(ts_files), output_path))
        if writes is not None:
            output_path.write_bytes(writes)
        if error is not None:
            raise error

    monkeypatch.setattr("core.orphan_recovery.merge_ts_files_to_mp4", fake_merge)


class TestOrphanRecovery:
    """Recovery of session .ts files left behind by an interrupted run."""

    def test_session_fragments_merge_in_order_and_inputs_are_deleted(
        self, archive, monkeypatch
    ):
        """Test one session's numbered fragments merge, sorted, into one mp4."""
        prefix = "20260306_120000_"
        base = archive / f"{prefix}#Creator 2026-03-06 123.ts"
        sibling = archive / f"{prefix}#Creator 2026-03-06 123_1.ts"
        base.write_bytes(b"ts")
        sibling.write_bytes(b"ts")
        captured = []
        _fake_merge(monkeypatch, captured=captured)

        recover_orphaned_sessions(LOGGER)

        assert (archive / "#Creator 2026-03-06 123.mp4").read_bytes() == b"mp4"
        assert [ts_files for ts_files, _ in captured] == [[base, sibling]]
        assert not base.exists()
        assert not sibling.exists()

    def test_failed_merge_keeps_every_input_and_the_next_run_recovers(
        self, archive, monkeypatch
    ):
        """Test a failed merge leaves the session retryable and untouched."""
        prefix = "20260306_120000_"
        base = archive / f"{prefix}#Creator 2026-03-06 123.ts"
        sibling = archive / f"{prefix}#Creator 2026-03-06 123_1.ts"
        base.write_bytes(b"ts")
        sibling.write_bytes(b"ts")
        _fake_merge(
            monkeypatch,
            writes=b"partial",
            error=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1),
        )

        recover_orphaned_sessions(LOGGER)

        # Nothing but the inputs may survive a failure — no partial artifact
        # under any name, or the next run has a broken recording to reason about.
        assert sorted(archive.iterdir()) == [base, sibling]

        _fake_merge(monkeypatch)
        recover_orphaned_sessions(LOGGER)

        assert sorted(archive.iterdir()) == [archive / "#Creator 2026-03-06 123.mp4"]

    def test_merge_producing_no_output_keeps_the_inputs(self, archive, monkeypatch):
        """Test a merge that returns without writing anything is not a success."""
        ts_file = archive / "20260306_120000_#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        _fake_merge(monkeypatch, writes=None)

        recover_orphaned_sessions(LOGGER)

        assert list(archive.iterdir()) == [ts_file]

    def test_merge_producing_an_empty_output_keeps_the_inputs(
        self, archive, monkeypatch
    ):
        """Test a zero-byte result is not a recording, so the inputs stay."""
        ts_file = archive / "20260306_120000_#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        _fake_merge(monkeypatch, writes=b"")

        recover_orphaned_sessions(LOGGER)

        assert list(archive.iterdir()) == [ts_file]

    def test_interrupted_merge_leaves_the_final_name_free_for_the_next_run(
        self, archive, monkeypatch
    ):
        """Test a merge killed mid-write leaves a stale temp the next run overwrites."""
        ts_file = archive / "20260306_120000_#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        final_path = archive / "#Creator 2026-03-06 123.mp4"
        captured = []
        _fake_merge(
            monkeypatch, writes=b"partial", error=RuntimeError("killed"), captured=captured
        )

        recover_orphaned_sessions(LOGGER)

        # ffmpeg must never write the collision-significant final name directly:
        # a partial left there is read as a finished recording, and the session
        # is skipped on every later startup instead of being recovered.
        (_, temp_path), = captured
        assert temp_path != final_path

        # A killed process runs no cleanup, so its partial temp survives.
        temp_path.write_bytes(b"partial from a killed process")
        _fake_merge(monkeypatch)

        recover_orphaned_sessions(LOGGER)

        assert final_path.read_bytes() == b"mp4"
        assert list(archive.iterdir()) == [final_path]

    def test_existing_output_gets_the_next_suffix_and_is_never_overwritten(
        self, archive, monkeypatch
    ):
        """Test a taken mp4 name pushes this session to _1 instead of skipping it."""
        ts_file = archive / "20260306_120000_#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        existing = archive / "#Creator 2026-03-06 123.mp4"
        existing.write_bytes(b"already merged")
        _fake_merge(monkeypatch)

        recover_orphaned_sessions(LOGGER)

        # The earlier recording is byte-identical afterwards: reserving a free
        # name is what keeps the rename from overwriting it.
        assert existing.read_bytes() == b"already merged"
        assert (archive / "#Creator 2026-03-06 123_1.mp4").read_bytes() == b"mp4"
        assert not ts_file.exists()

    def test_a_name_taken_while_the_merge_runs_is_not_overwritten(
        self, archive, monkeypatch
    ):
        """Test the final name is reserved at rename time, not before the merge.

        A concat can run for hours, so a name that was free when it started may
        be taken by the time it finishes — and the rename overwrites silently.
        """
        ts_file = archive / "20260306_120000_#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        final_path = archive / "#Creator 2026-03-06 123.mp4"

        def merge_then_lose_the_name(ts_files, output_path, run_command):
            output_path.write_bytes(b"mp4")
            # The name goes from free to taken while ffmpeg is still busy.
            final_path.write_bytes(b"claimed mid-merge")

        monkeypatch.setattr(
            "core.orphan_recovery.merge_ts_files_to_mp4", merge_then_lose_the_name
        )

        recover_orphaned_sessions(LOGGER)

        assert final_path.read_bytes() == b"claimed mid-merge"
        assert (archive / "#Creator 2026-03-06 123_1.mp4").read_bytes() == b"mp4"

    def test_two_stranded_sessions_with_one_title_both_reach_their_own_mp4(
        self, archive, monkeypatch
    ):
        """Test the stop-and-restart case: same title, two sessions, two recordings kept.

        This is the shape of every docker-stop during a live stream, and the
        case a skip-on-collision policy stranded permanently.
        """
        first = archive / "20260728_200507_#Creator 2026-07-28 Live.ts"
        second = archive / "20260728_200831_#Creator 2026-07-28 Live.ts"
        first.write_bytes(b"first session")
        second.write_bytes(b"second session")

        def fake_concat(ts_files, output_path, run_command):
            output_path.write_bytes(b"".join(f.read_bytes() for f in ts_files))

        monkeypatch.setattr("core.orphan_recovery.merge_ts_files_to_mp4", fake_concat)

        recover_orphaned_sessions(LOGGER)

        # Each session's payload reaches its own file, oldest session first.
        assert (archive / "#Creator 2026-07-28 Live.mp4").read_bytes() == b"first session"
        assert (
            archive / "#Creator 2026-07-28 Live_1.mp4"
        ).read_bytes() == b"second session"
        assert list(archive.glob("*.ts")) == []

    def test_only_the_ts_part_is_adopted_out_of_a_mixed_artifact_directory(
        self, archive, monkeypatch
    ):
        """Test a stranded .ts.part is adopted and merged while .part-FragN and .ytdl are not.

        Pins the adoption invariant by suffix: exactly NAME.ts.part qualifies.
        Fragments and .ytdl may be torn mid-write, and a bare .part is not a
        recording this application can name a session from.
        """
        prefix = "20260306_120000_"
        adoptable = archive / f"{prefix}#Creator 2026-03-06 123.ts.part"
        adoptable.write_bytes(b"truncated but demuxable ts")
        untouchable = {
            archive / f"{prefix}#Creator 2026-03-06 123.ts.ytdl": b"ytdl",
            archive / f"{prefix}#Creator 2026-03-06 123.ts.part-Frag3": b"frag",
            archive / f"{prefix}#Creator 2026-03-06 456.part": b"bare part",
        }
        for path, payload in untouchable.items():
            path.write_bytes(payload)

        captured = []
        _fake_merge(monkeypatch, captured=captured)

        recover_orphaned_sessions(LOGGER)

        # The part is now the session's raw input, under its .ts name.
        adopted = archive / f"{prefix}#Creator 2026-03-06 123.ts"
        assert [ts_files for ts_files, _ in captured] == [[adopted]]
        assert (archive / "#Creator 2026-03-06 123.mp4").read_bytes() == b"mp4"
        assert not adoptable.exists()
        for path, payload in untouchable.items():
            assert path.read_bytes() == payload

    def test_part_is_left_alone_when_its_ts_name_is_already_taken(
        self, archive, monkeypatch
    ):
        """Test adoption never overwrites existing raw output for the same session."""
        prefix = "20260306_120000_"
        ts_file = archive / f"{prefix}#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"finished raw output")
        part_file = archive / f"{prefix}#Creator 2026-03-06 123.ts.part"
        part_file.write_bytes(b"a different attempt")
        captured = []
        _fake_merge(monkeypatch, captured=captured)

        recover_orphaned_sessions(LOGGER)

        # Only the existing .ts is merged; the part survives for an operator.
        assert [ts_files for ts_files, _ in captured] == [[ts_file]]
        assert part_file.read_bytes() == b"a different attempt"

    def test_empty_part_is_not_adopted(self, archive, monkeypatch):
        """Test a zero-byte part holds no recording, so it is left as it is."""
        part_file = archive / "20260306_120000_#Creator 2026-03-06 123.ts.part"
        part_file.write_bytes(b"")
        captured = []
        _fake_merge(monkeypatch, captured=captured)

        recover_orphaned_sessions(LOGGER)

        assert captured == []
        assert list(archive.iterdir()) == [part_file]

    def test_adopted_part_stays_retryable_when_its_merge_fails(
        self, archive, monkeypatch
    ):
        """Test adoption is not undone by a failed merge: the next run recovers it."""
        prefix = "20260306_120000_"
        part_file = archive / f"{prefix}#Creator 2026-03-06 123.ts.part"
        part_file.write_bytes(b"truncated but demuxable ts")
        adopted = archive / f"{prefix}#Creator 2026-03-06 123.ts"
        _fake_merge(monkeypatch, writes=b"partial", error=RuntimeError("boom"))

        recover_orphaned_sessions(LOGGER)

        # Adoption is one-way: the input is kept under its .ts name, not
        # reverted, so the retry below is the ordinary .ts recovery path.
        assert list(archive.iterdir()) == [adopted]

        _fake_merge(monkeypatch)
        recover_orphaned_sessions(LOGGER)

        assert sorted(archive.iterdir()) == [archive / "#Creator 2026-03-06 123.mp4"]

    @pytest.mark.parametrize(
        "filename",
        [
            "no-session-prefix.ts",
            "2026030_120000_#Creator short date.ts",
            "20260306_1200_#Creator short time.ts",
            "20260306120000_#Creator no separators.ts",
            "x20260306_120000_#Creator leading junk.ts",
            "no-session-prefix.ts.part",
            "x20260306_120000_#Creator leading junk.ts.part",
        ],
    )
    def test_non_canonical_names_are_ignored(self, archive, monkeypatch, filename, caplog):
        """Test only the canonical YYYYMMDD_HHMMSS_ session pattern is recovered."""
        stray = archive / filename
        stray.write_bytes(b"ts")
        _fake_merge(monkeypatch)

        caplog.set_level(logging.DEBUG)
        recover_orphaned_sessions(LOGGER)

        assert list(archive.iterdir()) == [stray]
        assert not caplog.records

    def test_missing_archive_directory_is_ignored(self, tmp_path, monkeypatch, caplog):
        """Test a first run with no archive directory is a quiet no-op."""
        monkeypatch.chdir(tmp_path)

        caplog.set_level(logging.DEBUG)
        recover_orphaned_sessions(LOGGER)

        assert not caplog.records

    def test_cleanup_failure_keeps_the_merged_mp4(self, archive, monkeypatch):
        """Test a locked input after a good merge never discards the mp4."""
        ts_file = archive / "20260306_120000_#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        _fake_merge(monkeypatch)

        real_unlink = Path.unlink

        def deny_ts_unlink(self, missing_ok=False):
            if self.suffix == ".ts":
                raise OSError("locked")
            return real_unlink(self, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", deny_ts_unlink)

        recover_orphaned_sessions(LOGGER)

        assert (archive / "#Creator 2026-03-06 123.mp4").exists()
        assert ts_file.exists()

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
        _fake_merge(monkeypatch)

        recover_orphaned_sessions(LOGGER)

        assert sorted(first.iterdir()) == [
            first / "#Creator 2026-03-06 A.mp4",
            first / "#Creator 2026-03-06 B.mp4",
        ]
        assert list(second.iterdir()) == [second / "#Other 2026-03-06 C.mp4"]

    def test_ffmpeg_is_run_with_check_and_a_timeout_on_a_mp4_target(
        self, archive, monkeypatch
    ):
        """Test recovery reaches ffmpeg through the shared helper, bounded and checked.

        Stubs only the subprocess call, so the kwargs and output target are the
        ones ffmpeg would actually be given.
        """
        ts_file = archive / "20260306_120000_#Creator 2026-03-06 123.ts"
        ts_file.write_bytes(b"ts")
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            # Stand in for ffmpeg actually producing the output.
            Path(command[-1]).write_bytes(b"mp4")

        monkeypatch.setattr("core.orphan_recovery.subprocess.run", fake_run)

        recover_orphaned_sessions(LOGGER)

        assert list(archive.iterdir()) == [archive / "#Creator 2026-03-06 123.mp4"]
        # Without check/timeout a wedged or failing ffmpeg would look successful.
        assert captured["kwargs"]["check"] is True
        assert captured["kwargs"]["timeout"] > 0
        # The temporary target keeps the suffix ffmpeg infers the muxer from.
        assert captured["command"][-1].endswith(".mp4")
