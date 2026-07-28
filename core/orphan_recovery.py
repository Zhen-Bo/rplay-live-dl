"""
Startup recovery for raw .ts recordings whose merge never completed.

A merge abandoned when shutdown's aggregate budget ran out, or a raw download
whose completion arrived after the merge executor had closed, leaves a finished
recording on disk as .ts files that no later run would look at again. This
merges them through the same ffmpeg concat invocation the live merge uses.
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
# YYYYMMDD_HHMMSS_. A .ts file without it was not produced by a session this
# application can reconstruct, so it is left alone rather than guessed at.
_SESSION_PREFIX_RE = re.compile(r"^[0-9]{8}_[0-9]{6}_")


def recover_orphaned_sessions(logger: logging.Logger) -> None:
    """
    Merge every recoverable orphaned session under the archive directory.

    Runs synchronously at startup, before the scheduler polls, so recovery
    cannot race a fresh recording writing into the same creator directory. A
    second concurrent instance on the same volume is out of scope: it could
    observe a half-written mp4 from the other instance's merge.
    """
    archive = Path.cwd() / StreamDownloader.ARCHIVE_DIR

    # Globbing *.ts alone is what keeps yt-dlp's in-flight artifacts out of
    # recovery entirely: .part, .part-FragN and .ytdl all end in another
    # suffix, so they can enter neither a concat list nor a cleanup loop. Those
    # may be truncated mid-write, and merging them would produce broken video.
    sessions: Dict[Tuple[Path, str], List[Path]] = {}
    for ts_file in sorted(archive.glob("*/*.ts")):
        match = _SESSION_PREFIX_RE.match(ts_file.name)
        if match is None:
            continue
        sessions.setdefault((ts_file.parent, match.group(0)), []).append(ts_file)

    for (output_dir, session_prefix), ts_files in sorted(sessions.items()):
        _recover_one_session(logger, output_dir, session_prefix, ts_files)


def _recover_one_session(
    logger: logging.Logger,
    output_dir: Path,
    session_prefix: str,
    ts_files: List[Path],
) -> None:
    """Merge one session's raw .ts files, deleting them only once the mp4 is proven."""
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
        return

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
        return

    # ffmpeg writes to a temporary name in the same directory, and only a
    # validated result is renamed onto the final one. A process death mid-merge
    # would otherwise leave a partial file under the final name, which the
    # collision check above reads as a finished recording — skipping that
    # session, and its intact inputs, on every later startup. The .mp4 suffix
    # is kept so ffmpeg still infers the mp4 muxer; a stale temp from an
    # interrupted attempt is overwritten by the invocation's -y.
    temp_path = output_path.with_name(f".{final_stem}.recovering.mp4")
    try:
        merge_ts_files_to_mp4(
            ts_files,
            temp_path,
            lambda command: subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=DEFAULT_MERGE_TIMEOUT_SECONDS,
            ),
        )

        # A merge callable that returns without producing anything must not
        # count as success: the inputs below are deleted on the strength of
        # this check, so the recording would be lost to an empty result.
        if not temp_path.is_file() or temp_path.stat().st_size == 0:
            raise RuntimeError(f"merge produced no output at {temp_path.name}")

        # Atomic within the directory: the final name never exists half-written.
        temp_path.replace(output_path)
    except Exception as exc:
        # Same contract as the live merge: drop the partial output so a failure
        # cannot pass for a finished recording, and keep every input so the
        # next startup can attempt this session again unchanged.
        _discard_partial_output(logger, temp_path)
        logger.warning(
            f"⚠️ Orphan recovery merge failed for session {session_id}: {exc}. "
            f"Raw .ts files left in: {output_dir}"
        )
        return

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


def _discard_partial_output(logger: logging.Logger, temp_path: Path) -> None:
    """Drop a half-written merge output, without letting a locked file raise."""
    try:
        temp_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            f"Could not remove partial recovery output {temp_path.name}: {exc}"
        )
