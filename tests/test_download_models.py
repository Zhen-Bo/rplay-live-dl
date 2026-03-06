"""Tests for session-aware download models."""

from datetime import datetime

from models.download import DownloadResult, DownloadSession, SessionState


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
            staging_dir=tmp_path,
        )

        assert session.final_output_path is None
        assert session.last_error is None


class TestDownloadResult:
    """Tests for DownloadResult."""

    def test_download_result_keeps_session_identity(self, tmp_path):
        """Test result payload preserves session information."""
        result = DownloadResult(
            session_key="creator1:2026-03-06T12:00:00",
            staging_dir=tmp_path,
            base_output_stem="#Creator 2026-03-06 Test",
            creator_name="Creator",
            title="Test",
            stream_start_time=datetime(2026, 3, 6, 12, 0, 0),
        )

        assert result.session_key.startswith("creator1:")

