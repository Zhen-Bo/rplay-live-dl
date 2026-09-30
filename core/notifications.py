"""Best-effort Discord delivery; no network I/O on the recording control thread."""

import logging
import math
import re
import time
from collections import OrderedDict
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Iterable, Optional

import requests

from models.notification import EVENT_KINDS, Notification

_LABELS = {
    "live": "Live stream detected",
    "blocked": "Stream inaccessible (possibly paid/private)",
    "auth_failed": "RPlay credentials rejected",
    "download_failed": "Recording repeatedly failed",
    "merge_failed": "Merge failed; raw recordings retained",
    "disk_warning": "Disk capacity warning",
    "disk_critical": "Disk capacity CRITICAL",
    "disk_recovered": "Disk capacity recovered",
}


def format_discord_message(event: Notification, secrets: Iterable[str] = ()) -> dict:
    """The single place to change presentation later; never enables mentions."""
    lines = [f"[rplay-live-dl] {_LABELS[event.kind]}"]
    for label, value in (
        ("Creator", event.creator),
        ("Title", event.title),
        ("Stream started (UTC)", event.started_at),
        ("Details", event.detail),
    ):
        if value:
            lines.append(f"{label}: {value}")
    text = "\n".join(lines)
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    # No authenticated URLs or arbitrary upstream exception bodies in messages.
    text = re.sub(r"https?://[^\s]+", "[URL REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?i)(key2|refresh[_-]?token|access[_-]?token)\s*[=:]\s*[^\s&]+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    # Bound UTF-16 units too, including emoji; leave headroom below 2000.
    text = text.encode("utf-16-le", errors="replace")[:3800].decode(
        "utf-16-le", errors="ignore"
    )
    return {"content": text, "allowed_mentions": {"parse": []}, "flags": 4}


class DiscordNotifier:
    def __init__(
        self,
        url: str = "",
        *,
        events: Iterable[str] = EVENT_KINDS,
        secrets: Iterable[str] = (),
        logger: Optional[logging.Logger] = None,
        queue_size: int = 100,
    ) -> None:
        self._url = url
        self._events = frozenset(events) & EVENT_KINDS
        self._secrets = tuple(secrets) + (url, url.rsplit("/", 1)[-1] if url else "")
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

    def notify(
        self, event: Notification, *, key: str = "", cooldown: float = 3600
    ) -> bool:
        if (
            self._thread is None
            or self._disabled.is_set()
            or event.kind not in self._events
        ):
            return False
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
            payload = format_discord_message(event, self._secrets)
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
