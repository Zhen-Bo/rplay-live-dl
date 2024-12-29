from pydantic import BaseModel, Field


class EnvConfig(BaseModel):
    """
    Environment configuration model.
    Handles authentication and application settings for the downloader.
    """

    auth_token: str = Field(..., description="Authentication token for API access")
    user_oid: str = Field(..., description="User ID")
    interval: int = Field(
        default=10,
        description="Check interval in seconds",
    )
