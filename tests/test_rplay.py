"""Tests for RPlay API client module."""

from datetime import datetime
from unittest.mock import MagicMock, call, patch

import pytest
import requests
import responses
from requests.exceptions import ConnectionError, HTTPError, Timeout

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

    def test_reset_method(self):
        """Test CreatorStreamState reset method clears state."""
        state = CreatorStreamState(
            last_stream_oid="stream-1",
            is_current_stream_blocked=True,
        )
        state.reset()
        assert state.last_stream_oid is None
        assert state.is_current_stream_blocked is False

    def test_update_stream_oid(self):
        """Test updating stream oid clears the blocked flag."""
        state = CreatorStreamState(
            last_stream_oid="stream-1",
            is_current_stream_blocked=True,
        )
        state.update_stream_oid("stream-2")
        assert state.last_stream_oid == "stream-2"
        assert state.is_current_stream_blocked is False

    def test_mark_blocked(self):
        """Test mark_blocked sets the blocked flag."""
        state = CreatorStreamState()
        state.mark_blocked()
        assert state.is_current_stream_blocked is True


class TestValidateM3u8Url:
    """Tests for validate_m3u8_url method using real HTTP mocking."""

    TEST_URL = "http://example.com/stream.m3u8"

    @responses.activate
    def test_returns_true_on_200(self):
        """Test returns True when URL returns 200 OK."""
        responses.add(responses.HEAD, self.TEST_URL, status=200)
        api = RPlayAPI(auth_token="test", user_oid="test")

        result = api.validate_m3u8_url(self.TEST_URL, retries=1)

        assert result is True
        assert len(responses.calls) == 1
        assert responses.calls[0].request.url == self.TEST_URL

    @responses.activate
    def test_returns_false_on_404(self):
        """Test returns False when URL returns 404 (paid content)."""
        responses.add(responses.HEAD, self.TEST_URL, status=404)
        api = RPlayAPI(auth_token="test", user_oid="test")

        with patch("time.sleep"):
            result = api.validate_m3u8_url(self.TEST_URL, retries=1)

        assert result is False

    @responses.activate
    def test_returns_false_on_403(self):
        """Test returns False when URL returns 403 Forbidden."""
        responses.add(responses.HEAD, self.TEST_URL, status=403)
        api = RPlayAPI(auth_token="test", user_oid="test")

        with patch("time.sleep"):
            result = api.validate_m3u8_url(self.TEST_URL, retries=1)

        assert result is False

    @responses.activate
    def test_does_not_retry_blocked_statuses(self):
        """Test 403 stops validation immediately without retry."""
        responses.add(responses.HEAD, self.TEST_URL, status=403)
        api = RPlayAPI(auth_token="test", user_oid="test")

        with patch("time.sleep"):
            result = api.validate_m3u8_url(self.TEST_URL, retries=3, retry_delay=0.1)

        assert result is False
        assert len(responses.calls) == 1

    @responses.activate
    def test_retries_on_404_before_giving_up(self):
        """Test 404 is retried before returning False (stream may not be ready yet)."""
        responses.add(responses.HEAD, self.TEST_URL, status=404)
        responses.add(responses.HEAD, self.TEST_URL, status=404)
        responses.add(responses.HEAD, self.TEST_URL, status=404)
        api = RPlayAPI(auth_token="test", user_oid="test")

        with patch("time.sleep"):
            result = api.validate_m3u8_url(self.TEST_URL, retries=3, retry_delay=0.1)

        assert result is False
        assert len(responses.calls) == 3

    @responses.activate
    def test_returns_true_after_404_then_200(self):
        """Test returns True when 404 is followed by 200 on retry."""
        responses.add(responses.HEAD, self.TEST_URL, status=404)
        responses.add(responses.HEAD, self.TEST_URL, status=200)
        api = RPlayAPI(auth_token="test", user_oid="test")

        with patch("time.sleep"):
            result = api.validate_m3u8_url(self.TEST_URL, retries=3, retry_delay=0.1)

        assert result is True
        assert len(responses.calls) == 2

    @responses.activate
    def test_401_raises_auth_error_during_validation(self):
        """Test 401 during validation is treated as a global auth failure."""
        responses.add(responses.HEAD, self.TEST_URL, status=401)
        api = RPlayAPI(auth_token="test", user_oid="test")

        with patch("time.sleep") as mock_sleep:
            with pytest.raises(RPlayAuthError, match="Authentication failed"):
                api.validate_m3u8_url(self.TEST_URL, retries=3, retry_delay=0.1)

        mock_sleep.assert_not_called()
        assert len(responses.calls) == 1

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

    @responses.activate
    def test_succeeds_after_retry(self):
        """Test returns True if succeeds on retry."""
        responses.add(responses.HEAD, self.TEST_URL, status=500)
        responses.add(responses.HEAD, self.TEST_URL, status=500)
        responses.add(responses.HEAD, self.TEST_URL, status=200)
        api = RPlayAPI(auth_token="test", user_oid="test")

        with patch("time.sleep"):
            result = api.validate_m3u8_url(self.TEST_URL, retries=3, retry_delay=0.1)

        assert result is True
        assert len(responses.calls) == 3

    @responses.activate
    def test_returns_false_on_timeout(self):
        """Test returns False on connection timeout."""
        responses.add(
            responses.HEAD,
            self.TEST_URL,
            body=requests.exceptions.Timeout("Connection timed out"),
        )
        api = RPlayAPI(auth_token="test", user_oid="test")

        with patch("time.sleep"):
            result = api.validate_m3u8_url(self.TEST_URL, retries=1)

        assert result is False

    @responses.activate
    def test_returns_false_on_connection_error(self):
        """Test returns False on connection error."""
        responses.add(
            responses.HEAD,
            self.TEST_URL,
            body=requests.exceptions.ConnectionError("Network unreachable"),
        )
        api = RPlayAPI(auth_token="test", user_oid="test")

        with patch("time.sleep"):
            result = api.validate_m3u8_url(self.TEST_URL, retries=1)

        assert result is False

    @responses.activate
    def test_uses_default_retry_values(self):
        """Test uses default retry count (3) and delay (3.0s)."""
        responses.add(responses.HEAD, self.TEST_URL, status=500)
        responses.add(responses.HEAD, self.TEST_URL, status=500)
        responses.add(responses.HEAD, self.TEST_URL, status=500)
        api = RPlayAPI(auth_token="test", user_oid="test")

        with patch("time.sleep") as mock_sleep:
            api.validate_m3u8_url(self.TEST_URL)

        assert len(responses.calls) == 3
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list == [call(3.0), call(6.0)]

    @responses.activate
    def test_retries_on_retriable_server_errors(self):
        """Test retriable server errors continue until attempts are exhausted."""
        responses.add(responses.HEAD, self.TEST_URL, status=500)
        responses.add(responses.HEAD, self.TEST_URL, status=500)
        responses.add(responses.HEAD, self.TEST_URL, status=500)
        api = RPlayAPI(auth_token="test", user_oid="test")

        with patch("time.sleep"):
            result = api.validate_m3u8_url(self.TEST_URL, retries=3, retry_delay=0.1)

        assert result is False
        assert len(responses.calls) == 3

    @responses.activate
    def test_stops_retrying_on_success(self):
        """Test stops retrying once success is achieved."""
        responses.add(responses.HEAD, self.TEST_URL, status=200)
        responses.add(responses.HEAD, self.TEST_URL, status=200)
        responses.add(responses.HEAD, self.TEST_URL, status=200)
        api = RPlayAPI(auth_token="test", user_oid="test")

        result = api.validate_m3u8_url(self.TEST_URL, retries=3)

        assert result is True
        assert len(responses.calls) == 1
