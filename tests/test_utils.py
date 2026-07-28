"""Tests for utility functions."""

import subprocess
import sys
from pathlib import Path

import pytest

from core.utils import format_file_size, merge_ts_files_to_mp4, terminate_child_processes


class TestFormatFileSize:
    """Tests for format_file_size function."""

    @pytest.mark.parametrize(
        "size_bytes,expected",
        [
            # Zero
            (0, "0.0 B"),
            # Bytes range (0-1023)
            (1, "1.0 B"),
            (512, "512.0 B"),
            (1023, "1023.0 B"),
            # Kilobytes range
            (1024, "1.0 KB"),
            (1536, "1.5 KB"),
            (10240, "10.0 KB"),
            (1024 * 1023, "1023.0 KB"),
            # Megabytes range
            (1024 * 1024, "1.0 MB"),
            (1024 * 1024 * 5, "5.0 MB"),
            (int(1024 * 1024 * 2.5), "2.5 MB"),
            # Gigabytes range
            (1024 * 1024 * 1024, "1.0 GB"),
            (1024 * 1024 * 1024 * 4, "4.0 GB"),
            (int(1024 * 1024 * 1024 * 1.5), "1.5 GB"),
            # Terabytes range
            (1024 * 1024 * 1024 * 1024, "1.0 TB"),
        ],
        ids=[
            "zero_bytes",
            "1_byte",
            "512_bytes",
            "1023_bytes",
            "1_KB",
            "1.5_KB",
            "10_KB",
            "1023_KB",
            "1_MB",
            "5_MB",
            "2.5_MB",
            "1_GB",
            "4_GB",
            "1.5_GB",
            "1_TB",
        ],
    )
    def test_format_file_size(self, size_bytes: int, expected: str):
        """Test formatting file sizes across all unit ranges."""
        assert format_file_size(size_bytes) == expected


class TestMergeTsFilesToMp4:
    """The ffmpeg concat contract shared by the live merge and startup recovery."""

    def test_builds_the_concat_command_and_removes_its_list(self, tmp_path):
        """Test the argv ffmpeg receives, the concat list it reads, and its cleanup."""
        ts_file = tmp_path / "#Creator 2026-03-06 it's live.ts"
        ts_file.write_bytes(b"ts")
        output_path = tmp_path / "final.mp4"
        list_path = tmp_path / "merge-inputs.txt"
        captured = {}

        def fake_run(command):
            captured["command"] = command
            captured["list_content"] = Path(command[7]).read_text(encoding="utf-8")

        merge_ts_files_to_mp4([ts_file], output_path, fake_run)

        assert captured["command"] == [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c", "copy", str(output_path),
        ]
        # Apostrophes must survive into the concat list, or ffmpeg reads a
        # truncated path and the recording is lost.
        assert captured["list_content"].startswith("file '")
        assert r"it'\''s live.ts" in captured["list_content"]
        # The list must not survive as a new leftover of its own.
        assert not list_path.exists()


class TestTerminateChildProcesses:
    """Tests for terminating child processes."""

    def test_returns_zero_when_no_children(self):
        """Test no-child cleanup returns zero."""
        assert terminate_child_processes(timeout_seconds=1.0) == 0

    def test_excluded_child_survives_the_sweep(self):
        """Test a protected pid is left running while everything else is reaped."""
        protected = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        doomed = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            reaped = terminate_child_processes(
                timeout_seconds=5.0, exclude_pid=protected.pid
            )

            # This is the merge-vs-recording discrimination: shutdown reaps the
            # recording ffmpeg while an active merge ffmpeg keeps running.
            assert reaped >= 1
            doomed.wait(timeout=5)
            assert protected.poll() is None
        finally:
            for proc in (protected, doomed):
                try:
                    proc.kill()
                except OSError:
                    pass

    def test_terminates_a_real_child_process(self):
        """Test a real child process is terminated and reaped."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            assert proc.poll() is None
            assert terminate_child_processes(timeout_seconds=5.0) >= 1
            proc.wait(timeout=5)
            assert proc.returncode is not None
        finally:
            try:
                proc.kill()
            except OSError:
                pass
