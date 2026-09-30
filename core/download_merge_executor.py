"""Dedicated executor for asynchronous merge jobs."""

from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from threading import Lock
from typing import Callable, Optional


class DownloadMergeExecutor:
    """Small wrapper around ThreadPoolExecutor for merge tasks."""

    def __init__(self, max_workers: int = 1) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="merge"
        )
        self._lock = Lock()
        self._closed = False

    def submit_merge(self, task: Callable[[], object]) -> Future:
        """
        Raise RuntimeError once drain or shutdown closed acceptance.

        Callers must treat this as "too late to merge", not as a crash.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("merge executor is shut down")
            return self._executor.submit(task)

    def drain(self, timeout: Optional[float] = None) -> bool:
        """
        Close acceptance, then wait for queued merges. Returns False on timeout.

        The pool is FIFO with one worker, so a no-op enqueued under the lock that
        closes acceptance is the last task in the queue. Once it runs, every earlier
        merge has finished. This keeps the wait bounded, unlike re-checking a pending
        set that late submissions could extend forever.
        """
        with self._lock:
            self._closed = True
            barrier = self._executor.submit(lambda: None)

        try:
            barrier.result(timeout=timeout)
            return True
        except FutureTimeoutError:
            return False

    def shutdown(self, wait: bool = False, cancel_futures: bool = False) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)
