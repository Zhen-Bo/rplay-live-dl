from typing import List

import requests

from models.rplay import LiveStream


class RPlayAPI:
    """RPlay livestream platform API client.

    A client for interacting with the RPlay live streaming platform API.
    Provides methods for retrieving stream status information and stream URLs.

    Attributes:
        API_BASE_URL: Base URL for all API endpoints
        SITE_URL: Main website URL used for headers
        DEFAULT_HEADERS: Standard HTTP headers used in requests
    """

    # API endpoint constants
    API_BASE_URL = "https://api.rplay.live"
    SITE_URL = "https://rplay.live"

    # Default request headers
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        ),
        "Referer": SITE_URL,
        "Origin": SITE_URL,
    }

    def __init__(self, auth_token: str, user_oid: str) -> None:
        """Initialize the API client with authentication credentials.

        Args:
            auth_token (str): JWT authentication token from user login
            user_oid (str): Unique identifier for the authenticated user

        Note:
            Both auth_token and user_oid are required for authenticated endpoints
        """
        self.auth_token = auth_token
        self.user_oid = user_oid
        self.headers = self.DEFAULT_HEADERS.copy()

    def get_livestream_status(self) -> List[LiveStream]:
        """Retrieve status information for all currently active livestreams.

        Returns:
            List[LiveStream]: A list of LiveStream objects, each containing
                information about an active stream including creator details
                and stream metadata.

        Raises:
            requests.exceptions.HTTPError: If the API request fails
        """
        response = requests.get(f"{self.API_BASE_URL}/live/livestreams")
        response.raise_for_status()
        streams_data = response.json()
        return [LiveStream(**stream) for stream in streams_data]

    def get_stream_url(self, creator_oid: str) -> str:
        """Generate the playback URL for a specific creator's livestream.

        Args:
            creator_oid (str): Unique identifier of the streamer

        Returns:
            str: Complete M3U8 format stream URL with authentication parameters

        Raises:
            requests.exceptions.HTTPError: If stream key retrieval fails
            ValueError: If authentication credentials are invalid
        """
        # Get authentication key for stream access
        stream_key = self.__get_stream_key()

        # Construct full stream URL with required parameters
        return (
            f"{self.API_BASE_URL}/live/stream/playlist.m3u8?"
            f"creatorOid={creator_oid}&key2={stream_key}"
        )

    def __get_stream_key(self) -> str:
        """Retrieve the authentication key required for stream access.

        Returns:
            str: Stream authentication key from the API

        Raises:
            requests.exceptions.HTTPError: If the API request fails
            ValueError: If auth_token or user_oid are missing or invalid
        """
        # Add authorization header to default headers
        auth_headers = self.headers.copy()
        auth_headers["Authorization"] = self.auth_token

        # Request stream key with authentication
        url = f"{self.API_BASE_URL}/live/key2?lang=en&requestorOid={self.user_oid}&loginType=plax"
        response = requests.get(url, headers=auth_headers)
        response.raise_for_status()
        return response.json()["authKey"]
