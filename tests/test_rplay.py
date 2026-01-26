"""Tests for RPlay API client module."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
import requests
from requests.exceptions import ConnectionError, HTTPError, Timeout
from urllib3.util.retry import Retry

from core.rplay import (
    RPlayAPI,
    RPlayAPIError,
    RPlayAuthError,
    RPlayConnectionError,
)
from models.rplay import CreatorStreamState


class TestRPlayAPIInit:
    """Tests for RPlayAPI initialization."""

    def test_creates_session_with_retry(self):
        """Test that API client creates session with retry strategy."""
        api = RPlayAPI(auth_token="test_token", user_oid="test_oid")

        # Check session has adapters mounted
        assert "https://" in api._session.adapters
        assert "http://" in api._session.adapters

        # Get the adapter and check retry config
        adapter = api._session.get_adapter("https://example.com")
        retry = adapter.max_retries

        assert isinstance(retry, Retry)
        assert retry.total == 3  # DEFAULT_MAX_RETRIES
        assert 429 in retry.status_forcelist
        assert 500 in retry.status_forcelist
        assert 502 in retry.status_forcelist
        assert 503 in retry.status_forcelist
        assert 504 in retry.status_forcelist

    def test_stores_credentials(self):
        """Test that credentials are stored correctly."""
        api = RPlayAPI(auth_token="my_token", user_oid="my_oid")

        assert api.auth_token == "my_token"
        assert api.user_oid == "my_oid"

    def test_context_manager(self):
        """Test API can be used as context manager."""
        with RPlayAPI(auth_token="test", user_oid="test") as api:
            assert api is not None


class TestGetLivestreamStatus:
    """Tests for get_livestream_status method."""

    def test_successful_request(self):
        """Test successful livestream status retrieval."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "_id": "id1",
                "oid": "oid1",
                "creatorOid": "creator1",
                "creatorNickname": "Test Creator",
                "title": "Test Stream",
                "streamStartTime": "2026-01-08T10:00:00Z",
                "streamState": "live",
            }
        ]
        mock_response.raise_for_status = MagicMock()

        with patch.object(api._session, "get", return_value=mock_response):
            streams = api.get_livestream_status()

        assert len(streams) == 1
        assert streams[0].creator_nickname == "Test Creator"

    def test_timeout_raises_connection_error(self):
        """Test that timeout raises RPlayConnectionError."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        with patch.object(api._session, "get", side_effect=Timeout()):
            with pytest.raises(RPlayConnectionError, match="timed out"):
                api.get_livestream_status()

    def test_connection_error_raises_connection_error(self):
        """Test that connection errors raise RPlayConnectionError."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        with patch.object(api._session, "get", side_effect=ConnectionError("Network unreachable")):
            with pytest.raises(RPlayConnectionError, match="Connection failed"):
                api.get_livestream_status()

    def test_http_error_raises_api_error(self):
        """Test that HTTP errors raise RPlayAPIError."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = HTTPError("500 Server Error")

        with patch.object(api._session, "get", return_value=mock_response):
            with pytest.raises(RPlayAPIError, match="HTTP error"):
                api.get_livestream_status()


class TestGetStreamUrl:
    """Tests for get_stream_url method."""

    def test_url_encoding(self):
        """Test that stream URL is properly URL-encoded."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        # Mock _get_stream_key to return a key with special characters
        with patch.object(api, "_get_stream_key", return_value="key+with/special=chars"):
            url = api.get_stream_url("creator123")

        # Check URL encoding
        assert "key%2Bwith%2Fspecial%3Dchars" in url
        assert "creatorOid=creator123" in url

    def test_returns_m3u8_url(self):
        """Test that returned URL is an m3u8 playlist URL."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        with patch.object(api, "_get_stream_key", return_value="simple_key"):
            url = api.get_stream_url("creator123")

        assert "playlist.m3u8" in url


class TestGetStreamKey:
    """Tests for _get_stream_key method."""

    def test_successful_key_retrieval(self):
        """Test successful stream key retrieval."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        mock_response = MagicMock()
        mock_response.json.return_value = {"authKey": "my_stream_key"}
        mock_response.raise_for_status = MagicMock()

        with patch.object(api._session, "get", return_value=mock_response):
            key = api._get_stream_key()

        assert key == "my_stream_key"

    def test_missing_auth_key_raises_auth_error(self):
        """Test that missing authKey raises RPlayAuthError."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        mock_response = MagicMock()
        mock_response.json.return_value = {"other": "data"}
        mock_response.raise_for_status = MagicMock()

        with patch.object(api._session, "get", return_value=mock_response):
            with pytest.raises(RPlayAuthError, match="Invalid authentication"):
                api._get_stream_key()

    def test_401_raises_auth_error(self):
        """Test that 401 response raises RPlayAuthError."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        mock_response = MagicMock()
        mock_response.status_code = 401
        http_error = HTTPError("401 Unauthorized")
        http_error.response = mock_response
        mock_response.raise_for_status.side_effect = http_error

        with patch.object(api._session, "get", return_value=mock_response):
            with pytest.raises(RPlayAuthError, match="Authentication failed"):
                api._get_stream_key()

    def test_403_raises_auth_error(self):
        """Test that 403 response raises RPlayAuthError."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        mock_response = MagicMock()
        mock_response.status_code = 403
        http_error = HTTPError("403 Forbidden")
        http_error.response = mock_response
        mock_response.raise_for_status.side_effect = http_error

        with patch.object(api._session, "get", return_value=mock_response):
            with pytest.raises(RPlayAuthError, match="Authentication failed"):
                api._get_stream_key()

    def test_timeout_raises_connection_error(self):
        """Test that timeout raises RPlayConnectionError."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        with patch.object(api._session, "get", side_effect=Timeout()):
            with pytest.raises(RPlayConnectionError, match="timed out"):
                api._get_stream_key()


