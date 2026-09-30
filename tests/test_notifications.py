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
    MergeFailed,
    RawDownloadBlocked,
    RawDownloadFailed,
    SessionState,
)
from models.env import EnvConfig
from models.notification import Notification
from models.rplay import StreamState

URL = "https://discord.com/api/webhooks/123456/test-token"


def test_plain_text_redacts_secrets_disables_mentions_and_bounds_emoji():
    event = Notification(
        "live",
        creator="@everyone",
        title="private-value key2=hidden https://example.test/?token=secret "
        + "😀" * 2000,
    )
    payload = format_discord_message(event, secrets=("private-value",))
    assert payload["allowed_mentions"] == {"parse": []}
    assert payload["flags"] == 4
    assert "private-value" not in payload["content"]
    assert "hidden" not in payload["content"]
    assert "https://" not in payload["content"]
    assert len(payload["content"].encode("utf-16-le")) <= 3800


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
    assert monitor.notifier.notify.call_count == 2


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
    assert monitor.notifier.notify.call_args.args[0].kind == "disk_critical"
    assert not monitor.is_healthy


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
