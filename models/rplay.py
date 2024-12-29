from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated


class StreamState(str, Enum):
    """
    Enumeration of possible streaming platforms.
    Indicates where the stream is being broadcasted.
    """

    LIVE = "live"  # Native platform streaming
    TWITCH = "twitch"  # Twitch platform streaming
    YOUTUBE = "youtube"  # YouTube platform streaming


class MultiLangNick(BaseModel):
    """
    Multi-language nickname model.
    Stores creator nicknames in different languages.
    """

    ko: Optional[str] = None  # Korean nickname
    en: Optional[str] = None  # English nickname
    jp: Optional[str] = None  # Japanese nickname


class LiveStream(BaseModel):
    """
    Core model representing a live streaming session.
    Contains all essential information about an active stream including
    creator details, stream metadata, and platform-specific information.

    Attributes:
        _id: Unique identifier for the stream
        oid: Unique identifier for the stream
        creator_oid: Unique identifier for the creator
        creator_nickname: Display name of the creator
        creator_multi_lang_nick: Creator's nicknames in different languages
        title: Stream title
        description: Optional stream description
        hashtags: List of stream-related tags
        is_adult_content: Flag for mature content
        viewer_count: Current number of viewers
        multi_platform_key: creator name on the other platform
        channel_language: Two-letter language code
        stream_start_time: Timestamp when stream started
        stream_state: Current streaming platform
    """

    _id: str
    oid: str
    creator_oid: str = Field(alias="creatorOid")
    creator_nickname: str = Field(alias="creatorNickname")
    creator_multi_lang_nick: MultiLangNick = Field(
        default_factory=MultiLangNick, alias="creatorMultiLangNick"
    )
    title: str
    description: Optional[str] = None
    hashtags: List[str]
    is_adult_content: bool = Field(alias="isAdultContent")
    viewer_count: int = Field(alias="viewerCount")
    multi_platform_key: str = Field(alias="multiPlatformKey")
    channel_language: Annotated[str, StringConstraints(pattern="^[a-z]{2}$")] = Field(
        alias="channelLanguage"
    )
    stream_start_time: datetime = Field(alias="streamStartTime")
    stream_state: StreamState = Field(alias="streamState")

    class Config:
        populate_by_name = True
