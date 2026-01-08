"""
RPlay API response models.

Defines Pydantic models for data structures returned by the RPlay API,
including live stream information and related entities.
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class StreamState(str, Enum):
    """
    Enumeration of possible stream states.

    Indicates the current state/platform of the stream.
    """

    LIVE = "live"  # Native RPlay platform streaming
    TWITCH = "twitch"  # Twitch platform streaming
    YOUTUBE = "youtube"  # YouTube platform streaming

    def __str__(self) -> str:
        """Return the string value of the enum."""
        return self.value


class MultiLangNick(BaseModel):
    """
    Multi-language nickname model.

    Stores creator nicknames in different languages for
    internationalization support.

    Attributes:
        ko: Korean nickname
        en: English nickname
        jp: Japanese nickname
    """

    ko: Optional[str] = Field(default=None, description="Korean nickname")
    en: Optional[str] = Field(default=None, description="English nickname")
    jp: Optional[str] = Field(default=None, description="Japanese nickname")

    def get_display_name(self, preferred_lang: str = "en") -> Optional[str]:
        """
        Get display name in preferred language, with fallback.

        Args:
            preferred_lang: Preferred language code (ko, en, jp)

        Returns:
            Nickname in preferred language, or first available, or None
        """
        lang_map = {"ko": self.ko, "en": self.en, "jp": self.jp}

        # Try preferred language first
        if preferred_lang in lang_map and lang_map[preferred_lang]:
            return lang_map[preferred_lang]

        # Fall back to any available
        for nickname in [self.en, self.ko, self.jp]:
            if nickname:
                return nickname

        return None


class LiveStream(BaseModel):
    """
    Core model representing a live streaming session.

    Contains all essential information about an active stream including
    creator details, stream metadata, and platform-specific information.

    Attributes:
        id_: Internal MongoDB identifier
        oid: Unique stream identifier
        creator_oid: Unique identifier for the creator
        creator_nickname: Display name of the creator
        creator_multi_lang_nick: Creator's nicknames in different languages
        title: Stream title
        description: Optional stream description
        hashtags: List of stream-related tags
        is_adult_content: Flag for mature content
        viewer_count: Current number of viewers
        multi_platform_key: Creator identifier on external platforms
        channel_language: Two-letter language code (ISO 639-1)
        stream_start_time: UTC timestamp when stream started
        stream_state: Current streaming platform/state
    """

    id_: str = Field(alias="_id")
    oid: str
    creator_oid: str = Field(alias="creatorOid")
    creator_nickname: str = Field(alias="creatorNickname")
    creator_multi_lang_nick: MultiLangNick = Field(
        default_factory=MultiLangNick,
        alias="creatorMultiLangNick",
    )
    title: str
    description: Optional[str] = Field(default=None)
    hashtags: List[str] = Field(default_factory=list)
    is_adult_content: bool = Field(alias="isAdultContent", default=False)
    viewer_count: int = Field(alias="viewerCount", default=0)
    multi_platform_key: str = Field(alias="multiPlatformKey", default="")
    channel_language: str = Field(alias="channelLanguage", default="en")
    stream_start_time: datetime = Field(alias="streamStartTime")
    stream_state: StreamState = Field(alias="streamState")

    model_config = {
        "populate_by_name": True,
        "str_strip_whitespace": True,
    }

    @property
    def is_live(self) -> bool:
        """Check if the stream is currently live on native platform."""
        return self.stream_state == StreamState.LIVE

    @property
    def duration_seconds(self) -> float:
        """Calculate stream duration in seconds from start time."""
        return (datetime.now(self.stream_start_time.tzinfo) - self.stream_start_time).total_seconds()

    def __str__(self) -> str:
        """Return string representation of the live stream."""
        return f"LiveStream({self.creator_nickname}: {self.title!r})"

    def __repr__(self) -> str:
        """Return detailed representation of the live stream."""
        return (
            f"LiveStream(creator={self.creator_nickname!r}, "
            f"title={self.title!r}, state={self.stream_state.value})"
        )
