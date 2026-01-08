"""
Creator profile configuration model.

Defines the Pydantic model for creator profile data used in the
configuration file for monitoring specific content creators.
"""

from pydantic import BaseModel, Field, field_validator


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
