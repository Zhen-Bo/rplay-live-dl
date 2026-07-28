"""
RPlay API client module.

Provides a client for interacting with the RPlay live streaming platform API,
including methods for retrieving stream status and generating stream URLs.
"""

import time
from typing import List
from urllib.parse import urlencode

import requests
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from core.constants import (
    DEFAULT_HTTP_HEADERS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_RETRY_BACKOFF_FACTOR,
    DEFAULT_RPLAY_API_BASE_URL,
    RETRY_STATUS_CODES,
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


class _RetryableStatusCodeError(Exception):
    """Internal exception used to retry transient HTTP status codes."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class RPlayAPI:
    """
    RPlay livestream platform API client.

    A client for interacting with the RPlay live streaming platform API.
    Provides methods for retrieving stream status information and stream URLs
    with automatic retry on transient failures.
    """

    def __init__(
        self,
        auth_token: str,
        user_oid: str,
        base_url: str = DEFAULT_RPLAY_API_BASE_URL,
    ) -> None:
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
        self.base_url = base_url.rstrip("/")
        self.headers = DEFAULT_HTTP_HEADERS.copy()
        self.logger = setup_logger("RPlayAPI")
        self._session = self._create_session()

    def set_base_url(self, base_url: str) -> None:
        """Update the API base URL used for future requests."""
        self.base_url = base_url.rstrip("/")

    def _create_session(self) -> requests.Session:
        """Create a plain requests session."""
        return requests.Session()

    def _build_retrying(
        self,
        *,
        attempts: int,
        retry_delay: float,
        operation: str,
    ) -> Retrying:
        """Build a tenacity retry controller for transient request failures."""
        return Retrying(
            reraise=True,
            stop=stop_after_attempt(max(1, attempts)),
            wait=wait_exponential(multiplier=retry_delay),
            retry=retry_if_exception_type(
                (
                    requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError,
                    _RetryableStatusCodeError,
                )
            ),
            sleep=time.sleep,
            before_sleep=self._make_before_sleep_logger(operation),
        )

    def _make_before_sleep_logger(self, operation: str):
        """Create a before-sleep callback for retry logging."""

        def _callback(retry_state) -> None:
            exception = retry_state.outcome.exception()
            wait_seconds = 0.0
            if retry_state.next_action is not None:
                wait_seconds = retry_state.next_action.sleep
            self.logger.warning(
                f"{operation} attempt {retry_state.attempt_number} failed; "
                f"retrying in {wait_seconds:.1f}s: {exception}"
            )

        return _callback

    @staticmethod
    def _is_retryable_status_code(status_code: object) -> bool:
        """Return True when the given HTTP status should be retried."""
        return isinstance(status_code, int) and status_code in RETRY_STATUS_CODES

    @staticmethod
    def _is_auth_status_code(status_code: object) -> bool:
        """Return True when the given HTTP status indicates credential failure."""
        return isinstance(status_code, int) and status_code in (401, 403)

    def get_livestream_status(self) -> List[LiveStream]:
        """
        Retrieve status information for all currently active livestreams.

        Returns:
            List[LiveStream]: A list of LiveStream objects, each containing
                information about an active stream including creator details
                and stream metadata.

        Raises:
            RPlayConnectionError: If the API request times out or loses connection
            RPlayAuthError: If authentication is invalid or expired
            RPlayAPIError: If the API returns a non-retryable HTTP failure
        """
        url = f"{self.base_url}/live/livestreams"

        try:
            for attempt in self._build_retrying(
                attempts=DEFAULT_MAX_RETRIES,
                retry_delay=DEFAULT_RETRY_BACKOFF_FACTOR,
                operation="Fetching livestream status",
            ):
                with attempt:
                    response = self._session.get(
                        url,
                        headers=self.headers,
                        timeout=DEFAULT_REQUEST_TIMEOUT,
                    )
                    status_code = getattr(response, "status_code", None)

                    if self._is_auth_status_code(status_code):
                        raise RPlayAuthError(
                            "Authentication failed. Please check your AUTH_TOKEN."
                        )

                    if self._is_retryable_status_code(status_code):
                        raise _RetryableStatusCodeError(status_code)

                    response.raise_for_status()
                    streams_data = response.json()
                    return [LiveStream(**stream) for stream in streams_data]

        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout while fetching livestream status from {url}")
            raise RPlayConnectionError("Request timed out")

        except requests.exceptions.ConnectionError as exc:
            self.logger.error(f"Connection error while fetching livestream status: {exc}")
            raise RPlayConnectionError(f"Connection failed: {exc}")

        except _RetryableStatusCodeError as exc:
            self.logger.error(f"HTTP error while fetching livestream status: {exc}")
            raise RPlayAPIError(f"HTTP error: {exc}")

        except requests.exceptions.HTTPError as exc:
            self.logger.error(f"HTTP error while fetching livestream status: {exc}")
            raise RPlayAPIError(f"HTTP error: {exc}")

        except RPlayAuthError:
            raise

        except Exception as exc:
            self.logger.exception(f"Unexpected error while fetching livestream status: {exc}")
            raise RPlayAPIError(f"Unexpected error: {exc}")

        return []

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

        return f"{self.base_url}/live/stream/playlist.m3u8?{params}"

    def validate_credentials(self) -> None:
        """
        Verify AUTH_TOKEN/USER_OID by fetching a stream key once.

        Raises:
            RPlayAuthError: If credentials are invalid or expired
            RPlayConnectionError: If the request times out or loses connection
            RPlayAPIError: If the API returns a non-retryable non-auth failure
        """
        # ponytail: key2 is the cheapest authenticated call; no parallel health endpoint.
        self._get_stream_key()

    def _get_stream_key(self) -> str:
        """
        Retrieve the authentication key required for stream access.

        Returns:
            str: Stream authentication key from the API

        Raises:
            RPlayAuthError: If authentication is invalid or expired
            RPlayConnectionError: If the API request times out or loses connection
            RPlayAPIError: If the API returns a non-retryable HTTP failure
        """
        auth_headers = self.headers.copy()
        auth_headers["Authorization"] = self.auth_token

        url = (
            f"{self.base_url}/live/key2?"
            f"lang=en&requestorOid={self.user_oid}&loginType=plax"
        )

        try:
            for attempt in self._build_retrying(
                attempts=DEFAULT_MAX_RETRIES,
                retry_delay=DEFAULT_RETRY_BACKOFF_FACTOR,
                operation="Fetching stream key",
            ):
                with attempt:
                    response = self._session.get(
                        url,
                        headers=auth_headers,
                        timeout=DEFAULT_REQUEST_TIMEOUT,
                    )
                    status_code = getattr(response, "status_code", None)

                    if self._is_auth_status_code(status_code):
                        # Caller logs expected auth failures (startup / monitor dedup).
                        raise RPlayAuthError(
                            "Authentication failed. Please check your AUTH_TOKEN."
                        )

                    if self._is_retryable_status_code(status_code):
                        raise _RetryableStatusCodeError(status_code)

                    response.raise_for_status()
                    data = response.json()
                    if "authKey" not in data:
                        # Caller logs expected auth failures (startup / monitor dedup).
                        raise RPlayAuthError("Invalid authentication response")
                    return data["authKey"]

        except RPlayAuthError:
            raise

        except requests.exceptions.Timeout:
            self.logger.error("Timeout while getting stream key")
            raise RPlayConnectionError("Request timed out while getting stream key")

        except requests.exceptions.ConnectionError as exc:
            self.logger.error(f"Connection error while getting stream key: {exc}")
            raise RPlayConnectionError(f"Connection failed: {exc}")

        except _RetryableStatusCodeError as exc:
            self.logger.error(f"Failed to get stream key: {exc}")
            raise RPlayAPIError(f"Failed to get stream key: {exc}")

        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (401, 403):
                # Caller logs expected auth failures (startup / monitor dedup).
                raise RPlayAuthError(
                    "Authentication failed. Please check your AUTH_TOKEN."
                )
            raise RPlayAPIError(f"Failed to get stream key: {exc}")

        except Exception as exc:
            # ponytail: traceback and message suppressed - may embed the Authorization header
            msg = f"Unexpected error while getting stream key: {type(exc).__name__}"
            self.logger.error(msg)
            raise RPlayAPIError(msg) from None

    def close(self) -> None:
        """Close the API client session."""
        self._session.close()

    def __enter__(self) -> "RPlayAPI":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()
