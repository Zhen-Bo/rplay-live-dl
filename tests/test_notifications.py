import json
import logging
from datetime import datetime, timezone
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
import responses

from core.disk_space import DiskAlert
from core.live_stream_monitor import LiveStreamMonitor
from core.notifications import DiscordNotifier, format_discord_message
from core.rplay import RPlayAPIError
from models.config import CreatorProfile
from models.download import (
    DownloadSession,
    MergeCompleted,
    MergeFailed,
    RawDownloadBlocked,
    RawDownloadFailed,
    SessionState,
)
from models.env import EnvConfig
from models.notification import Notification
from models.rplay import StreamState

URL = "https://discord.com/api/webhooks/123456/test-token"


def test_embed_redacts_secrets_disables_mentions_and_bounds_emoji():
    event = Notification(
        "live",
        creator="@everyone **spoofed heading** <@123456>",
        title="private-value key2=hidden https://example.test/?token=secret "
        + "😀" * 2000,
    )
    payload = format_discord_message(event, secrets=("private-value",))
    assert payload["allowed_mentions"] == {"parse": []}
    assert not payload.get("flags", 0) & 4
    assert "content" not in payload
    assert len(payload["embeds"]) == 1
    card = payload["embeds"][0]
    assert "private-value" not in card["description"]
    assert "hidden" not in card["description"]
    assert "https://" not in card["description"]
    assert "@everyone" not in card["author"]["name"]
    assert "**spoofed heading**" not in card["author"]["name"]
    assert len(card["title"].encode("utf-16-le")) <= 512
    assert len(card["description"].encode("utf-16-le")) <= 8192


@pytest.mark.parametrize(
    "kind,color",
    [
        ("live", 0xE11D48),
        ("offline", 0x64748B),
        ("blocked", 0xF59E0B),
        ("auth_failed", 0xEF4444),
        ("download_failed", 0x3B82F6),
        ("merge_failed", 0xA855F7),
        ("disk_warning", 0xEAB308),
        ("disk_critical", 0xDC2626),
        ("merge_completed", 0x14B8A6),
    ],
)
def test_all_event_cards_fit_discord_limits_with_long_input(kind, color):
    long_text = "😀*_\n" * 3000
    payload = format_discord_message(
        Notification(
            kind,
            creator=long_text,
            title=long_text,
            started_at=long_text,
            detail=long_text,
            free_bytes=3 * 1024**3,
            warning_bytes=30 * 1024**3,
            critical_bytes=10 * 1024**3,
            recovery_bytes=32 * 1024**3,
        )
    )
    card = payload["embeds"][0]
    units = lambda text: len(text.encode("utf-16-le")) // 2
    assert units(card["title"]) <= 256
    assert units(card["description"]) <= 4096
    assert "footer" not in card
    if kind == "auth_failed":
        assert card["title"] == "🔑 Authentication failed"
    else:
        assert card["title"].isascii()
    assert card["description"].startswith("**")
    assert not card["description"][2].isascii()
    assert units(card["author"]["name"]) <= 256
    assert card["color"] == color
    assert "timestamp" not in card
    fields = card.get("fields", [])
    assert len(fields) <= 25
    for field in fields:
        assert 0 < units(field["name"]) <= 256
        assert 0 < units(field["value"]) <= 1024
    total = sum(units(card[key]) for key in ("title", "description"))
    total += units(card["author"]["name"])
    total += sum(units(field[part]) for field in fields for part in ("name", "value"))
    assert total <= 6000


@pytest.mark.parametrize(
    "started_at", ["2026-09-30T12:00:00Z", "2026-09-30T20:00:00+08:00"]
)
def test_stream_start_uses_native_timestamp_and_compact_layout(started_at):
    card = format_discord_message(
        Notification(
            "live",
            creator="Creator",
            title="Stream title",
            started_at=started_at,
        )
    )["embeds"][0]
    fields = card["fields"]
    timestamp = int(datetime(2026, 9, 30, 12, tzinfo=timezone.utc).timestamp())
    assert fields == [
        {"name": "🕒 Started", "value": f"<t:{timestamp}:f>", "inline": True},
    ]
    assert card["author"]["name"] == "Creator"
    assert card["title"] == "Live now"
    assert card["description"] == "**🎬 Stream title**\n**Stream title**"


