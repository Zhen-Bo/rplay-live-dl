"""Tests for the download merge executor."""

import pytest

from core.download_merge_executor import DownloadMergeExecutor


class TestDownloadMergeExecutor:
    """Tests for DownloadMergeExecutor."""

    def test_submit_merge_runs_task(self):
        """Test a submitted merge task is executed."""
        events = []
        executor = DownloadMergeExecutor(max_workers=1)

        future = executor.submit_merge("session1", lambda: events.append("done"))
        future.result(timeout=1)
        executor.shutdown(wait=True)

        assert events == ["done"]

    def test_shutdown_stops_accepting_new_tasks(self):
        """Test executor rejects new work after shutdown."""
        executor = DownloadMergeExecutor(max_workers=1)

        executor.shutdown(wait=False)

        with pytest.raises(RuntimeError):
            executor.submit_merge("session1", lambda: None)

