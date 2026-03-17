"""Tests for session-aware download models."""

from datetime import datetime
from pathlib import Path

from models.download import (
    DownloadSession,
    MergeCompleted,
    MergeFailed,
    MergeJobSpec,
    RawDownloadBlocked,
    RawDownloadCompleted,
    SessionState,
)


class TestDownloadSession:
    """Tests for DownloadSession."""

    def test_download_session_defaults(self, tmp_path):
        """Test session defaults for optional fields."""
        session = DownloadSession(
            session_key="creator1:2026-03-06T12:00:00",
            creator_oid="creator1",
            creator_name="Creator",
            title="Test",
            stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
            state=SessionState.RAW_RUNNING,
            output_dir=tmp_path,
            session_prefix="20260306_120000",
        )

        assert session.final_output_path is None
        assert session.last_error is None


class TestSessionState:
    """Tests for SessionState."""

    def test_session_state_does_not_expose_unused_raw_failed(self):
        """Test the session state machine omits the unused RAW_FAILED state."""
        assert not hasattr(SessionState, "RAW_FAILED")


class TestMonitorEvents:
    """Tests for typed monitor events."""

    def test_raw_download_completed_preserves_session_identity(self, tmp_path):
        """Test raw completion event carries only the needed transport data."""
        event = RawDownloadCompleted(
            session_key="creator1:2026-03-06T12:00:00",
            output_dir=tmp_path,
        )

        assert event.session_key == "creator1:2026-03-06T12:00:00"
        assert event.output_dir == tmp_path

    def test_raw_download_blocked_carries_error_message(self):
        """Test blocked event preserves the emitted error message."""
        event = RawDownloadBlocked(
            session_key="creator1:2026-03-06T12:00:00",
            error_message="HTTP Error 404",
        )

        assert event.error_message == "HTTP Error 404"

    def test_merge_completed_stores_final_output_path(self, tmp_path):
        """Test merge completion event exposes the reserved final output path."""
        event = MergeCompleted(
            session_key="creator1:2026-03-06T12:00:00",
            output_path=tmp_path / "final.mp4",
        )

        assert event.output_path.name == "final.mp4"

    def test_merge_failed_has_no_failed_staging_dir(self):
        """Test merge failure event carries only session key and error message."""
        event = MergeFailed(
            session_key="creator1:2026-03-06T12:00:00",
            error_message="ffmpeg timeout",
        )

        assert event.error_message == "ffmpeg timeout"
        assert not hasattr(event, "failed_staging_dir")

    def test_merge_job_spec_groups_merge_inputs(self, tmp_path):
        """Test merge job inputs are grouped into one immutable spec."""
        spec = MergeJobSpec(
            session_key="creator1:2026-03-06T12:00:00",
            creator_name="Creator",
            title="Test",
            stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
            output_dir=tmp_path,
            session_prefix="20260306_120000",
        )

        assert spec.creator_name == "Creator"
        assert spec.output_dir == tmp_path
        assert spec.session_prefix == "20260306_120000"