@pytest.mark.parametrize(
    "kind,label,value",
    [
        ("disk_warning", "⚠️ Warning level", "30.00 GiB"),
        ("disk_critical", "🚨 Critical level", "10.00 GiB"),
    ],
)
def test_disk_cards_show_remaining_space_and_only_relevant_level(kind, label, value):
    card = format_discord_message(
        Notification(
            kind,
            free_bytes=3 * 1024**3,
            warning_bytes=30 * 1024**3,
            critical_bytes=10 * 1024**3,
            recovery_bytes=32 * 1024**3,
            detail="Unnecessary diagnostic context",
            started_at="2026-09-30T12:00:00Z",
        )
    )["embeds"][0]
    assert card["fields"] == [
        {"name": "💾 Free space", "value": "**3.00 GiB**", "inline": False},
        {"name": label, "value": value, "inline": False},
    ]
    assert "Unnecessary" not in json.dumps(card)


def test_ended_stream_distinguishes_stream_end_from_recording_completion():
    card = format_discord_message(
        Notification(
            "offline",
            creator="Creator",
            title="Stream title",
            started_at="2026-09-30T12:00:00Z",
            detail="Simulated test only",
        )
    )["embeds"][0]
    assert (
        card["description"]
        == "**🎬 Stream title**\n**Stream title**\n\nRecording finalization may still be in progress."
    )
    assert "fields" not in card


@pytest.mark.parametrize(
    "kind",
    [
        "live",
        "offline",
        "blocked",
        "download_failed",
        "merge_failed",
        "merge_completed",
    ],
)
def test_creator_cards_include_safe_public_avatar_and_links(kind):
    creator_oid = "0123456789abcdef01234567"
    card = format_discord_message(
        Notification(kind, creator="Creator", creator_oid=creator_oid)
    )["embeds"][0]
    avatar = f"https://pb3.rplay.live/profilePhoto/{creator_oid}-small/cdn-cgi/image/width=128,height=128,fit=cover,quality=90,format=auto"
    assert card["thumbnail"] == {"url": avatar}
    if kind in {"live", "blocked"}:
        stream_url = f"https://rplay.live/live/{creator_oid}"
        assert card["url"] == stream_url
        assert card["author"] == {
            "name": "Creator",
            "icon_url": avatar,
        }
    else:
        assert "url" not in card
        assert card["author"] == {"name": "Creator", "icon_url": avatar}


@pytest.mark.parametrize(
    "creator_oid",
    [
        "",
        "../secret",
        "https://example.test/photo",
        "0123456789abcdef01234567?token=secret",
    ],
)
def test_unknown_creator_identity_omits_image_without_breaking_card(creator_oid):
    card = format_discord_message(
        Notification("live", creator="Creator", creator_oid=creator_oid)
    )["embeds"][0]
    assert card["author"] == {"name": "Creator"}
    assert "thumbnail" not in card
    assert "url" not in card


def test_disk_cards_preserve_zero_and_omit_unknown_metrics():
    card = format_discord_message(Notification("disk_critical", free_bytes=0))[
        "embeds"
    ][0]
    fields = {field["name"]: field["value"] for field in card["fields"]}
    assert fields["💾 Free space"] == "**0.00 GiB**"
    assert "Warning threshold" not in fields
    assert "Critical threshold" not in fields
    assert "Clear alert at" not in fields
    assert "Creator" not in fields
    assert "Context" not in fields


