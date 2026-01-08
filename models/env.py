"""
Environment configuration model.

Defines the Pydantic Settings model for environment-based configuration
used by the rplay-live-dl application.
"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvConfig(BaseSettings):
    """
    Environment configuration model using pydantic-settings.

    Automatically loads values from environment variables and .env file.
    All fields are validated using Pydantic for type safety and constraints.

    Attributes:
        auth_token: JWT authentication token for RPlay API access
        user_oid: User's unique identifier on the RPlay platform
        interval: Monitoring check interval in seconds
    """

    auth_token: str = Field(
        ...,
        description="JWT authentication token for API access",
        min_length=1,
    )
    user_oid: str = Field(
        ...,
        description="User's unique identifier (OID)",
        min_length=1,
    )
    interval: int = Field(
        default=60,
        description="Check interval in seconds",
        ge=10,  # Minimum 10 seconds
        le=3600,  # Maximum 1 hour
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        str_strip_whitespace=True,
        extra="ignore",
    )

    @field_validator("auth_token")
    @classmethod
    def validate_auth_token(cls, v: str) -> str:
        """Validate that auth token is not just whitespace."""
        if not v.strip():
            raise ValueError("AUTH_TOKEN cannot be empty or whitespace")
        return v.strip()

    @field_validator("user_oid")
    @classmethod
    def validate_user_oid(cls, v: str) -> str:
        """Validate that user OID is not just whitespace."""
        if not v.strip():
            raise ValueError("USER_OID cannot be empty or whitespace")
        return v.strip()
