"""
Shared constants for rplay-live-dl.

Centralizes configuration values used across multiple modules
to ensure consistency and ease of maintenance.
"""

# RPlay platform URLs
RPLAY_SITE_URL = "https://rplay.live"
RPLAY_API_BASE_URL = "https://api.rplay-cdn.com"

# Default User-Agent for HTTP requests
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# Default HTTP headers for RPlay API requests
DEFAULT_HTTP_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Referer": RPLAY_SITE_URL,
    "Origin": RPLAY_SITE_URL,
}

# Request configuration
DEFAULT_REQUEST_TIMEOUT = 30  # seconds
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_FACTOR = 0.5
RETRY_STATUS_CODES = [429, 500, 502, 503, 504]

# Download configuration
DEFAULT_DOWNLOAD_RETRIES = 10
DEFAULT_FRAGMENT_RETRIES = 10

__all__ = [
    "RPLAY_SITE_URL",
    "RPLAY_API_BASE_URL",
    "DEFAULT_USER_AGENT",
    "DEFAULT_HTTP_HEADERS",
    "DEFAULT_REQUEST_TIMEOUT",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RETRY_BACKOFF_FACTOR",
    "RETRY_STATUS_CODES",
    "DEFAULT_DOWNLOAD_RETRIES",
    "DEFAULT_FRAGMENT_RETRIES",
]