@pytest.mark.parametrize(
    "url",
    [
        "http://discord.com/api/webhooks/1/secret",
        "https://discord.com.attacker.test/api/webhooks/1/secret",
        "https://discord.com/api/webhooks/1/secret?thread_id=123",
    ],
)
def test_invalid_webhook_is_rejected_without_exposing_token(url):
    with pytest.raises(ValueError) as exc:
        EnvConfig(user_oid="u", refresh_token="t", discord_webhook_url=url)
    assert url not in str(exc.value)


def test_webhook_setting_is_secret_and_event_filter_is_validated():
    env = EnvConfig(user_oid="u", refresh_token="t", discord_webhook_url=URL)
    assert "test-token" not in repr(env)
    assert "test-token" not in env.model_dump_json()
    with pytest.raises(ValueError, match="unsupported event"):
        EnvConfig(user_oid="u", refresh_token="t", discord_webhook_events="unknown")


def test_retired_recovery_filter_is_ignored_and_merge_completion_defaults_on():
    env = EnvConfig(user_oid="u", refresh_token="t")
    assert "merge_completed" in env.discord_webhook_events.split(",")
    assert "disk_recovered" not in env.discord_webhook_events
    env = EnvConfig(
        user_oid="u",
        refresh_token="t",
        discord_webhook_events="disk_recovered,disk_warning",
    )
    assert env.discord_webhook_events == "disk_warning"


@pytest.mark.parametrize(
    "kind",
    [
        "blocked",
        "auth_failed",
        "download_failed",
        "merge_failed",
        "merge_completed",
        "disk_warning",
        "disk_critical",
    ],
)
def test_guidance_sentences_are_on_separate_lines(kind):
    card = format_discord_message(Notification(kind))["embeds"][0]
    assert ". " not in card["description"]
    if kind == "blocked":
        assert (
            card["description"]
            == "Check subscription or viewing permissions.\nNo automatic retry for this stream."
        )


def test_merge_completion_file_is_safe_and_extension_is_not_split():
    card = format_discord_message(
        Notification(
            "merge_completed",
            output_file="@everyone **stream**.mp4",
        )
    )["embeds"][0]
    value = card["fields"][0]["value"]
    assert card["fields"][0]["name"] == "File"
    assert value.endswith(".mp4") and "\n" not in value
    assert "@everyone" not in value and "**stream**" not in value


def test_disabled_notifier_starts_no_thread():
    notifier = DiscordNotifier()
    assert notifier._thread is None
    assert not notifier.notify(Notification("live"))
    notifier.close()
    filtered = DiscordNotifier(URL, events=[""])
    assert filtered._thread is None
    filtered.close()


def test_queue_is_nonblocking_bounded_and_deduplicated(monkeypatch):
    entered, release = Event(), Event()

    def deliver(self, session, payload):
        entered.set()
        release.wait(3)
        return True

    monkeypatch.setattr(DiscordNotifier, "_deliver", deliver)
    notifier = DiscordNotifier(URL, events=["live"], queue_size=1)
    try:
        assert not notifier.notify(Notification("blocked"))
        assert notifier.notify(Notification("live"), key="one")
        assert entered.wait(1)
        assert not notifier.notify(Notification("live"), key="one")
        assert notifier.notify(Notification("live"), key="two")
        assert not notifier.notify(Notification("live"), key="three")
    finally:
        release.set()
        notifier.close()
    assert not notifier._thread.is_alive()
    assert not notifier.notify(Notification("live"), key="four")


def transport(monkeypatch):
    notifier = DiscordNotifier()
    notifier._url = URL
    waits = []
    monkeypatch.setattr("core.notifications.time.monotonic", lambda: 100.0)
    monkeypatch.setattr(
        notifier._stop, "wait", lambda delay: waits.append(delay) or False
    )
    return notifier, waits


@responses.activate
def test_rate_limit_waits_then_sends_safe_payload(monkeypatch):
    notifier, waits = transport(monkeypatch)
    responses.post(URL, status=429, json={"retry_after": 2.5})
    responses.post(URL, status=200, json={"id": "message"})
    payload = format_discord_message(Notification("disk_critical", detail="3 GiB free"))
    with requests.Session() as session:
        assert notifier._deliver(session, payload)
    assert waits == [0, 2.5]
    assert len(responses.calls) == 2
    assert responses.calls[1].request.url.endswith("?wait=true")
    assert json.loads(responses.calls[1].request.body) == payload


