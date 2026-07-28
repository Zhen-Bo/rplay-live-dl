"""Tests for RPlay API client module."""

from unittest.mock import MagicMock, patch

import pytest
import requests
from requests.exceptions import ConnectionError, HTTPError, JSONDecodeError, Timeout

from core.rplay import (
    RPlayAPI,
    RPlayAPIError,
    RPlayAuthError,
    RPlayConnectionError,
)
from models.rplay import CreatorStreamState


class TestRPlayAPIInit:
    """Tests for RPlayAPI initialization."""

    def test_creates_session(self):
        """Test that API client creates a requests session."""
        api = RPlayAPI(auth_token="test_token", user_oid="test_oid")

        # Check session has adapters mounted
        assert "https://" in api._session.adapters
        assert "http://" in api._session.adapters

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

    def test_uses_custom_base_url(self):
        """Test livestream status uses the instance-level API base URL."""
        api = RPlayAPI(
            auth_token="test",
            user_oid="test",
            base_url="https://api.example.com/",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        with patch.object(api._session, "get", return_value=mock_response) as mock_get:
            api.get_livestream_status()

        assert mock_get.call_args.args[0] == "https://api.example.com/live/livestreams"

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

    def test_json_decode_error_raises_api_error(self):
        """Test malformed JSON body raises RPlayAPIError, not the raw decode error."""
        api = RPlayAPI(auth_token="test", user_oid="test")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = JSONDecodeError(
            "Expecting value", "doc", 0
        )

        with patch.object(api._session, "get", return_value=mock_response):
            with pytest.raises(RPlayAPIError, match="Unexpected error"):
                api._get_stream_key()

    def test_unexpected_exception_does_not_leak_secret(self, caplog):
        """Exception messages may embed Authorization; must not reach logs or RPlayAPIError."""
        api = RPlayAPI(auth_token="test", user_oid="test")
        secret = "Bearer sekrit-token"

        with patch.object(
            api._session, "get", side_effect=RuntimeError(secret)
        ):
            with caplog.at_level("ERROR"):
                with pytest.raises(RPlayAPIError) as exc_info:
                    api._get_stream_key()

        assert secret not in str(exc_info.value)
        assert all(secret not in record.getMessage() for record in caplog.records)
        assert "RuntimeError" in str(exc_info.value)


class TestCreatorStreamState:
    """Tests for CreatorStreamState dataclass."""

    def test_default_initialization(self):
        """Test CreatorStreamState default values."""
        state = CreatorStreamState()
        assert state.last_stream_oid is None
        assert state.is_current_stream_blocked is False

    def test_initialization_with_values(self):
        """Test CreatorStreamState with explicit values."""
        state = CreatorStreamState(
            last_stream_oid="stream-1",
            is_current_stream_blocked=True,
        )
        assert state.last_stream_oid == "stream-1"
        assert state.is_current_stream_blocked is True

    def test_mark_blocked(self):
        """Test mark_blocked sets the blocked flag."""
        state = CreatorStreamState()
        state.mark_blocked()
        assert state.is_current_stream_blocked is True


class TestTransientRetry:
    """Tests for retrying transient failures on the surviving API calls."""

    def test_get_livestream_status_retries_transient_connection_errors(self):
        """Test transient API connection failures are retried before succeeding."""
        api = RPlayAPI(auth_token="test", user_oid="test")
        success_response = MagicMock()
        success_response.raise_for_status = MagicMock()
        success_response.json.return_value = []

        with (
            patch.object(
                api._session,
                "get",
                side_effect=[ConnectionError("boom"), ConnectionError("boom"), success_response],
            ) as mock_get,
            patch("time.sleep") as mock_sleep,
        ):
            streams = api.get_livestream_status()

        assert streams == []
        assert mock_get.call_count == 3
        assert mock_sleep.call_count == 2

    def test_get_stream_key_retries_transient_connection_errors(self):
        """Test transient key-fetch failures are retried before succeeding."""
        api = RPlayAPI(auth_token="test", user_oid="test")
        success_response = MagicMock()
        success_response.raise_for_status = MagicMock()
        success_response.json.return_value = {"authKey": "my_stream_key"}

        with (
            patch.object(
                api._session,
                "get",
                side_effect=[ConnectionError("boom"), ConnectionError("boom"), success_response],
            ) as mock_get,
            patch("time.sleep") as mock_sleep,
        ):
            stream_url = api.get_stream_url("creator123")

        assert "creatorOid=creator123" in stream_url
        assert mock_get.call_count == 3
        assert mock_sleep.call_count == 2
