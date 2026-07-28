"""Tests for the download merge executor."""

from threading import Event

import pytest

from core.download_merge_executor import DownloadMergeExecutor


class TestDownloadMergeExecutor:
    """Tests for DownloadMergeExecutor."""

    def test_submit_merge_runs_task(self):
        """Test a submitted merge task is executed."""
        events = []
        executor = DownloadMergeExecutor(max_workers=1)

        future = executor.submit_merge(lambda: events.append("done"))
        future.result(timeout=1)
        executor.shutdown(wait=True)

        assert events == ["done"]

    def test_shutdown_stops_accepting_new_tasks(self):
        """Test executor rejects new work after shutdown."""
        executor = DownloadMergeExecutor(max_workers=1)

        executor.shutdown(wait=False)

        with pytest.raises(RuntimeError):
            executor.submit_merge(lambda: None)

    def test_drain_waits_for_queued_merges_then_refuses_new_work(self):
        """Test the drain barrier flushes queued merges and closes acceptance."""
        release = Event()
        finished = []
        executor = DownloadMergeExecutor(max_workers=1)

        def slow_merge():
            release.wait(timeout=5)
            finished.append("merge")

        executor.submit_merge(slow_merge)
        release.set()

        # FIFO with one worker: the barrier cannot run before the merge queued
        # ahead of it, so True here means that merge is finished.
        assert executor.drain(timeout=5) is True
        assert finished == ["merge"]

        with pytest.raises(RuntimeError):
            executor.submit_merge(lambda: finished.append("late"))

        executor.shutdown(wait=True)
        assert finished == ["merge"]

    def test_drain_returns_false_when_merge_outlasts_timeout(self):
        """Test drain reports failure instead of hanging on a stuck merge."""
        release = Event()
        executor = DownloadMergeExecutor(max_workers=1)
        executor.submit_merge(lambda: release.wait(timeout=5))

        try:
            assert executor.drain(timeout=0.1) is False
        finally:
            release.set()
            executor.shutdown(wait=True)

    def test_drain_on_idle_executor_returns_true(self):
        """Test the barrier completes right away when no merge is queued."""
        executor = DownloadMergeExecutor(max_workers=1)

        assert executor.drain(timeout=5) is True
        executor.shutdown(wait=True)
