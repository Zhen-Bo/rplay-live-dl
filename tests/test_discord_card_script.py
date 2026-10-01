"""Offline checks for the manual Discord card tester."""

import json

import pytest
import responses

from scripts import test_discord_cards as script

URL = "https://discord.com/api/webhooks/123456/test-token"


@pytest.mark.parametrize("args,count", [([], 8), (["--include-offline"], 9)])
@responses.activate
def test_sends_real_card_payloads(monkeypatch, capsys, args, count):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", URL)
    monkeypatch.setenv("DISCORD_WEBHOOK_EVENTS", "")
    responses.add(responses.POST, URL, json={"id": "123"}, status=200)
    assert script.main(args) == 0
    assert len(responses.calls) == count
    payloads = [json.loads(call.request.body) for call in responses.calls]
    assert len({p["embeds"][0]["color"] for p in payloads}) == count
    assert "Disk space recovered" not in json.dumps(payloads)
    completed = [
        p["embeds"][0] for p in payloads if p["embeds"][0]["title"] == "Merge complete"
    ]
    assert len(completed) == 1
    assert completed[0]["fields"][0]["value"].endswith(".mp4")
    for call in responses.calls:
        payload = json.loads(call.request.body)
        card = payload["embeds"][0]
        assert "TEST" not in card["title"]
        assert card["title"][0].isascii() or card["title"] == "🔑 Authentication failed"
        assert "url" not in card.get("author", {})
        assert payload["allowed_mentions"] == {"parse": []}
        assert "wait=true" in call.request.url
    live = json.loads(responses.calls[0].request.body)["embeds"][0]
    assert live["title"] == "Live now"
    assert live["description"].startswith("**🎬 Stream title**\n**Late-night chat")
    assert live["description"].endswith("**")
    assert live["thumbnail"]["url"] == live["author"]["icon_url"]
    assert "Late-night chat" in live["description"]
    auth_index = 3 if count == 9 else 2
    auth = json.loads(responses.calls[auth_index].request.body)["embeds"][0]
    assert auth["title"] == "🔑 Authentication failed"
    assert "author" not in auth
    assert "thumbnail" not in auth
    assert "Late-night chat" not in auth["description"]
    assert (
        "Confirmed deliveries: " + str(count) + "/" + str(count)
        in capsys.readouterr().out
    )


@responses.activate
def test_dry_run_does_not_read_credentials_or_send(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Dry-run must not read .env")

    monkeypatch.setattr(script, "dotenv_values", forbidden)
    assert script.main(["--dry-run"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 8
    assert not responses.calls


@responses.activate
def test_reads_project_dotenv_independently_of_cwd(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(f"DISCORD_WEBHOOK_URL={URL}\n", encoding="utf-8")
    monkeypatch.setattr(script, "ROOT", tmp_path)
    responses.add(responses.POST, URL, status=204)
    assert script.main([]) == 0
    assert len(responses.calls) == 8


@pytest.mark.parametrize("value", ["", "https://example.com/private-secret"])
@responses.activate
def test_environment_overrides_dotenv_and_rejects_invalid_url(
    monkeypatch, tmp_path, capsys, value
):
    (tmp_path / ".env").write_text(f"DISCORD_WEBHOOK_URL={URL}\n", encoding="utf-8")
    monkeypatch.setattr(script, "ROOT", tmp_path)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", value)
    assert script.main([]) == 2
    output = capsys.readouterr()
    assert "private-secret" not in output.err + output.out
    assert not responses.calls


@responses.activate
def test_failed_delivery_stops_without_claiming_success(monkeypatch, capsys, caplog):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", URL)
    responses.add(responses.POST, URL, status=404)
    assert script.main([]) == 1
    assert len(responses.calls) == 1
    output = capsys.readouterr()
    assert "Confirmed deliveries: 0/8" in output.out
    assert "FAILED: live" in output.err
    assert "test-token" not in output.out + output.err + caplog.text
