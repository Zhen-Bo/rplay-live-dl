"""
Utility functions for rplay-live-dl.

Provides common helper functions used across multiple modules.
"""

from typing import Iterable, Optional, Set

import psutil

__all__ = [
    "format_file_size",
    "terminate_child_processes",
]


def _expand_protected_pids(exclude_pids: Optional[Iterable[int]]) -> Set[int]:
    """Expand protected pids to cover their descendants at snapshot time."""
    protected = set(exclude_pids or ())
    for pid in list(protected):
        try:
            protected.update(
                child.pid for child in psutil.Process(pid).children(recursive=True)
            )
        except psutil.Error:
            continue
    return protected


def terminate_child_processes(
    timeout_seconds: float = 10.0,
    exclude_pids: Optional[Iterable[int]] = None,
) -> int:
    """
    Terminate child processes of this process, politely then firmly.

    yt-dlp downloads HLS through FFmpegFD, which spawns ffmpeg as a subprocess
    and keeps the Popen object in a local variable, so there is no handle to
    stop it through. Measured on shutdown: the recording ffmpeg processes
    survive the Python process and keep downloading indefinitely.

    Sends terminate to the whole child tree, waits, then kills whatever is
    still alive. Returns the number of children that had to be dealt with.

    Args:
        timeout_seconds: Grace period before killing survivors of the first pass
        exclude_pids: Children this caller owns deliberately (a running merge
            ffmpeg) plus their descendants. They are left completely alone, so
            shutdown can reap recordings without killing an active merge.
    """
    protected = _expand_protected_pids(exclude_pids)
    total = 0
    # A running download may spawn a child between passes, so re-scan once.
    for pass_timeout in (timeout_seconds, 2.0):
        try:
            children = [
                child
                for child in psutil.Process().children(recursive=True)
                if child.pid not in protected
            ]
        except psutil.Error:
            break
        if not children:
            break

        for child in children:
            try:
                child.terminate()
            except psutil.Error:
                # Already gone, or access denied: neither may stop the sweep.
                continue

        _, alive = psutil.wait_procs(children, timeout=pass_timeout)
        for child in alive:
            try:
                child.kill()
            except psutil.Error:
                continue

        total += len(children)

    return total


def format_file_size(size_bytes: float) -> str:
    """
    Format file size in human-readable format.

    Args:
        size_bytes: File size in bytes

    Returns:
        Human-readable size string (e.g., "1.5 GB")
    """
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"
