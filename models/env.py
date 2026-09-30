"""Environment settings model for rplay-live-dl."""

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.constants import (
    DEFAULT_INTERVAL,
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOG_MAX_SIZE_MB,
    DEFAULT_LOG_RETENTION_DAYS,
    DEFAULT_LOG_YTDLP_INTERNAL,
    DEFAULT_MIN_FREE_DISK_GB,
    DEFAULT_TOKEN_REFRESH_LEEWAY_SECONDS,
)

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_TRUTHY_BOOL_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSY_BOOL_VALUES = frozenset({"0", "false", "no", "off", ""})


class EnvConfig(BaseSettings):
    """Settings loaded from environment variables and the .env file."""

    user_oid: str = Field(
        ...,
        description="User's unique identifier (OID)",
        min_length=1,
    )
    auth_token: str = Field(
        default="",
        description="No longer supported; read only to explain the migration",
        repr=False,
    )
    refresh_token: str = Field(
        default="",
        description="Required credential for acquiring and renewing access JWTs",
        repr=False,
    )
    token_refresh_leeway_seconds: int = Field(
        default=DEFAULT_TOKEN_REFRESH_LEEWAY_SECONDS,
        description="Refresh before key2 when the JWT has fewer seconds remaining",
        ge=0,
    )
    interval: int = Field(
        default=DEFAULT_INTERVAL,
        description="Check interval in seconds",
        ge=10,
        le=3600,
    )

    log_level: str = Field(
        default=DEFAULT_LOG_LEVEL,
        description="Application log level name",
    )
    log_ytdlp_internal: bool = Field(
        default=DEFAULT_LOG_YTDLP_INTERNAL,
        description="Surface yt-dlp internal debug chatter",
    )
    log_max_size_mb: int = Field(
        default=DEFAULT_LOG_MAX_SIZE_MB,
        description="Maximum log file size in MB before rotation",
        ge=1,
        le=100,
    )
    log_backup_count: int = Field(
        default=DEFAULT_LOG_BACKUP_COUNT,
        description="Number of backup log files to keep",
        ge=1,
        le=50,
    )
    log_retention_days: int = Field(
        default=DEFAULT_LOG_RETENTION_DAYS,
        description="Days to retain old log files",
        ge=1,
        le=365,
    )
    min_free_disk_gb: float = Field(
        default=DEFAULT_MIN_FREE_DISK_GB,
        description="Minimum free disk space in GiB before starting a recording; 0 disables",
        ge=0,
        allow_inf_nan=False,
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        str_strip_whitespace=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    @field_validator("refresh_token")
    @classmethod
    def validate_credentials(cls, v: str, info: ValidationInfo) -> str:
        """Require REFRESH_TOKEN, and explain the removal of AUTH_TOKEN."""
        if v:
            return v
        if info.data.get("auth_token"):
            raise ValueError(
                "AUTH_TOKEN is no longer supported. "
                "Set REFRESH_TOKEN in .env instead (see README, Account credentials)"
            )
        raise ValueError("Set REFRESH_TOKEN in .env")

    @field_validator("user_oid")
    @classmethod
    def validate_user_oid(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("USER_OID cannot be empty or whitespace")
        return v.strip()

    @field_validator("log_level", mode="after")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        normalized = v.upper()
        if normalized not in _VALID_LOG_LEVELS:
            raise ValueError(
                f"LOG_LEVEL must be one of {', '.join(sorted(_VALID_LOG_LEVELS))}; "
                f"got {v!r}"
            )
        return normalized

    @field_validator("log_ytdlp_internal", mode="before")
    @classmethod
    def validate_log_ytdlp_internal(cls, v: object) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return DEFAULT_LOG_YTDLP_INTERNAL
        normalized = str(v).strip().lower()
        if normalized in _TRUTHY_BOOL_VALUES:
            return True
        if normalized in _FALSY_BOOL_VALUES:
            return False
        raise ValueError(
            "LOG_YTDLP_INTERNAL must be one of "
            "1, true, yes, on, 0, false, no, off (or empty); "
            f"got {v!r}"
        )
