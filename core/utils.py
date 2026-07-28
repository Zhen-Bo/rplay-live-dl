"""
Utility functions for rplay-live-dl.

Provides common helper functions used across multiple modules.
"""

from typing import Optional

import psutil

__all__ = [
    "format_file_size",
    "terminate_child_processes",
]


def terminate_child_processes(
    timeout_seconds: float = 10.0,
    exclude_pid: Optional[int] = None,
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
        exclude_pid: The one child this caller owns deliberately — the merge
            ffmpeg, of which there is at most one because the merge executor
            runs a single worker. It is left alone, so shutdown can reap
            recordings without killing an active merge. ffmpeg spawns no
            children of its own, so the pid alone is the whole exclusion.
    """
    total = 0
    # A running download may spawn a child between passes, so re-scan once.
    # ponytail: one re-scan covers the single respawn yt-dlp does; hand the
    # sweep tracked child handles if recordings ever outrun two passes.
    for pass_timeout in (timeout_seconds, 2.0):
        try:
            children = [
                child
                for child in psutil.Process().children(recursive=True)
                if child.pid != exclude_pid
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
