"""
Startup recovery for raw .ts recordings whose merge never completed.

Two paths leave a finished recording unmerged on disk: a merge abandoned when
shutdown's aggregate budget ran out, and a raw download whose completion event
arrived after the merge executor had already closed. Both are logged at the
time, but nothing merges them afterwards, so the recording stays as .ts files
that no later run would ever look at again.

This module merges them at the next startup using the same ffmpeg concat
invocation the live merge executor uses, so a recovered recording is
byte-for-byte the artifact the interrupted run would have produced.
"""

import logging
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from core.constants import DEFAULT_MERGE_TIMEOUT_SECONDS
from core.downloader import StreamDownloader
from core.utils import merge_ts_files_to_mp4

__all__ = [
    "recover_orphaned_sessions",
]

# The canonical session prefix the downloader stamps onto every raw output:
# YYYYMMDD_HHMMSS_. Recovery is deliberately narrow — a .ts file without this
# prefix was not produced by a session this application can reconstruct, so it
# is left alone rather than guessed at.
_SESSION_PREFIX_RE = re.compile(r"^[0-9]{8}_[0-9]{6}_")

_TS_SUFFIX = ".ts"


def recover_orphaned_sessions(
    logger: logging.Logger,
    merge_timeout_seconds: int = DEFAULT_MERGE_TIMEOUT_SECONDS,
) -> int:
    """
    Merge every recoverable orphaned session under the archive directory.

    Runs synchronously at startup, before the scheduler polls, so recovery
    cannot race a fresh recording writing into the same creator directory.

    Single-instance assumption: at startup nothing else in this process is
    writing these files. A second concurrent instance pointed at the same
    volume is explicitly out of scope — it could observe a half-written mp4
    from the other instance's merge.

    Returns:
        The number of sessions merged successfully.
    """
    archive = Path.cwd() / StreamDownloader.ARCHIVE_DIR
    if not archive.is_dir():
        return 0

    sessions = _group_orphan_sessions(archive)
    recovered = 0
    for (output_dir, session_prefix), ts_files in sorted(sessions.items()):
        if _recover_one_session(
            logger, output_dir, session_prefix, ts_files, merge_timeout_seconds
        ):
            recovered += 1

    return recovered


def _group_orphan_sessions(archive: Path) -> Dict[Tuple[Path, str], List[Path]]:
    """
    Group orphaned .ts payloads by the session that produced them.

    Only ``*.ts`` is globbed, which is what keeps yt-dlp's in-flight artifacts
    out of recovery entirely: .part, .part-FragN and .ytdl all end in another
    suffix, so they can never enter a concat list nor a cleanup loop. Those may
    be truncated mid-write, and merging them would produce broken video.
    """
    sessions: Dict[Tuple[Path, str], List[Path]] = {}
    for ts_file in sorted(archive.glob(f"*/*{_TS_SUFFIX}")):
        match = _SESSION_PREFIX_RE.match(ts_file.name)
        if match is None:
            continue
        sessions.setdefault((ts_file.parent, match.group(0)), []).append(ts_file)

    return sessions


def _recover_one_session(
    logger: logging.Logger,
    output_dir: Path,
    session_prefix: str,
    ts_files: List[Path],
    merge_timeout_seconds: int,
) -> bool:
    """Merge one session's raw .ts files, returning True only on full success."""
    session_id = session_prefix.rstrip("_")

    # The live path builds the final name by dropping the session prefix from
    # the raw output name, so recovering it is the same subtraction. Deriving
    # it from the file on disk also keeps whatever title sanitization the
    # original run applied, which post-restart state can no longer supply.
    final_stem = ts_files[0].stem[len(session_prefix):]
    if not final_stem:
        logger.warning(
            f"⚠️ Skipping orphan recovery for session {session_id}: "
            f"{ts_files[0].name} has no name beyond its session prefix"
        )
        return False

    output_path = output_dir / f"{final_stem}.mp4"
    if output_path.exists():
        # Most likely this session already merged and only the .ts cleanup
        # failed (a locked input leaves the mp4 in place by design). Re-merging
        # would archive a duplicate of a finished recording, so the inputs are
        # left for an operator to compare and remove.
        logger.warning(
            f"⚠️ Skipping orphan recovery for session {session_id}: "
            f"{output_path.name} already exists. "
            f"Raw .ts files left in: {output_dir}"
        )
        return False

    try:
        merge_ts_files_to_mp4(
            ts_files,
            output_path,
            lambda command: _run_recovery_ffmpeg(command, merge_timeout_seconds),
        )
    except Exception as exc:
        # Same contract as the live merge: drop the half-written mp4 so a
        # failure cannot pass for a finished recording, and keep every input so
        # the next startup can attempt this session again unchanged.
        _discard_partial_output(logger, output_path)
        logger.warning(
            f"⚠️ Orphan recovery merge failed for session {session_id}: {exc}. "
            f"Raw .ts files left in: {output_dir}"
        )
        return False

    for ts_file in ts_files:
        try:
            ts_file.unlink(missing_ok=True)
        except OSError as cleanup_error:
            # The mp4 is the artifact of record from here; a locked input must
            # not undo a good merge. Startup's leftover scan will list it, and
            # the next run skips this session on the existing-output check.
            logger.warning(
                f"Merged, but could not remove {ts_file.name}: {cleanup_error}"
            )

    logger.info(
        f"🛟 Recovered interrupted recording (session {session_id}): "
        f"merged {len(ts_files)} raw file(s) into {output_path}"
    )
    return True


def _run_recovery_ffmpeg(command: List[str], timeout_seconds: int) -> None:
    """
    Run one recovery merge to completion.

    Unlike the monitor's merge child, this one needs no pid registration: no
    recording sweep runs at startup, so there is nothing to be spared from.

    Raises:
        subprocess.TimeoutExpired: If the merge outlives timeout_seconds
        subprocess.CalledProcessError: If ffmpeg exits non-zero
    """
    subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def _discard_partial_output(logger: logging.Logger, output_path: Path) -> None:
    """Drop a half-written mp4 so a failed recovery leaves no broken artifact."""
    try:
        output_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            f"Could not remove partial recovery output {output_path.name}: {exc}"
        )
