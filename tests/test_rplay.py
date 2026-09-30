"""Tests for RPlay API client module."""

from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import ConnectionError, HTTPError, JSONDecodeError, Timeout

from core.rplay import (
    RPlayAPI,
    RPlayAPIError,
    RPlayAuthError,
    RPlayConnectionError,
)
from models.rplay import CreatorStreamState


@pytest.fixture(autouse=True)
def _skip_token_renewal(monkeypatch):
    """These tests cover request handling, not JWT renewal (see test_token_refresh)."""
    monkeypatch.setattr(RPlayAPI, "_ensure_valid_token", lambda self: None)


@pytest.fixture
def api():
    return RPlayAPI(
        base_url="https://api.rplay.live", user_oid="test", refresh_token="test"
    )


def ok_response(body):
    """A successful response whose JSON body is `body`."""
    response = MagicMock()
    response.json.return_value = body
    return response


def stub_get(api, **kwargs):
    """Replace the HTTP GET the client makes. Pass return_value or side_effect."""
    return patch.object(api._session, "get", **kwargs)


class TestGetLivestreamStatus:
    """Tests for get_livestream_status method."""

    def test_uses_custom_base_url(self):
        """Test livestream status uses the instance-level API base URL."""
        api = RPlayAPI(
            base_url="https://api.example.com/", user_oid="test", refresh_token="test"
        )

        with stub_get(api, return_value=ok_response([])) as mock_get:
            api.get_livestream_status()

        assert mock_get.call_args.args[0] == "https://api.example.com/live/livestreams"

    def test_successful_request(self, api):
        """Test successful livestream status retrieval."""
        body = [
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

        with stub_get(api, return_value=ok_response(body)):
            streams = api.get_livestream_status()

        assert len(streams) == 1
        assert streams[0].creator_nickname == "Test Creator"

    def test_timeout_raises_connection_error(self, api):
        """Test that timeout raises RPlayConnectionError."""
        with stub_get(api, side_effect=Timeout()):
            with pytest.raises(RPlayConnectionError, match="timed out"):
                api.get_livestream_status()

    def test_connection_error_raises_connection_error(self, api):
        """Test that connection errors raise RPlayConnectionError."""
        with stub_get(api, side_effect=ConnectionError("Network unreachable")):
            with pytest.raises(RPlayConnectionError, match="Connection failed"):
                api.get_livestream_status()

    def test_http_error_raises_api_error(self, api):
        """Test that HTTP errors raise RPlayAPIError."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = HTTPError("500 Server Error")

        with stub_get(api, return_value=mock_response):
            with pytest.raises(RPlayAPIError, match="HTTP error"):
                api.get_livestream_status()


class TestGetStreamUrl:
    """Tests for get_stream_url method."""

    def test_url_encoding(self, api):
        url = api.get_stream_url("creator123", stream_key="key+with/special=chars")
        assert "key%2Bwith%2Fspecial%3Dchars" in url
        assert "creatorOid=creator123" in url
        assert "playlist.m3u8" in url


class TestValidateCredentials:
    """Tests for the public credential-validation seam."""

    def test_auth_failure_raises_auth_error(self, api):
        """Test 401 from key2 raises RPlayAuthError without API-layer ERROR log."""
        mock_response = MagicMock()
        mock_response.status_code = 401

        with stub_get(api, return_value=mock_response):
            with patch.object(api.logger, "error") as mock_error:
                with pytest.raises(RPlayAuthError, match="Authentication failed"):
                    api.validate_credentials()

        mock_error.assert_not_called()


class TestGetStreamKey:
    """Tests for _get_stream_key method."""

    def test_successful_key_retrieval(self, api):
        """Test successful stream key retrieval."""
        with stub_get(api, return_value=ok_response({"authKey": "my_stream_key"})):
            key = api._get_stream_key()

        assert key == "my_stream_key"

    @pytest.mark.parametrize(
        "body", [{"authKey": None}, {"authKey": ""}, {"other": "data"}]
    )
    def test_null_empty_or_missing_auth_key_raises_auth_error(self, api, body):
        with stub_get(api, return_value=ok_response(body)):
            with pytest.raises(RPlayAuthError, match="Invalid authentication"):
                api._get_stream_key()

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_status_raises_auth_error(self, api, status):
        """Test that 401 and 403 responses raise RPlayAuthError."""
        mock_response = MagicMock()
        mock_response.status_code = status
        http_error = HTTPError(f"{status} error")
        http_error.response = mock_response
        mock_response.raise_for_status.side_effect = http_error

        with stub_get(api, return_value=mock_response):
            with pytest.raises(RPlayAuthError, match="Authentication failed"):
                api._get_stream_key()

    def test_timeout_raises_connection_error(self, api):
        """Test that timeout raises RPlayConnectionError."""
        with stub_get(api, side_effect=Timeout()):
            with pytest.raises(RPlayConnectionError, match="timed out"):
                api._get_stream_key()

    def test_json_decode_error_raises_api_error(self, api):
        """Test malformed JSON body raises RPlayAPIError, not the raw decode error."""
        mock_response = MagicMock()
        mock_response.json.side_effect = JSONDecodeError("Expecting value", "doc", 0)

        with stub_get(api, return_value=mock_response):
            with pytest.raises(RPlayAPIError, match="Unexpected error"):
                api._get_stream_key()

    def test_unexpected_exception_does_not_leak_secret(self, api, caplog):
        """Exception messages may embed Authorization; must not reach logs or RPlayAPIError."""
        secret = "Bearer sekrit-token"

        with stub_get(api, side_effect=RuntimeError(secret)):
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
        assert state.is_current_stream_blocked is False

    def test_mark_blocked(self):
        """Test mark_blocked sets the blocked flag."""
        state = CreatorStreamState()
        state.mark_blocked()
        assert state.is_current_stream_blocked is True


class TestTransientRetry:
    """Tests for retrying transient failures on the surviving API calls."""

    def test_get_livestream_status_retries_transient_connection_errors(self, api):
        """Test transient API connection failures are retried before succeeding."""
        with (
            stub_get(
                api,
                side_effect=[
                    ConnectionError("boom"),
                    ConnectionError("boom"),
                    ok_response([]),
                ],
            ) as mock_get,
            patch("time.sleep") as mock_sleep,
        ):
            streams = api.get_livestream_status()

        assert streams == []
        assert mock_get.call_count == 3
        assert mock_sleep.call_count == 2

    def test_get_stream_key_retries_transient_connection_errors(self, api):
        """Test transient key-fetch failures are retried before succeeding."""
        with (
            stub_get(
                api,
                side_effect=[
                    ConnectionError("boom"),
                    ConnectionError("boom"),
                    ok_response({"authKey": "my_stream_key"}),
                ],
            ) as mock_get,
            patch("time.sleep") as mock_sleep,
        ):
            key = api._get_stream_key()

        assert key == "my_stream_key"
        assert mock_get.call_count == 3
        assert mock_sleep.call_count == 2
