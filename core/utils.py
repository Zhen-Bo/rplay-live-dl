"""Shared helpers for rplay-live-dl."""

from pathlib import Path
from typing import Callable, List, Optional

import psutil

__all__ = [
    "MAX_FILENAME_COMPONENT_BYTES",
    "format_file_size",
    "fit_filename_component_bytes",
    "merge_ts_files_to_mp4",
    "terminate_child_processes",
]


MAX_FILENAME_COMPONENT_BYTES = 255


def _truncate_utf8(value: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def fit_filename_component_bytes(
    path: Path,
    appended_suffix: str = "",
    max_bytes: int = MAX_FILENAME_COMPONENT_BYTES,
) -> Path:
    """``appended_suffix`` is reserved space so a collision suffix like ``_1`` is never cut off."""
    extension = path.suffix
    stem = path.name[: -len(extension)] if extension else path.name
    reserved_bytes = len((appended_suffix + extension).encode("utf-8"))
    stem = _truncate_utf8(stem, max_bytes - reserved_bytes)
    return path.parent / f"{stem}{appended_suffix}{extension}"


def terminate_child_processes(
    timeout_seconds: float = 10.0,
    exclude_pid: Optional[int] = None,
) -> int:
    """
    Terminate child processes of this process, then kill survivors.

    yt-dlp runs ffmpeg through FFmpegFD and keeps no handle to it, so recording
    ffmpeg processes survive the Python process and keep downloading forever.
    Returns the number of children handled.

    ``exclude_pid`` is the merge ffmpeg (at most one) and its descendants,
    which are left alone so an active merge survives the sweep.
    """
    total = 0
    # A running download may spawn a child between passes, so re-scan once.
    for pass_timeout in (timeout_seconds, 2.0):
        try:
            excluded_pids = {exclude_pid} if exclude_pid is not None else set()
            if exclude_pid is not None:
                excluded_pids.update(
                    child.pid
                    for child in psutil.Process(exclude_pid).children(recursive=True)
                )
            children = [
                child
                for child in psutil.Process().children(recursive=True)
                if child.pid not in excluded_pids
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
    escaped_path = ts_file.resolve().as_posix().replace("'", r"'\''")
    return f"file '{escaped_path}'"


def merge_ts_files_to_mp4(
    ts_files: List[Path],
    output_path: Path,
    run_command: Callable[[List[str]], object],
) -> None:
    """
    Merge ts fragments into one mp4 with ffmpeg concat.

    The caller supplies ``run_command`` so the monitor can register the child
    pid under its state lock and shutdown can spare an active merge. It must
    raise ``CalledProcessError`` on non-zero exit and ``TimeoutExpired`` on timeout.
    Callers own collision policy and validation of the result.
    """
    list_path = ts_files[0].parent / "merge-inputs.txt"
    list_content = "\n".join(
        _format_ffconcat_input_path(ts_file) for ts_file in ts_files
    )
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
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"
