"""Dedicated executor for asynchronous merge jobs."""

from concurrent.futures import Future, ThreadPoolExecutor, wait as wait_for_futures
from threading import Lock
from time import monotonic
from typing import Callable, Optional, Set


class DownloadMergeExecutor:
    """Small wrapper around ThreadPoolExecutor for merge tasks."""

    def __init__(self, max_workers: int = 1) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="merge")
        self._lock = Lock()
        self._shutdown = False
        self._pending: Set[Future] = set()

    def submit_merge(self, task: Callable[[], object]) -> Future:
        """Submit a merge task for asynchronous execution."""
        with self._lock:
            if self._shutdown:
                raise RuntimeError("merge executor is shut down")
            future = self._executor.submit(task)
            self._pending.add(future)

        # Outside the lock on purpose: an already-finished task runs this
        # callback inline, and self._lock is not reentrant.
        future.add_done_callback(self._forget_pending)
        return future

    def _forget_pending(self, future: Future) -> None:
        """Drop a finished merge from the pending set."""
        with self._lock:
            self._pending.discard(future)

    def drain(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for submitted merges to finish while staying open for new work.

        Shutdown needs this: closing the executor to flush it would reject the
        merges that recordings stopped by that same shutdown are about to
        submit. Re-checks after each wait because a late raw completion can
        still queue a merge while this drains.

        Returns:
            True when nothing is pending anymore, False if timeout ran out.
        """
        deadline = None if timeout is None else monotonic() + timeout
        while True:
            with self._lock:
                pending = set(self._pending)
            if not pending:
                return True
            if deadline is None:
                wait_for_futures(pending)
                continue
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            wait_for_futures(pending, timeout=remaining)

    def shutdown(self, wait: bool = False) -> None:
        """Stop accepting new work and shut down the executor."""
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
        self._executor.shutdown(wait=wait)
