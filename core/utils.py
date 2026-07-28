"""
Utility functions for rplay-live-dl.

Provides common helper functions used across multiple modules.
"""

from pathlib import Path
from typing import Callable, List, Optional

import psutil

__all__ = [
    "format_file_size",
    "merge_ts_files_to_mp4",
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


def _format_ffconcat_input_path(ts_file: Path) -> str:
    """Format one concat-demuxer input line with apostrophe-safe escaping."""
    escaped_path = ts_file.resolve().as_posix().replace("'", r"'\''")
    return f"file '{escaped_path}'"


def merge_ts_files_to_mp4(
    ts_files: List[Path],
    output_path: Path,
    run_command: Callable[[List[str]], None],
) -> None:
    """
    Merge ts fragments into one mp4 file using ffmpeg concat.

    Shared by the live merge executor and startup orphan recovery so both
    produce a recording through the same invocation. Only process supervision
    differs, which is why the caller supplies ``run_command``: the monitor
    registers the child pid under its state lock so shutdown can spare an
    active merge, while startup recovery has no sweep to protect against.

    Args:
        ts_files: Raw inputs, already ordered; the first one's parent holds the
            temporary concat list.
        output_path: Destination mp4, in an existing directory. Callers own
            collision policy and validation of the result.
        run_command: Executes the ffmpeg command. Must raise on non-zero exit
            (``CalledProcessError``) and on timeout (``TimeoutExpired``).
    """
    list_path = ts_files[0].parent / "merge-inputs.txt"
    list_content = "\n".join(_format_ffconcat_input_path(ts_file) for ts_file in ts_files)
    list_path.write_text(list_content, encoding="utf-8")

    try:
        run_command(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-c",
                "copy",
                str(output_path),
            ]
        )
    finally:
        list_path.unlink(missing_ok=True)


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