@responses.activate
def test_successful_bucket_exhaustion_delays_next_message(monkeypatch):
    notifier, waits = transport(monkeypatch)
    responses.post(
        URL,
        status=200,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-After": "1.5"},
    )
    responses.post(URL, status=200)
    with requests.Session() as session:
        assert notifier._deliver(session, {"content": "first"})
        assert notifier._deliver(session, {"content": "second"})
    assert waits == [0, 1.5]


@responses.activate
@pytest.mark.parametrize("status", [401, 403, 404])
def test_unusable_webhook_disables_delivery_without_logging_url(
    monkeypatch, caplog, status
):
    notifier, _ = transport(monkeypatch)
    responses.post(URL, status=status)
    with requests.Session() as session, caplog.at_level(logging.WARNING):
        assert not notifier._deliver(session, {"content": "hello"})
    assert notifier._disabled.is_set()
    assert len(responses.calls) == 1
    assert "test-token" not in caplog.text


@responses.activate
def test_transient_errors_retry_without_logging_exception_secrets(monkeypatch, caplog):
    notifier, waits = transport(monkeypatch)
    responses.post(URL, body=requests.ConnectionError(URL))
    responses.post(URL, status=503)
    responses.post(URL, status=200)
    with requests.Session() as session, caplog.at_level(logging.WARNING):
        assert notifier._deliver(session, {"content": "hello"})
    assert waits == [0, 1, 2]
    assert "test-token" not in caplog.text


@responses.activate
@pytest.mark.parametrize("status", [400, 302])
def test_bad_request_or_redirect_is_not_retried(monkeypatch, status):
    notifier, _ = transport(monkeypatch)
    responses.post(URL, status=status, headers={"Location": "https://example.test"})
    with requests.Session() as session:
        assert not notifier._deliver(session, {"content": "hello"})
    assert len(responses.calls) == 1


def test_shutdown_cancels_rate_limit_wait(monkeypatch):
    entered = Event()

    def deliver(self, session, payload):
        entered.set()
        self._stop.wait(60)
        return False

    monkeypatch.setattr(DiscordNotifier, "_deliver", deliver)
    notifier = DiscordNotifier(URL)
    try:
        assert notifier.notify(Notification("live"))
        assert entered.wait(1)
        notifier.close(timeout=0)
        assert notifier._stop.is_set()
    finally:
        notifier.close(timeout=1)
    assert not notifier._thread.is_alive()


@responses.activate
def test_background_worker_sends_and_drains_on_close():
    responses.post(URL, status=200, json={"id": "message"})
    notifier = DiscordNotifier(URL)
    assert notifier.notify(Notification("disk_warning", detail="20 GiB free"))
    notifier.close()
    assert not notifier._thread.is_alive()
    assert len(responses.calls) == 1


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    notifier = Mock()
    notifier.notify.return_value = True
    monitor = LiveStreamMonitor(api_client=Mock(), notifier=notifier)
    monitor.monitored_creators = {
        "c": CreatorProfile(creator_oid="c", creator_name="Creator")
    }
    yield monitor
    monitor.shutdown()


def test_live_notifies_once_per_stream_not_per_poll_or_title_change(
    monitor, monkeypatch
):
    monkeypatch.setattr(monitor, "_process_live_stream", Mock())
    stream = SimpleNamespace(
        creator_oid="c",
        stream_state=StreamState.LIVE,
        title="First",
        stream_start_time=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )
    monitor._process_live_streams([stream])
    stream.title = "Changed"
    monitor._process_live_streams([stream])
    assert monitor.notifier.notify.call_count == 1
    stream.stream_start_time = datetime(2026, 10, 1, tzinfo=timezone.utc)
    monitor._process_live_streams([stream])
    events = [call.args[0] for call in monitor.notifier.notify.call_args_list]
    assert [event.kind for event in events] == ["live", "offline", "live"]
    assert events[1].title == "Changed"
    assert all(event.creator_oid == "c" for event in events)


