"""Tests for data models."""

import pytest
from pydantic import ValidationError

from models.config import CreatorProfile
from models.env import EnvConfig
from models.rplay import StreamState


class TestCreatorProfile:
    """Tests for CreatorProfile model."""

    def test_creator_name_whitespace_stripped(self):
        """Test that creator name whitespace is stripped."""
        profile = CreatorProfile(
            creator_name="  Test Creator  ",
            creator_oid="abc123",
        )
        assert profile.creator_name == "Test Creator"

    def test_creator_oid_whitespace_stripped(self):
        """Test that creator OID whitespace is stripped."""
        profile = CreatorProfile(
            creator_name="Test",
            creator_oid="  abc123  ",
        )
        assert profile.creator_oid == "abc123"

    @pytest.mark.parametrize("name", ["", "   "])
    def test_empty_creator_name_rejected(self, name):
        """Test that empty or whitespace-only creator name is rejected."""
        with pytest.raises(ValidationError):
            CreatorProfile(
                creator_name=name,
                creator_oid="abc123",
            )

    def test_empty_creator_oid_rejected(self):
        """Test that empty creator OID is rejected."""
        with pytest.raises(ValidationError):
            CreatorProfile(
                creator_name="Test",
                creator_oid="",
            )

    def test_string_representation(self):
        """Test string representation of creator profile."""
        profile = CreatorProfile(
            creator_name="Test Creator",
            creator_oid="abc123",
        )
        assert "Test Creator" in str(profile)
        assert "abc123" in str(profile)


class TestEnvConfig:
    """Tests for EnvConfig model."""

    @pytest.mark.parametrize("interval", [5, 4000])
    def test_interval_minimum(self, interval):
        """Test that interval must stay within its allowed range."""
        with pytest.raises(ValidationError):
            EnvConfig(user_oid="user456", refresh_token="token123", interval=interval)

    def test_empty_refresh_token_rejected(self):
        """Test that empty auth token is rejected."""
        with pytest.raises(ValidationError):
            EnvConfig(user_oid="user456", refresh_token="")


class TestStreamState:
    """Tests for StreamState enum."""

    def test_stream_state_values(self):
        """Test StreamState enum values."""
        assert StreamState.LIVE.value == "live"
        assert StreamState.TWITCH.value == "twitch"
        assert StreamState.YOUTUBE.value == "youtube"

    def test_stream_state_string(self):
        """Test StreamState string representation."""
        assert str(StreamState.LIVE) == "live"
        assert str(StreamState.TWITCH) == "twitch"
