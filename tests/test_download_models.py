"""Tests for session-aware download models."""

from models.download import MergeFailed, SessionState


class TestSessionState:
    """Tests for SessionState."""

    def test_session_state_does_not_expose_unused_raw_failed(self):
        """Test the session state machine omits the unused RAW_FAILED state."""
        assert not hasattr(SessionState, "RAW_FAILED")


class TestMonitorEvents:
    """Tests for typed monitor events."""

    def test_merge_failed_has_no_failed_staging_dir(self):
        """Test merge failure event carries only session key and error message."""
        event = MergeFailed(
            session_key="creator1:2026-03-06T12:00:00",
            error_message="ffmpeg timeout",
        )

        assert event.error_message == "ffmpeg timeout"
        assert not hasattr(event, "failed_staging_dir")