def test_disk_notification_still_runs_when_upstream_fails(
    monitor, tmp_path, monkeypatch
):
    monkeypatch.setattr(monitor, "_update_downloaders", lambda: None)
    monkeypatch.setattr(
        monitor.disk_monitor,
        "check",
        lambda *_: DiskAlert("critical", 3 * 1024**3, tmp_path),
    )
    monitor.api_client.get_livestream_status.side_effect = RPlayAPIError("offline")
    monitor._run_poll_cycle()
    notification = monitor.notifier.notify.call_args.args[0]
    assert notification.kind == "disk_critical"
    assert notification.free_bytes == 3 * 1024**3
    assert notification.warning_bytes == 30 * 1024**3
    assert notification.critical_bytes == 10 * 1024**3
    assert notification.recovery_bytes == 32 * 1024**3
    assert not monitor.is_healthy


def test_disk_recovery_remains_internal_without_webhook(monitor, tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "_update_downloaders", lambda: None)
    monkeypatch.setattr(
        monitor.disk_monitor,
        "check",
        lambda *_: DiskAlert("recovered", 35 * 1024**3, tmp_path),
    )
    monitor.api_client.get_livestream_status.return_value = []
    monitor._run_poll_cycle()
    monitor.notifier.notify.assert_not_called()


def observed_stream():
    return SimpleNamespace(
        creator_oid="c",
        stream_state=StreamState.LIVE,
        title="Latest title",
        stream_start_time=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )


def test_offline_requires_two_missing_polls_and_notifies_once(monitor, monkeypatch):
    monkeypatch.setattr(monitor, "_process_live_stream", Mock())
    monitor._process_live_streams([observed_stream()])
    monitor.notifier.notify.reset_mock()
    monitor._process_live_streams([])
    monitor.notifier.notify.assert_not_called()
    monitor._process_live_streams([])
    notice = monitor.notifier.notify.call_args.args[0]
    assert notice.kind == "offline"
    assert notice.creator == "Creator" and notice.creator_oid == "c"
    assert notice.title == "Latest title"
    assert notice.started_at == "2026-09-30T00:00:00Z"
    monitor._process_live_streams([])
    monitor.notifier.notify.assert_called_once()


def test_api_errors_do_not_advance_offline_confirmation_and_reappearance_resets_it(
    monitor, monkeypatch
):
    monkeypatch.setattr(monitor, "_process_live_stream", Mock())
    monkeypatch.setattr(monitor, "_update_downloaders", lambda: None)
    monitor._process_live_streams([observed_stream()])
    monitor.notifier.notify.reset_mock()
    monitor._process_live_streams([])
    monitor.api_client.get_livestream_status.side_effect = RPlayAPIError("offline API")
    monitor._run_poll_cycle()
    assert monitor._observed_streams["c"].missing_polls == 1
    monitor._process_live_streams([observed_stream()])
    assert monitor._observed_streams["c"].missing_polls == 0
    monitor._process_live_streams([])
    assert not [
        call
        for call in monitor.notifier.notify.call_args_list
        if call.args[0].kind == "offline"
    ]


def test_removing_a_creator_is_not_a_stream_end(monitor, monkeypatch):
    monkeypatch.setattr(monitor, "_process_live_stream", Mock())
    monitor._process_live_streams([observed_stream()])
    monitor.notifier.notify.reset_mock()
    monitor.monitored_creators.clear()
    monitor._process_live_streams([])
    monitor._process_live_streams([])
    monitor.notifier.notify.assert_not_called()
    assert not monitor._observed_streams


