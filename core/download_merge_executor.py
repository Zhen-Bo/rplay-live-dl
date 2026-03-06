"""Dedicated executor for asynchronous merge jobs."""

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Callable, Optional


class DownloadMergeExecutor:
    """Small wrapper around ThreadPoolExecutor for merge tasks."""

    def __init__(self, max_workers: int = 1) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="merge")
        self._lock = Lock()
        self._shutdown = False

    def submit_merge(self, session_key: str, task: Callable[[], object]) -> Future:
        """Submit a merge task for asynchronous execution."""
        del session_key

        with self._lock:
            if self._shutdown:
                raise RuntimeError("merge executor is shut down")
            return self._executor.submit(task)

    def shutdown(self, wait: bool = False) -> None:
        """Stop accepting new work and shut down the executor."""
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
        self._executor.shutdown(wait=wait)

