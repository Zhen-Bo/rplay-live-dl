"""
Shared constants for rplay-live-dl.

Centralizes configuration values used across multiple modules
to ensure consistency and ease of maintenance.
"""

# RPlay platform URLs
RPLAY_SITE_URL = "https://rplay.live"
DEFAULT_RPLAY_API_BASE_URL = "https://api.rplay.live"

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
DEFAULT_DOWNLOAD_SOCKET_TIMEOUT = 10
DEFAULT_DOWNLOAD_TASK_RETRY_BACKOFF_FACTOR = 2.0

# Logging configuration (env defaults; single source of truth)
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_MAX_SIZE_MB = 5
DEFAULT_LOG_BACKUP_COUNT = 5
DEFAULT_LOG_RETENTION_DAYS = 30
DEFAULT_LOG_YTDLP_INTERNAL = False

# Minimum free disk space (GiB) before starting a recording; 0 disables
DEFAULT_MIN_FREE_DISK_GB = 5.0

# Poll interval default (INTERVAL env); shared by EnvConfig and the health probe
DEFAULT_INTERVAL = 60

# Docker healthcheck heartbeat file (touched once per monitor poll cycle)
HEARTBEAT_FILE_PATH = "/tmp/rplay-live-dl-heartbeat"
HEARTBEAT_STALE_MULTIPLIER = 3

__all__ = [
    "RPLAY_SITE_URL",
    "DEFAULT_RPLAY_API_BASE_URL",
    "DEFAULT_USER_AGENT",
    "DEFAULT_HTTP_HEADERS",
    "DEFAULT_REQUEST_TIMEOUT",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RETRY_BACKOFF_FACTOR",
    "RETRY_STATUS_CODES",
    "DEFAULT_DOWNLOAD_RETRIES",
    "DEFAULT_FRAGMENT_RETRIES",
    "DEFAULT_DOWNLOAD_SOCKET_TIMEOUT",
    "DEFAULT_DOWNLOAD_TASK_RETRY_BACKOFF_FACTOR",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_LOG_MAX_SIZE_MB",
    "DEFAULT_LOG_BACKUP_COUNT",
    "DEFAULT_LOG_RETENTION_DAYS",
    "DEFAULT_LOG_YTDLP_INTERNAL",
    "DEFAULT_MIN_FREE_DISK_GB",
    "DEFAULT_INTERVAL",
    "HEARTBEAT_FILE_PATH",
    "HEARTBEAT_STALE_MULTIPLIER",
]