class TestRetryMechanism:
    """Tests specifically for the retry mechanism."""

    def test_retry_on_503(self):
        """Test that 503 errors trigger retry (via retry strategy config)."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        adapter = api._session.get_adapter("https://example.com")
        retry = adapter.max_retries

        # Verify 503 is in the retry list
        assert 503 in retry.status_forcelist
        assert retry.total == 3

    def test_retry_on_429(self):
        """Test that 429 (rate limit) errors trigger retry."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        adapter = api._session.get_adapter("https://example.com")
        retry = adapter.max_retries

        assert 429 in retry.status_forcelist

    def test_retry_backoff_factor(self):
        """Test that backoff factor is configured."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        adapter = api._session.get_adapter("https://example.com")
        retry = adapter.max_retries

        # DEFAULT_RETRY_BACKOFF_FACTOR = 0.5
        assert retry.backoff_factor == 0.5

    def test_retry_allowed_methods(self):
        """Test that GET and POST are allowed for retry."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        adapter = api._session.get_adapter("https://example.com")
        retry = adapter.max_retries

        assert "GET" in retry.allowed_methods
        assert "POST" in retry.allowed_methods


class TestCreatorStreamState:
    """Tests for CreatorStreamState dataclass."""

    def test_default_initialization(self):
        """Test CreatorStreamState default values."""
        state = CreatorStreamState()
        assert state.last_stream_start_time is None
        assert state.is_current_stream_blocked is False

    def test_initialization_with_values(self):
        """Test CreatorStreamState with explicit values."""
        start_time = datetime(2026, 1, 26, 12, 0, 0)
        state = CreatorStreamState(
            last_stream_start_time=start_time,
            is_current_stream_blocked=True,
        )
        assert state.last_stream_start_time == start_time
        assert state.is_current_stream_blocked is True

    def test_reset_method(self):
        """Test CreatorStreamState reset method clears state."""
        start_time = datetime(2026, 1, 26, 12, 0, 0)
        state = CreatorStreamState(
            last_stream_start_time=start_time,
            is_current_stream_blocked=True,
        )
        state.reset()
        assert state.last_stream_start_time is None
        assert state.is_current_stream_blocked is False

    def test_update_stream_start_time(self):
        """Test updating stream start time clears blocked flag."""
        old_time = datetime(2026, 1, 26, 12, 0, 0)
        new_time = datetime(2026, 1, 26, 14, 0, 0)
        state = CreatorStreamState(
            last_stream_start_time=old_time,
            is_current_stream_blocked=True,
        )
        state.update_stream_start_time(new_time)
        assert state.last_stream_start_time == new_time
        assert state.is_current_stream_blocked is False

    def test_mark_blocked(self):
        """Test mark_blocked sets the blocked flag."""
        state = CreatorStreamState()
        state.mark_blocked()
        assert state.is_current_stream_blocked is True
