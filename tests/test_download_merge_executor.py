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

    def test_drain_waits_for_running_merge_and_stays_open(self):
        """Test drain flushes in-flight merges without closing the executor."""
        release = Event()
        finished = []
        executor = DownloadMergeExecutor(max_workers=1)

        def slow_merge():
            release.wait(timeout=2)
            finished.append("merge")

        executor.submit_merge(slow_merge)
        release.set()

        assert executor.drain(timeout=5) is True
        assert finished == ["merge"]

        # Staying open is the point: a recording stopped by shutdown still has
        # to be able to queue its merge after this drain.
        executor.submit_merge(lambda: finished.append("late")).result(timeout=5)
        executor.shutdown(wait=True)

        assert finished == ["merge", "late"]

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

    def test_drain_covers_merge_submitted_while_draining(self):
        """Test drain re-checks so a late submission is not left unflushed."""
        finished = []
        executor = DownloadMergeExecutor(max_workers=1)

        def first_merge():
            finished.append("first")
            executor.submit_merge(lambda: finished.append("second"))

        executor.submit_merge(first_merge)

        assert executor.drain(timeout=5) is True
        assert finished == ["first", "second"]
        executor.shutdown(wait=True)

    def test_drain_returns_immediately_when_nothing_pending(self):
        """Test drain on an idle executor is a no-op."""
        executor = DownloadMergeExecutor(max_workers=1)

        assert executor.drain(timeout=0) is True
        executor.shutdown(wait=True)
