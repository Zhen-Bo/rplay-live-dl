"""
RPlay API client module.

Provides a client for interacting with the RPlay live streaming platform API,
including methods for retrieving stream status and generating stream URLs.
"""

from typing import List
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.constants import (
    DEFAULT_HTTP_HEADERS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_RETRY_BACKOFF_FACTOR,
    RETRY_STATUS_CODES,
    RPLAY_API_BASE_URL,
)
from core.logger import setup_logger
from models.rplay import LiveStream

__all__ = [
    "RPlayAPI",
    "RPlayAPIError",
    "RPlayAuthError",
    "RPlayConnectionError",
]


class RPlayAPIError(Exception):
    """Base exception for RPlay API errors."""

    pass


class RPlayAuthError(RPlayAPIError):
    """Exception raised for authentication-related errors."""

    pass


class RPlayConnectionError(RPlayAPIError):
    """Exception raised for connection-related errors."""

    pass


class RPlayAPI:
    """
    RPlay livestream platform API client.

    A client for interacting with the RPlay live streaming platform API.
    Provides methods for retrieving stream status information and stream URLs
    with automatic retry on transient failures.
    """

    def __init__(self, auth_token: str, user_oid: str) -> None:
        """
        Initialize the API client with authentication credentials.

        Args:
            auth_token: JWT authentication token from user login
            user_oid: Unique identifier for the authenticated user

        Note:
            Both auth_token and user_oid are required for authenticated endpoints
        """
        self.auth_token = auth_token
        self.user_oid = user_oid
        self.headers = DEFAULT_HTTP_HEADERS.copy()
        self.logger = setup_logger("RPlayAPI")

        # Configure session with retry strategy
        self._session = self._create_session()

    def _create_session(self) -> requests.Session:
        """
        Create a requests session with retry configuration.

        Returns:
            requests.Session: Configured session with retry adapter
        """
        session = requests.Session()

        retry_strategy = Retry(
            total=DEFAULT_MAX_RETRIES,
            backoff_factor=DEFAULT_RETRY_BACKOFF_FACTOR,
            status_forcelist=RETRY_STATUS_CODES,
            allowed_methods=["GET", "POST"],
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        return session

    def get_livestream_status(self) -> List[LiveStream]:
        """
        Retrieve status information for all currently active livestreams.

        Returns:
            List[LiveStream]: A list of LiveStream objects, each containing
                information about an active stream including creator details
                and stream metadata.

        Raises:
            RPlayConnectionError: If the API request fails after retries
        """
        url = f"{RPLAY_API_BASE_URL}/live/livestreams"

        try:
            response = self._session.get(
                url,
                headers=self.headers,
                timeout=DEFAULT_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            streams_data = response.json()
            return [LiveStream(**stream) for stream in streams_data]

        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout while fetching livestream status from {url}")
            raise RPlayConnectionError("Request timed out")

        except requests.exceptions.ConnectionError as e:
            self.logger.error(f"Connection error while fetching livestream status: {e}")
            raise RPlayConnectionError(f"Connection failed: {e}")

        except requests.exceptions.HTTPError as e:
            self.logger.error(f"HTTP error while fetching livestream status: {e}")
            raise RPlayAPIError(f"HTTP error: {e}")

        except Exception as e:
            self.logger.error(f"Unexpected error while fetching livestream status: {e}")
            raise RPlayAPIError(f"Unexpected error: {e}")

    def get_stream_url(self, creator_oid: str) -> str:
        """
        Generate the playback URL for a specific creator's livestream.

        Args:
            creator_oid: Unique identifier of the streamer

        Returns:
            str: Complete M3U8 format stream URL with authentication parameters

        Raises:
            RPlayAuthError: If authentication fails
            RPlayAPIError: If stream key retrieval fails
        """
        stream_key = self._get_stream_key()

        params = urlencode({
            "creatorOid": creator_oid,
            "key2": stream_key,
        })

        return f"{RPLAY_API_BASE_URL}/live/stream/playlist.m3u8?{params}"

    def _get_stream_key(self) -> str:
        """
        Retrieve the authentication key required for stream access.

        Returns:
            str: Stream authentication key from the API

        Raises:
            RPlayAuthError: If authentication is invalid or expired
            RPlayConnectionError: If the API request fails
        """
        auth_headers = self.headers.copy()
        auth_headers["Authorization"] = self.auth_token

        url = (
            f"{RPLAY_API_BASE_URL}/live/key2?"
            f"lang=en&requestorOid={self.user_oid}&loginType=plax"
        )

        try:
            response = self._session.get(
                url,
                headers=auth_headers,
                timeout=DEFAULT_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()

            if "authKey" not in data:
                self.logger.error("Invalid response: missing authKey")
                raise RPlayAuthError("Invalid authentication response")

            return data["authKey"]

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                self.logger.error("Authentication failed - token may be expired")
                raise RPlayAuthError(
                    "Authentication failed. Please check your AUTH_TOKEN."
                )
            raise RPlayAPIError(f"Failed to get stream key: {e}")

        except requests.exceptions.Timeout:
            self.logger.error("Timeout while getting stream key")
            raise RPlayConnectionError("Request timed out while getting stream key")

        except requests.exceptions.ConnectionError as e:
            self.logger.error(f"Connection error while getting stream key: {e}")
            raise RPlayConnectionError(f"Connection failed: {e}")

    def close(self) -> None:
        """Close the API client session."""
        self._session.close()

    def __enter__(self) -> "RPlayAPI":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()
