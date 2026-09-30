import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from core.recording_metadata import recording_metadata
from core.utils import merge_ts_files_to_mp4
from models.download import MergeJobSpec


def make_job(tmp_path):
    return MergeJobSpec(
        session_key="local-session",
        creator_name="創作者",
        title="原始 / 標題 ' = 測試",
        stream_start_time=datetime(
            2026, 9, 30, 20, tzinfo=timezone(timedelta(hours=8))
        ),
        output_dir=tmp_path,
        session_prefix="20260930_200000_",
        creator_oid="creator-1",
        stream_oid="live-2",
        recording_started_at=datetime(2026, 9, 30, 12, 1, tzinfo=timezone.utc),
    )


def test_metadata_preserves_original_title_and_utc_times(tmp_path):
    tags = recording_metadata(make_job(tmp_path))
    assert tags == {
        "title": "原始 / 標題 ' = 測試",
        "artist": "創作者",
        "rplay_metadata_version": "1",
        "rplay_creator_oid": "creator-1",
        "rplay_stream_oid": "live-2",
        "rplay_stream_start_time": "2026-09-30T12:00:00Z",
        "rplay_recording_started_at": "2026-09-30T12:01:00Z",
    }


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg tools not installed",
)
def test_real_mp4_roundtrip_preserves_custom_unicode_metadata(tmp_path):
    raw = tmp_path / "sample.ts"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=64x64:rate=10",
            "-t",
            "0.5",
            "-c:v",
            "mpeg2video",
            "-f",
            "mpegts",
            str(raw),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    output = tmp_path / "sample.mp4"
    tags = recording_metadata(make_job(tmp_path))
    merge_ts_files_to_mp4(
        [raw],
        output,
        lambda argv: subprocess.run(argv, check=True, capture_output=True, timeout=15),
        metadata=tags,
        reserve_gb=0,
    )
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format_tags",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    stored = json.loads(probe.stdout)["format"]["tags"]
    assert {key: stored[key] for key in tags} == tags
