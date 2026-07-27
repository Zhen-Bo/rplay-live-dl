"""
Utility functions for rplay-live-dl.

Provides common helper functions used across multiple modules.
"""

import psutil

__all__ = [
    "format_file_size",
    "terminate_child_processes",
]


def terminate_child_processes(timeout_seconds: float = 10.0) -> int:
    """
    Terminate every child process of this process, politely then firmly.

    yt-dlp downloads HLS through FFmpegFD, which spawns ffmpeg as a subprocess
    and keeps the Popen object in a local variable, so there is no handle to
    stop it through. Measured on shutdown: the recording ffmpeg processes
    survive the Python process and keep downloading indefinitely.

    Sends terminate to the whole child tree, waits, then kills whatever is
    still alive. Returns the number of children that had to be dealt with.
    """
    try:
        children = psutil.Process().children(recursive=True)
    except psutil.Error:
        return 0

    for child in children:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            continue

    _, alive = psutil.wait_procs(children, timeout=timeout_seconds)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            continue

    return len(children)


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