def test_offline_can_be_enabled_without_live_notifications(monitor, monkeypatch):
    monkeypatch.setattr(monitor, "_process_live_stream", Mock())
    monitor.notifier.notify.side_effect = (
        lambda event, **kwargs: event.kind == "offline"
    )
    monitor._process_live_streams([observed_stream()])
    monitor._process_live_streams([])
    monitor._process_live_streams([])
    assert [call.args[0].kind for call in monitor.notifier.notify.call_args_list] == [
        "live",
        "offline",
    ]
    assert not monitor._observed_streams


def test_failed_offline_enqueue_retries_on_next_poll(monitor, monkeypatch):
    monkeypatch.setattr(monitor, "_process_live_stream", Mock())
    monitor._process_live_streams([observed_stream()])
    monitor.notifier.notify.reset_mock()
    monitor.notifier.notify.return_value = False
    monitor._process_live_streams([])
    monitor._process_live_streams([])
    assert "c" in monitor._observed_streams
    monitor.notifier.notify.return_value = True
    monitor._process_live_streams([])
    assert not monitor._observed_streams
    assert monitor.notifier.notify.call_count == 2


def test_blocked_merge_and_auth_events_have_safe_details(monitor, tmp_path):
    session = DownloadSession(
        session_key="s",
        creator_oid="c",
        creator_name="Creator",
        title="Title",
        stream_start_time=datetime(2026, 9, 30, tzinfo=timezone.utc),
        state=SessionState.RAW_RUNNING,
        output_dir=tmp_path,
        session_prefix="prefix",
    )
    monitor.sessions["s"] = session
    monitor._handle_raw_download_blocked(
        RawDownloadBlocked("s", "https://secret.invalid/key2=token")
    )
    monitor._handle_raw_download_blocked(RawDownloadBlocked("s", "duplicate"))
    monitor._handle_monitor_event(MergeFailed("s", "https://secret.invalid/key2=token"))
    monitor._log_auth_error("private diagnostic")
    monitor._log_auth_error("duplicate")
    events = [call.args[0] for call in monitor.notifier.notify.call_args_list]
    assert [event.kind for event in events] == [
        "blocked",
        "merge_failed",
        "auth_failed",
    ]
    assert all("secret.invalid" not in event.detail for event in events)


def test_merge_completion_notifies_once_after_success_with_filename_only(
    monitor, tmp_path
):
    session = DownloadSession(
        session_key="s",
        creator_oid="c",
        creator_name="Creator",
        title="Title",
        stream_start_time=datetime(2026, 9, 30, tzinfo=timezone.utc),
        state=SessionState.MERGING,
        output_dir=tmp_path,
        session_prefix="prefix",
    )
    monitor.sessions["s"] = session
    monitor._handle_monitor_event(MergeCompleted("s", tmp_path / "recording.mp4"))
    monitor._handle_monitor_event(MergeCompleted("s", tmp_path / "recording.mp4"))
    monitor.notifier.notify.assert_called_once()
    notice = monitor.notifier.notify.call_args.args[0]
    assert notice.kind == "merge_completed"
    assert notice.creator == "Creator" and notice.title == "Title"
    assert notice.output_file == "recording.mp4"
    assert session.state == SessionState.DONE


def test_repeated_download_failure_notifies_only_after_immediate_retry_budget(
    monitor, tmp_path, monkeypatch
):
    started = datetime(2026, 9, 30, tzinfo=timezone.utc)
    monitor._download_retry_stream_start["c"] = started
    monkeypatch.setattr(monitor, "_request_retry_poll", lambda *_: False)
    for attempt in range(monitor.DOWNLOAD_RETRY_IMMEDIATE_BUDGET + 1):
        key = str(attempt)
        monitor.sessions[key] = DownloadSession(
            session_key=key,
            creator_oid="c",
            creator_name="Creator",
            title="Title",
            stream_start_time=started,
            state=SessionState.RAW_RUNNING,
            output_dir=tmp_path,
            session_prefix="prefix",
        )
        monitor._handle_raw_download_failed(RawDownloadFailed(key, "network failure"))
    monitor.notifier.notify.assert_called_once()
    assert monitor.notifier.notify.call_args.args[0].kind == "download_failed"
