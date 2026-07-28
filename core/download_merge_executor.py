"""Dedicated executor for asynchronous merge jobs."""

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from threading import Lock
from typing import Callable, Optional


class DownloadMergeExecutor:
    """Small wrapper around ThreadPoolExecutor for merge tasks."""

    def __init__(self, max_workers: int = 1) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="merge")
        self._lock = Lock()
        self._closed = False

    def submit_merge(self, task: Callable[[], object]) -> Future:
        """
        Submit a merge task for asynchronous execution.

        Raises:
            RuntimeError: If acceptance was already closed by drain/shutdown.
                Callers must treat this as "too late to merge", not as a crash.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("merge executor is shut down")
            return self._executor.submit(task)

    def drain(self, timeout: Optional[float] = None) -> bool:
        """
        Close acceptance, then wait for already-queued merges to finish.

        The pool is FIFO with a single worker, so a no-op enqueued under the
        same lock that closes acceptance is necessarily the last task in the
        queue: once it runs, every merge submitted before it has finished.
        That barrier is what makes the wait bounded — the previous pending-set
        re-check could be extended indefinitely by late submissions.

        Returns:
            True when queued merges finished, False if the timeout ran out.
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
        """Stop accepting new work and shut down the executor."""
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)
