"""Small, explicit allowlist of metadata stored with completed recordings."""

from datetime import datetime, timezone
from typing import Dict

from models.download import MergeJobSpec


def utc_timestamp(value: datetime) -> str:
    # API timestamps are UTC; older callers sometimes pass a naive datetime.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def recording_metadata(job: MergeJobSpec) -> Dict[str, str]:
    tags = {
        "title": job.title,
        "artist": job.creator_name,
        "rplay_metadata_version": "1",
        "rplay_stream_start_time": utc_timestamp(job.stream_start_time),
    }
    if job.creator_oid:
        tags["rplay_creator_oid"] = job.creator_oid
    if job.stream_oid:
        tags["rplay_stream_oid"] = job.stream_oid
    if job.recording_started_at is not None:
        tags["rplay_recording_started_at"] = utc_timestamp(job.recording_started_at)
    return tags
