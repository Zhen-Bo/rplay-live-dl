"""
Creator profile configuration model.

Defines the Pydantic models used by the YAML configuration file,
including monitored creators and application-level settings.
"""

from pydantic import BaseModel, Field, field_validator


class AppConfig(BaseModel):
    """Application-level configuration loaded from config.yaml."""

    api_base_url: str = Field(..., description="Base URL for the RPlay API")
    creators: list["CreatorProfile"] = Field(
        default_factory=list,
        description="Configured creators to monitor",
    )

    @field_validator("api_base_url")
    @classmethod
    def validate_api_base_url(cls, value: str) -> str:
        """Validate and normalize api_base_url."""
        sanitized = value.strip().rstrip("/")
        if not sanitized:
            raise ValueError("api_base_url cannot be empty or whitespace")
        return sanitized


class CreatorProfile(BaseModel):
    """
    Data model for creator profile configuration.

    Contains essential information about a content creator including
    their display name and unique identifier on the RPlay platform.

    Attributes:
        creator_name: Display name of the creator (used for folder naming)
        creator_oid: Unique identifier (OID) of the creator on RPlay
    """

    creator_name: str = Field(
        ...,
        description="Display name of the creator",
        min_length=1,
        max_length=100,
    )
    creator_oid: str = Field(
        ...,
        description="Unique identifier (OID) of the creator",
        min_length=1,
    )

    @field_validator("creator_name")
    @classmethod
    def validate_creator_name(cls, v: str) -> str:
        """Validate and sanitize creator name."""
        sanitized = v.strip()
        if not sanitized:
            raise ValueError("creator_name cannot be empty or whitespace")
        return sanitized

    @field_validator("creator_oid")
    @classmethod
    def validate_creator_oid(cls, v: str) -> str:
        """Validate creator OID."""
        sanitized = v.strip()
        if not sanitized:
            raise ValueError("creator_oid cannot be empty or whitespace")
        return sanitized

    model_config = {
        "from_attributes": True,
        "str_strip_whitespace": True,
        "json_schema_extra": {
            "example": {
                "creator_name": "Example Creator",
                "creator_oid": "7a2b3c4d5e6f7g8h9i0j1k2l",
            }
        },
    }

    def __str__(self) -> str:
        """Return string representation of the creator profile."""
        return f"CreatorProfile(name={self.creator_name!r}, oid={self.creator_oid!r})"

    def __repr__(self) -> str:
        """Return detailed representation of the creator profile."""
        return self.__str__()


AppConfig.model_rebuild()
