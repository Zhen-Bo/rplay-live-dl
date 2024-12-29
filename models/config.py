from pydantic import BaseModel, Field


class CreatorProfile(BaseModel):
    """
    Data model for creator profile configuration.
    Contains essential information about a content creator including their name and unique identifier.
    """

    creator_name: str = Field(..., description="Creator Name")
    creator_oid: str = Field(
        ...,
        description="Creator OID",
    )

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "creator_name": "Test Creator",
                "creator_oid": "7a2b3c4d5e6f7g8h9i0j1k2l",
            }
        }
