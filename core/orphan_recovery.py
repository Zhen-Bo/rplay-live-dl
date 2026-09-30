"""Startup recovery for raw recordings whose merge never completed."""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from core.constants import DEFAULT_MERGE_TIMEOUT_SECONDS
from core.downloader import StreamDownloader
from core.utils import fit_filename_component_bytes, merge_ts_files_to_mp4

__all__ = [
    "install_merge_output_without_overwrite",
    "recover_orphaned_sessions",
]

# A .ts file without the downloader's YYYYMMDD_HHMMSS_ prefix cannot be
# reconstructed, so it is left alone.
_SESSION_PREFIX_RE = re.compile(r"^[0-9]{8}_[0-9]{6}_")


def recover_orphaned_sessions(
    logger: logging.Logger,
    *,
    reserve_gb: float = 1,
    space_multiplier: float = 2.2,
) -> None:
    """
    Merge every recoverable orphaned session under the archive directory.

    Runs before the scheduler polls, so it cannot race a fresh recording.
    A second concurrent instance on the same volume is unsupported.
    """
    archive = Path.cwd() / StreamDownloader.ARCHIVE_DIR

    # Adoption runs first so a claimed part joins the grouping below. Only the
    # exact *.ts.part suffix qualifies: .part-FragN and .ytdl may be torn
    # mid-write.
    for part_file in sorted(archive.glob("*/*.ts.part")):
        _adopt_orphaned_part(logger, part_file)

    sessions: Dict[Tuple[Path, str], List[Path]] = {}
    for ts_file in sorted(archive.glob("*/*.ts")):
        match = _SESSION_PREFIX_RE.match(ts_file.name)
        if match is None:
            continue
        sessions.setdefault((ts_file.parent, match.group(0)), []).append(ts_file)

    for (output_dir, session_prefix), ts_files in sorted(sessions.items()):
        _recover_one_session(
            logger,
            output_dir,
            session_prefix,
            ts_files,
            reserve_gb=reserve_gb,
            space_multiplier=space_multiplier,
        )


def _adopt_orphaned_part(logger: logging.Logger, part_file: Path) -> None:
    if _SESSION_PREFIX_RE.match(part_file.name) is None:
        return

    output_path = part_file.with_suffix("")
    if output_path.exists():
        # This part is from a different attempt and cannot be reconciled.
        # Overwriting would destroy a finished recording.
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
    *,
    reserve_gb: float = 1,
    space_multiplier: float = 2.2,
) -> None:
    """Merge one session's raw .ts files, deleting them only once the mp4 is proven."""
    session_id = session_prefix.rstrip("_")

    # Deriving the name from the file on disk keeps the title sanitization the
    # original run applied, which is lost after a restart.
    final_stem = ts_files[0].stem[len(session_prefix) :]
    if not final_stem:
        logger.warning(
            f"⚠️ Skipping orphan recovery for session {session_id}: "
            f"{ts_files[0].name} has no name beyond its session prefix"
        )
        return

    # A temp in the same directory keeps a death mid-merge from leaving a
    # partial mp4 under a final name. The marker is reserved before fitting so
    # long names stay within the filesystem's component byte limit.
    temp_seed = output_dir / f".{final_stem}.mp4"
    temp_path = fit_filename_component_bytes(temp_seed, appended_suffix=".recovering")
    try:
        try:
            # Stale bytes from a killed prior run must not pass the output
            # check below.
            temp_path.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"could not clear stale recovery output {temp_path.name}: {exc}"
            ) from exc

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
            reserve_gb=reserve_gb,
            space_multiplier=space_multiplier,
        )

        # The inputs are deleted on the strength of this check, so an empty
        # result must not count as success.
        if not temp_path.is_file() or temp_path.stat().st_size == 0:
            raise RuntimeError(f"merge produced no output at {temp_path.name}")

        # Same suffix policy as a live merge: a stop and restart mid-stream
        # yields two sessions with identical titles. The name is reserved at
        # install time because the merge can run for hours.
        output_path = install_merge_output_without_overwrite(
            logger, temp_path, output_dir / f"{final_stem}.mp4"
        )
    except Exception as exc:
        # Drop the partial output and keep every input so the next startup
        # can retry unchanged.
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
            # A locked input must not undo a good merge. The leftover .ts is
            # merged again later under the next free suffix.
            logger.warning(
                f"Merged, but could not remove {ts_file.name}: {cleanup_error}"
            )

    logger.info(
        f"🛟 Recovered interrupted recording (session {session_id}): "
        f"merged {len(ts_files)} raw file(s) into {output_path}"
    )


def install_merge_output_without_overwrite(
    logger: logging.Logger, temp_path: Path, base_path: Path
) -> Path:
    """Install a validated merge under the first free name, never clobbering one.

    A hardlink is an atomic no-overwrite install. Where hardlinks are rejected
    (exFAT, some CIFS mounts) the destination is created with ``O_EXCL`` and
    the bytes are copied in. A hard kill mid-copy can leave a partial
    destination under its claimed name. The raw inputs are retained and the
    next run picks the next free suffix.
    """
    hardlink_supported = True
    while True:
        candidate = StreamDownloader.get_unique_path(base_path)

        if hardlink_supported:
            try:
                # A claimant can win the gap between the availability check and
                # install. replace() would destroy it silently, os.link refuses.
                os.link(temp_path, candidate)
            except FileExistsError:
                continue
            except OSError as exc:
                # Errno mappings for unsupported hardlinks differ across
                # Windows, exFAT, and CIFS, so keep this fallback broad. A real
                # source or permission failure fails again in the exclusive copy.
                hardlink_supported = False
                logger.debug(
                    "Hardlink install unavailable for merge output "
                    f"{candidate.name}: {exc}; using exclusive copy"
                )
            else:
                _discard_partial_output(logger, temp_path)
                return candidate

        try:
            _copy_without_overwrite(logger, temp_path, candidate)
        except FileExistsError:
            # Name claimed after get_unique_path returned it.
            continue

        _discard_partial_output(logger, temp_path)
        return candidate


def _copy_without_overwrite(
    logger: logging.Logger, source_path: Path, destination_path: Path
) -> None:
    """Copy a validated output to a freshly created path without overwriting.

    ``shutil.copyfile`` truncates its destination, which would break the
    never-overwrite guarantee. ``O_EXCL`` reserves it atomically. A copy
    failure removes the reservation so the next startup can retry. SIGKILL
    skips that cleanup and leaves the name occupied.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    mode = source_path.stat().st_mode & 0o777
    destination_fd = os.open(destination_path, flags, mode)
    fd_open = True
    try:
        with source_path.open("rb") as source, os.fdopen(
            destination_fd, "wb", closefd=True
        ) as destination:
            fd_open = False
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        if fd_open:
            os.close(destination_fd)
        try:
            destination_path.unlink(missing_ok=True)
        except OSError as cleanup_error:
            # Preserve the original copy error. The name stays claimed, so a
            # later run picks another suffix.
            logger.warning(
                "Could not remove failed merge copy "
                f"{destination_path.name}: {cleanup_error}"
            )
        raise


def _discard_partial_output(logger: logging.Logger, temp_path: Path) -> None:
    try:
        temp_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"Could not remove partial merge output {temp_path.name}: {exc}")


# Kept for callers that imported the original private name.
_install_without_overwrite = install_merge_output_without_overwrite
