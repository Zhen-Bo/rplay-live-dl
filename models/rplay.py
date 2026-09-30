"""Models for data returned by the RPlay API."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


@dataclass
class CreatorStreamState:
    """Handled stream start time and blocked flag (likely paid content) for one creator."""

    last_stream_start_time: Optional[datetime] = field(default=None)
    is_current_stream_blocked: bool = field(default=False)

    def update_stream_start_time(self, start_time: datetime) -> None:
        """Set the handled start time and clear the blocked flag so the new session is retried."""
        self.last_stream_start_time = start_time
        self.is_current_stream_blocked = False

    def mark_blocked(self) -> None:
        self.is_current_stream_blocked = True


class StreamState(str, Enum):
    LIVE = "live"
    TWITCH = "twitch"
    YOUTUBE = "youtube"

    def __str__(self) -> str:
        return self.value


class MultiLangNick(BaseModel):
    ko: Optional[str] = Field(default=None, description="Korean nickname")
    en: Optional[str] = Field(default=None, description="English nickname")
    jp: Optional[str] = Field(default=None, description="Japanese nickname")


class LiveStream(BaseModel):
    """Live stream as returned by the API. stream_start_time is UTC."""

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

    def __str__(self) -> str:
        return f"LiveStream({self.creator_nickname}: {self.title!r})"

    def __repr__(self) -> str:
        return (
            f"LiveStream(creator={self.creator_nickname!r}, "
            f"title={self.title!r}, state={self.stream_state.value})"
        )
