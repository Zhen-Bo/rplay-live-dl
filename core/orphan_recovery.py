"""
Startup recovery for raw recordings whose merge never completed.

A merge abandoned when shutdown's budget ran out, a raw download that finished
after the merge executor had closed, or a recording whose ffmpeg was reaped
before yt-dlp could rename its .ts.part all leave a recording on disk that no
later run would look at again. This merges them through the same ffmpeg concat
invocation the live merge uses, adopting a canonical NAME.ts.part first.
"""

import logging
import os
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

    # Adoption runs first, so a claimed part is just another .ts input to the
    # grouping below. Only the exact *.ts.part suffix qualifies, here and in
    # the *.ts glob: .part-FragN and .ytdl may be torn mid-write, and merging
    # them would produce broken video.
    for part_file in sorted(archive.glob("*/*.ts.part")):
        _adopt_orphaned_part(logger, part_file)

    sessions: Dict[Tuple[Path, str], List[Path]] = {}
    for ts_file in sorted(archive.glob("*/*.ts")):
        match = _SESSION_PREFIX_RE.match(ts_file.name)
        if match is None:
            continue
        sessions.setdefault((ts_file.parent, match.group(0)), []).append(ts_file)

    for (output_dir, session_prefix), ts_files in sorted(sessions.items()):
        _recover_one_session(logger, output_dir, session_prefix, ts_files)


def _adopt_orphaned_part(logger: logging.Logger, part_file: Path) -> None:
    """Rename one stranded NAME.ts.part onto NAME.ts so it can be merged."""
    if _SESSION_PREFIX_RE.match(part_file.name) is None:
        return

    output_path = part_file.with_suffix("")
    if output_path.exists():
        # The session already has raw output under that name, so this part is
        # from a different attempt whose content cannot be reconciled here.
        # Overwriting would destroy a finished recording; the operator decides.
        logger.warning(
            f"⚠️ Not adopting {part_file.name}: {output_path.name} already exists"
        )
        return

    try:
        if part_file.stat().st_size == 0:
            logger.warning(f"⚠️ Not adopting {part_file.name}: it is empty")
            return

        part_file.rename(output_path)
    except OSError as exc:
        logger.warning(f"⚠️ Could not adopt {part_file.name}: {exc}")
        return

    logger.info(f"🛟 Adopted interrupted download as raw output: {output_path.name}")


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

    # ffmpeg writes a temp in the same directory so a death mid-merge cannot
    # leave a partial mp4 under a final name, which the suffix policy below
    # would then read as a real recording. The .mp4 suffix keeps ffmpeg's muxer
    # inference; a stale temp from an interrupted attempt is overwritten by -y.
    temp_path = output_dir / f".{final_stem}.recovering.mp4"
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

        # Same suffix policy as a live merge: a taken name becomes NAME_1.mp4
        # rather than a skip, because one stop and restart during a stream
        # produces two sessions with identical titles in one directory.
        # Reserved at install time rather than before the merge, which can run
        # for hours.
        output_path = _install_without_overwrite(
            logger, temp_path, output_dir / f"{final_stem}.mp4"
        )
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
            # not undo a good merge. The leftover .ts is merged again on a
            # later run, under the next free suffix, so nothing is overwritten.
            logger.warning(
                f"Merged, but could not remove {ts_file.name}: {cleanup_error}"
            )

    logger.info(
        f"🛟 Recovered interrupted recording (session {session_id}): "
        f"merged {len(ts_files)} raw file(s) into {output_path}"
    )


def _install_without_overwrite(
    logger: logging.Logger, temp_path: Path, base_path: Path
) -> Path:
    """Install the merged mp4 under the first free name, never clobbering one."""
    while True:
        candidate = StreamDownloader.get_unique_path(base_path)
        try:
            # Availability and install are two steps, and a claimant can win
            # the gap between them — replace() would destroy it silently.
            # os.link refuses an existing target instead, so a lost race costs
            # one more suffix. Every collision leaves that name taken for good,
            # so the retries run out at get_unique_path's duplicate cap.
            os.link(temp_path, candidate)
        except FileExistsError:
            continue
        _discard_partial_output(logger, temp_path)
        return candidate


def _discard_partial_output(logger: logging.Logger, temp_path: Path) -> None:
    """Drop a half-written merge output, without letting a locked file raise."""
    try:
        temp_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            f"Could not remove partial recovery output {temp_path.name}: {exc}"
        )
