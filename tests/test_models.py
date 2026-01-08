"""Tests for data models."""

import pytest
from pydantic import ValidationError

from models.config import CreatorProfile
from models.env import EnvConfig
from models.rplay import LiveStream, MultiLangNick, StreamState


class TestCreatorProfile:
    """Tests for CreatorProfile model."""

    def test_valid_creator_profile(self):
        """Test creating a valid creator profile."""
        profile = CreatorProfile(
            creator_name="Test Creator",
            creator_oid="abc123",
        )
        assert profile.creator_name == "Test Creator"
        assert profile.creator_oid == "abc123"

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

    def test_empty_creator_name_rejected(self):
        """Test that empty creator name is rejected."""
        with pytest.raises(ValidationError):
            CreatorProfile(
                creator_name="",
                creator_oid="abc123",
            )

    def test_whitespace_only_creator_name_rejected(self):
        """Test that whitespace-only creator name is rejected."""
        with pytest.raises(ValidationError):
            CreatorProfile(
                creator_name="   ",
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

    def test_valid_env_config(self):
        """Test creating a valid environment config."""
        config = EnvConfig(
            auth_token="token123",
            user_oid="user456",
            interval=60,
        )
        assert config.auth_token == "token123"
        assert config.user_oid == "user456"
        assert config.interval == 60

    def test_default_interval(self):
        """Test that interval has a default value."""
        config = EnvConfig(
            auth_token="token123",
            user_oid="user456",
        )
        assert config.interval == 60

    def test_interval_minimum(self):
        """Test that interval has a minimum value."""
        with pytest.raises(ValidationError):
            EnvConfig(
                auth_token="token123",
                user_oid="user456",
                interval=5,  # Below minimum of 10
            )

    def test_interval_maximum(self):
        """Test that interval has a maximum value."""
        with pytest.raises(ValidationError):
            EnvConfig(
                auth_token="token123",
                user_oid="user456",
                interval=4000,  # Above maximum of 3600
            )

    def test_empty_auth_token_rejected(self):
        """Test that empty auth token is rejected."""
        with pytest.raises(ValidationError):
            EnvConfig(
                auth_token="",
                user_oid="user456",
            )

    def test_whitespace_auth_token_rejected(self):
        """Test that whitespace-only auth token is rejected."""
        with pytest.raises(ValidationError):
            EnvConfig(
                auth_token="   ",
                user_oid="user456",
            )


class TestMultiLangNick:
    """Tests for MultiLangNick model."""

    def test_empty_multilang_nick(self):
        """Test creating empty multi-language nickname."""
        nick = MultiLangNick()
        assert nick.ko is None
        assert nick.en is None
        assert nick.jp is None

    def test_get_display_name_preferred_language(self):
        """Test getting display name in preferred language."""
        nick = MultiLangNick(ko="한국어", en="English", jp="日本語")
        assert nick.get_display_name("ko") == "한국어"
        assert nick.get_display_name("en") == "English"
        assert nick.get_display_name("jp") == "日本語"

    def test_get_display_name_fallback(self):
        """Test fallback when preferred language not available."""
        nick = MultiLangNick(ko="한국어")
        # Should fall back to Korean when English not available
        assert nick.get_display_name("en") == "한국어"

    def test_get_display_name_none(self):
        """Test returning None when no names available."""
        nick = MultiLangNick()
        assert nick.get_display_name() is None


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
