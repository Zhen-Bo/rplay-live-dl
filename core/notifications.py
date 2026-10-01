"""Best-effort Discord delivery; no network I/O on the recording control thread."""

import logging
import math
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Iterable, Optional

import requests

from core.constants import RPLAY_PROFILE_PHOTO_BASE_URL, RPLAY_SITE_URL
from core.logger import redact_sensitive_text
from models.notification import Notification, NotificationKind


@dataclass(frozen=True)
class _CardCopy:
    title: str
    color: int
    description: str = ""
    action: str = ""


_CARD_COPY = {
    NotificationKind.LIVE: _CardCopy("Live now", 0xE11D48),
    NotificationKind.OFFLINE: _CardCopy(
        "Stream ended",
        0x64748B,
        description="Recording finalization may still be in progress.",
    ),
    NotificationKind.BLOCKED: _CardCopy(
        "Stream access restricted",
        0xF59E0B,
        action="Check subscription or viewing permissions. No automatic retry for this stream.",
    ),
    NotificationKind.AUTH_FAILED: _CardCopy(
        "🔑 Authentication failed",
        0xEF4444,
        action="Update `REFRESH_TOKEN`, check `USER_OID`, and restart with the updated settings.",
    ),
    NotificationKind.DOWNLOAD_FAILED: _CardCopy(
        "Recording retries delayed",
        0x3B82F6,
        action="Retrying automatically while live. Check logs if failures continue.",
    ),
    NotificationKind.MERGE_FAILED: _CardCopy(
        "Recording merge incomplete",
        0xA855F7,
        description="Available raw fragments are kept.",
        action="Check logs, fix the cause, then restart to retry.",
    ),
    NotificationKind.MERGE_COMPLETED: _CardCopy(
        "Merge complete", 0x14B8A6, description="Recording saved as MP4."
    ),
    NotificationKind.DISK_WARNING: _CardCopy(
        "Disk space low", 0xEAB308, action="Free up space soon."
    ),
    NotificationKind.DISK_CRITICAL: _CardCopy(
        "Disk space critically low",
        0xDC2626,
        action="Free up space now. Recordings are not stopped automatically; writes may fail.",
    ),
}
_DISK_THRESHOLD_LABELS = {
    NotificationKind.DISK_WARNING: "⚠️ Warning level",
    NotificationKind.DISK_CRITICAL: "🚨 Critical level",
}
_STREAM_LINK_KINDS = frozenset({NotificationKind.LIVE, NotificationKind.BLOCKED})


