"""RPlay API client for stream status, stream keys and playback URLs."""

import base64
import json
import math
import time
from typing import Callable, List
from urllib.parse import urlencode

import requests
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.constants import (
    DEFAULT_HTTP_HEADERS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_RETRY_BACKOFF_FACTOR,
    DEFAULT_TOKEN_REFRESH_LEEWAY_SECONDS,
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
    pass


class RPlayAuthError(RPlayAPIError):
    pass


class RPlayConnectionError(RPlayAPIError):
    pass


class _RetryableStatusCodeError(Exception):
    """Internal exception used to retry transient HTTP status codes."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class RPlayAPI:
    def __init__(
        self,
        base_url: str,
        user_oid: str,
        refresh_token: str,
        token_refresh_leeway_seconds: int = DEFAULT_TOKEN_REFRESH_LEEWAY_SECONDS,
    ) -> None:
        refresh_token = refresh_token.strip()
        self.base_url = base_url.rstrip("/")
        self.user_oid = user_oid
        self.auth_token = ""
        self.refresh_token = refresh_token
        self.token_refresh_leeway_seconds = token_refresh_leeway_seconds
        self._token_expires_at: float | None = None
        self.headers = DEFAULT_HTTP_HEADERS.copy()
        self.logger = setup_logger("RPlayAPI")
        self._session = requests.Session()

    def set_base_url(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _build_retrying(self, operation: str) -> Retrying:
        return Retrying(
            reraise=True,
            stop=stop_after_attempt(DEFAULT_MAX_RETRIES),
            wait=wait_exponential(multiplier=DEFAULT_RETRY_BACKOFF_FACTOR),
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

    def _make_before_sleep_logger(
        self, operation: str
    ) -> Callable[[RetryCallState], None]:
        def _callback(retry_state: RetryCallState) -> None:
            exception = retry_state.outcome.exception() if retry_state.outcome else None
            # Transport exception text can contain headers or credential URLs.
            reason = (
                f"HTTP {exception.status_code}"
                if isinstance(exception, _RetryableStatusCodeError)
                else type(exception).__name__
            )
            wait_seconds = 0.0
            if retry_state.next_action is not None:
                wait_seconds = retry_state.next_action.sleep
            self.logger.warning(
                f"{operation} attempt {retry_state.attempt_number} failed; "
                f"retrying in {wait_seconds:.1f}s: {reason}"
            )

        return _callback

    def get_livestream_status(self) -> List[LiveStream]:
        """Raises RPlayAuthError on 401/403 and RPlayConnectionError on timeout or lost connection."""
        url = f"{self.base_url}/live/livestreams"

        try:
            return self._build_retrying("Fetching livestream status")(
                self._request_livestream_status
            )

        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout while fetching livestream status from {url}")
            raise RPlayConnectionError("Request timed out")

        except requests.exceptions.ConnectionError as exc:
            self.logger.error(
                f"Connection error while fetching livestream status: {exc}"
            )
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
            self.logger.exception(
                f"Unexpected error while fetching livestream status: {exc}"
            )
            raise RPlayAPIError(f"Unexpected error: {exc}")

    def _request_livestream_status(self) -> List[LiveStream]:
        response = self._session.get(
            f"{self.base_url}/live/livestreams",
            headers=self.headers,
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        if response.status_code in (401, 403):
            raise RPlayAuthError(
                "Authentication failed while fetching livestream status"
            )
        if response.status_code in RETRY_STATUS_CODES:
            raise _RetryableStatusCodeError(response.status_code)
        response.raise_for_status()
        return [LiveStream(**stream) for stream in response.json()]

    def get_stream_url(self, creator_oid: str, stream_key: str) -> str:
        params = urlencode(
            {
                "creatorOid": creator_oid,
                "key2": stream_key,
            }
        )

        return f"{self.base_url}/live/stream/playlist.m3u8?{params}"

    def validate_credentials(self) -> None:
        """Check REFRESH_TOKEN and USER_OID by fetching one stream key."""
        # key2 is the cheapest authenticated call, there is no health endpoint.
        self._get_stream_key()

    @staticmethod
    def _access_token_expiry(token: str) -> float:
        try:
            parts = token.split(".")
            if len(parts) != 3 or not all(parts):
                raise ValueError
            payload = base64.b64decode(
                f"{parts[1]}{'=' * (-len(parts[1]) % 4)}", altchars=b"-_", validate=True
            )
            claims = json.loads(payload)
            exp = claims.get("exp") if isinstance(claims, dict) else None
            if isinstance(exp, bool) or not isinstance(exp, (int, float)):
                raise ValueError
            if not math.isfinite(exp):
                raise ValueError
            if exp <= time.time():
                raise ValueError
            return exp
        except (ValueError, TypeError, OverflowError):
            raise RPlayAPIError(
                "Token refresh returned an invalid or expired access token"
            ) from None

    def _ensure_valid_token(self) -> None:
        """Acquire a JWT on first use, then renew it only before key2 requests."""
        remaining = (
            self._token_expires_at - time.time()
            if self._token_expires_at is not None
            else 0
        )
        if remaining <= 0 or remaining < self.token_refresh_leeway_seconds:
            self._refresh_auth_token()

    def _refresh_auth_token(self) -> None:
        try:
            token = self._build_retrying("Refreshing access token")(
                self._request_access_token
            )
            exp = self._access_token_expiry(token)
            self.auth_token = token
            self._token_expires_at = exp
            self.logger.info("Access token refreshed")
        except RPlayAPIError:
            raise
        except requests.exceptions.Timeout:
            raise RPlayConnectionError(
                "Request timed out while refreshing token"
            ) from None
        except requests.exceptions.ConnectionError:
            raise RPlayConnectionError(
                "Connection failed while refreshing token"
            ) from None
        except _RetryableStatusCodeError as exc:
            raise RPlayAPIError(
                f"Token refresh failed: HTTP {exc.status_code}"
            ) from None
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "error"
            raise RPlayAPIError(f"Token refresh failed: HTTP {status}") from None
        except Exception as exc:
            raise RPlayAPIError(
                f"Invalid token refresh response: {type(exc).__name__}"
            ) from None

    def _request_access_token(self) -> str:
        headers = {
            **self.headers,
            "refresh-token": self.refresh_token,
            "platform-type": "rplay",
            "Content-Type": "application/json",
        }
        response = self._session.post(
            f"{self.base_url}/rplay/account/refresh-token",
            headers=headers,
            json={"requestorOid": self.user_oid},
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        status_code = response.status_code
        if status_code in (401, 403):
            raise RPlayAuthError(
                "Token refresh rejected. Update REFRESH_TOKEN and verify USER_OID."
            )
        if status_code in RETRY_STATUS_CODES:
            raise _RetryableStatusCodeError(status_code)
        response.raise_for_status()
        data = response.json()
        token = data.get("accessToken") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise RPlayAPIError("Invalid token refresh response: missing accessToken")
        return token.strip()

    def _get_stream_key(self) -> str:
        try:
            return self._build_retrying("Fetching stream key")(self._request_stream_key)
        except RPlayAPIError:
            raise

        except requests.exceptions.Timeout:
            self.logger.error("Timeout while getting stream key")
            raise RPlayConnectionError(
                "Request timed out while getting stream key"
            ) from None

        except requests.exceptions.ConnectionError:
            self.logger.error("Connection error while getting stream key")
            raise RPlayConnectionError(
                "Connection failed while getting stream key"
            ) from None

        except _RetryableStatusCodeError as exc:
            self.logger.error(f"Failed to get stream key: {exc}")
            raise RPlayAPIError(f"Failed to get stream key: {exc}") from None

        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "error"
            if status in (401, 403):
                # Caller logs expected auth failures (startup / monitor dedup).
                raise RPlayAuthError(
                    "Authentication failed. Please check your REFRESH_TOKEN and USER_OID."
                ) from None
            raise RPlayAPIError(f"Failed to get stream key: HTTP {status}") from None

        except Exception as exc:
            # Traceback and message suppressed on purpose, they may embed the Authorization header.
            msg = f"Unexpected error while getting stream key: {type(exc).__name__}"
            self.logger.error(msg)
            raise RPlayAPIError(msg) from None

    def _request_stream_key(self) -> str:
        """Renews the JWT before each retry if needed."""
        self._ensure_valid_token()
        url = (
            f"{self.base_url}/live/key2?"
            f"lang=en&requestorOid={self.user_oid}&loginType=rplay"
        )
        response = self._session.get(
            url,
            headers={**self.headers, "Authorization": self.auth_token},
            timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        status_code = response.status_code
        if status_code in (401, 403):
            raise RPlayAuthError(
                "Authentication failed. Please check your REFRESH_TOKEN and USER_OID."
            )
        if status_code in RETRY_STATUS_CODES:
            raise _RetryableStatusCodeError(status_code)
        response.raise_for_status()
        data = response.json()
        auth_key = data.get("authKey")
        if not isinstance(auth_key, str) or not auth_key:
            raise RPlayAuthError("Invalid authentication response")
        return auth_key

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "RPlayAPI":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