def _safe_embed_value(text: str, limit: int) -> str:
    text = redact_sensitive_text(text)
    text = re.sub(r"https?://[^\s]+", "[URL REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    # Creator names and stream titles are data, not Markdown or mention markup.
    text = re.sub(r"([\\`*_~|<>\[\]])", lambda match: "\\" + match.group(0), text)
    text = text.replace("@", "@​").strip()
    encoded = text.encode("utf-16-le", errors="replace")
    if len(encoded) <= limit * 2:
        return text
    return (
        encoded[: (limit - 1) * 2].decode("utf-16-le", errors="ignore").rstrip("\\")
        + "…"
    )


def _stream_started_value(value: str) -> str:
    try:
        started = datetime.fromisoformat(value)
        if started.tzinfo is not None:
            return f"<t:{int(started.timestamp())}:f>"
    except (ValueError, OverflowError, OSError):
        pass
    return _safe_embed_value(value, 128)


def _gib(value: int) -> str:
    return f"{value / 1024**3:,.2f} GiB"


def _disk_fields(event: Notification) -> list:
    label = _DISK_THRESHOLD_LABELS.get(event.kind)
    if label is None:
        return []
    fields = []
    if event.free_bytes is not None:
        fields.append(
            {
                "name": "💾 Free space",
                "value": f"**{_gib(event.free_bytes)}**",
                "inline": False,
            }
        )
    threshold = (
        event.warning_bytes
        if event.kind == NotificationKind.DISK_WARNING
        else event.critical_bytes
    )
    if threshold is not None:
        fields.append({"name": label, "value": _gib(threshold), "inline": False})
    return fields


def format_discord_message(event: Notification) -> dict:
    """One rich embed per event; copy and layout can change without touching delivery."""
    card_copy = _CARD_COPY[event.kind]
    fields = []
    if event.kind == NotificationKind.LIVE:
        started = _stream_started_value(event.started_at)
        if started:
            fields.append({"name": "🕒 Started", "value": started, "inline": True})
    fields.extend(_disk_fields(event))
    if event.kind == NotificationKind.MERGE_COMPLETED and event.output_file:
        fields.append(
            {
                "name": "File",
                "value": _safe_embed_value(event.output_file, 1024),
                "inline": False,
            }
        )

    guidance = " ".join(
        text for text in (card_copy.description, card_copy.action) if text
    ).replace(". ", ".\n")
    stream_title = _safe_embed_value(event.title, 1024)
    description = "\n\n".join(
        text
        for text in (
            f"**🎬 Stream title**\n**{stream_title}**" if stream_title else "",
            guidance,
        )
        if text
    )

    embed: dict = {"title": card_copy.title, "color": card_copy.color}
    if description:
        embed["description"] = description
    if fields:
        embed["fields"] = fields
    creator = _safe_embed_value(event.creator, 256)
    if creator:
        embed["author"] = {"name": creator}
    # Observed public creator routes only; never accept arbitrary or signed URLs.
    if re.fullmatch(r"[0-9a-fA-F]{24}", event.creator_oid):
        creator_oid = event.creator_oid.lower()
        avatar_url = (
            f"{RPLAY_PROFILE_PHOTO_BASE_URL}/{creator_oid}-small/"
            "cdn-cgi/image/width=128,height=128,fit=cover,quality=90,format=auto"
        )
        embed["thumbnail"] = {"url": avatar_url}
        if creator:
            embed["author"]["icon_url"] = avatar_url
        if event.kind in _STREAM_LINK_KINDS:
            embed["url"] = f"{RPLAY_SITE_URL}/live/{creator_oid}"
    # Do not set SUPPRESS_EMBEDS (4): it would hide these cards.
    # Bounded fields above keep even worst-case cards well below 6000 characters.
    return {"embeds": [embed], "allowed_mentions": {"parse": []}}


class DiscordNotifier:
    def __init__(
        self,
        url: str = "",
        *,
        events: Iterable[str] = NotificationKind,
        logger: Optional[logging.Logger] = None,
        queue_size: int = 100,
    ) -> None:
        """An empty URL or event selection gives a disabled notifier that never sends."""
        self._url = url
        selected = set(events)
        self._events = frozenset(kind for kind in NotificationKind if kind in selected)
        self.logger = logger or logging.getLogger("Notifications")
        self._queue: Queue = Queue(maxsize=queue_size)
        self._lock = Lock()
        self._recent: OrderedDict = OrderedDict()
        self._stop = Event()
        self._disabled = Event()
        self._closing = False
        self._overflow_warning_at = float("-inf")
        self._next_request_at = 0.0
        self._thread: Optional[Thread] = None
        if url and self._events:
            self._thread = Thread(
                target=self._worker, name="discord-notifications", daemon=True
            )
            self._thread.start()

    def accepts(self, kind: NotificationKind) -> bool:
        """Whether this kind can be delivered at all, ignoring queue capacity."""
        return (
            self._thread is not None
            and not self._disabled.is_set()
            and kind in self._events
        )

    def notify(
        self, event: Notification, *, key: str = "", cooldown: float = 3600
    ) -> bool:
        """Queue one event. Never raises, so recording paths can call it directly."""
        if not self.accepts(event.kind):
            return False
        try:
            return self._enqueue(event, key, cooldown)
        except Exception:
            self.logger.warning("Could not queue Discord notification; message dropped")
            return False

    def _enqueue(self, event: Notification, key: str, cooldown: float) -> bool:
        with self._lock:
            if self._closing:
                return False
            now = time.monotonic()
            identity = (event.kind, key)
            if (
                cooldown > 0
                and now - self._recent.get(identity, float("-inf")) < cooldown
            ):
                return False
            payload = format_discord_message(event)
            try:
                self._queue.put_nowait(payload)
            except Full:
                if now - self._overflow_warning_at >= 60:
                    self.logger.warning(
                        "Discord queue full; dropping notifications without blocking recording"
                    )
                    self._overflow_warning_at = now
                return False
            if cooldown > 0:
                self._recent[identity] = now
                self._recent.move_to_end(identity)
                if len(self._recent) > 1024:
                    self._recent.popitem(last=False)
            return True

    def close(self, timeout: float = 5) -> None:
        with self._lock:
            self._closing = True
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                self._stop.set()
                self.logger.warning(
                    "Discord shutdown deadline reached; pending notifications may be lost"
                )

    def _worker(self) -> None:
        with requests.Session() as session:
            while not self._stop.is_set():
                try:
                    payload = self._queue.get(timeout=0.1)
                except Empty:
                    if self._closing:
                        return
                    continue
                try:
                    if not self._disabled.is_set():
                        self._deliver(session, payload)
                except Exception:
                    # Never log exception text: requests exceptions can contain the URL token.
                    self.logger.warning(
                        "Discord delivery failed unexpectedly; message dropped"
                    )
                finally:
                    self._queue.task_done()

    @staticmethod
    def _seconds(value, default: float = 5) -> float:
        try:
            seconds = float(value)
            return seconds if math.isfinite(seconds) and seconds >= 0 else default
        except (TypeError, ValueError):
            return default

    def _deliver(self, session: requests.Session, payload: dict) -> bool:
        for attempt in range(3):
            delay = max(0, self._next_request_at - time.monotonic())
            if self._stop.wait(delay):
                return False
            try:
                with session.post(
                    self._url,
                    params={"wait": "true"},
                    json=payload,
                    timeout=(3.05, 7),
                    allow_redirects=False,
                ) as response:
                    status = response.status_code
                    if response.headers.get("X-RateLimit-Remaining") == "0":
                        self._next_request_at = time.monotonic() + self._seconds(
                            response.headers.get("X-RateLimit-Reset-After")
                        )
                    if 200 <= status < 300:
                        return True
                    if status in {401, 403, 404}:
                        self._disabled.set()
                        self.logger.warning(
                            "Discord webhook unavailable (HTTP %s); disabled until restart",
                            status,
                        )
                        return False
                    if status == 429:
                        retry_after = response.headers.get("Retry-After")
                        try:
                            body = response.json()
                            if isinstance(body, dict):
                                retry_after = body.get("retry_after", retry_after)
                        except ValueError:
                            pass
                        self._next_request_at = max(
                            self._next_request_at,
                            time.monotonic() + self._seconds(retry_after),
                        )
                        continue
                    if status < 500:
                        self.logger.warning(
                            "Discord message rejected (HTTP %s); not retrying", status
                        )
                        return False
            except requests.RequestException:
                pass
            self._next_request_at = max(
                self._next_request_at, time.monotonic() + 2**attempt
            )
        self.logger.warning(
            "Discord delivery exhausted three attempts; message dropped"
        )
        return False
