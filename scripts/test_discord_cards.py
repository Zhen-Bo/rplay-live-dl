"""Send simulated cards using the application's actual Discord formatter."""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import requests
from dotenv import dotenv_values
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.notifications import DiscordNotifier, format_discord_message
from models.env import EnvConfig
from models.notification import Notification

KINDS = (
    "live",
    "blocked",
    "auth_failed",
    "download_failed",
    "merge_failed",
    "merge_completed",
    "disk_warning",
    "disk_critical",
)


def build_cards(include_offline=False):
    kinds = list(KINDS)
    if include_offline:
        kinds.insert(1, "offline")
    started_at = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    cards = []
    for kind in kinds:
        values = {}
        if kind.startswith("disk_"):
            free_gib = {
                "disk_warning": 24.68,
                "disk_critical": 7.42,
            }
            values.update(
                free_bytes=int(free_gib[kind] * 1024**3),
                warning_bytes=30 * 1024**3,
                critical_bytes=10 * 1024**3,
                recovery_bytes=32 * 1024**3,
            )
        elif kind != "auth_failed":
            values.update(
                creator="ranaelchan",
                creator_oid="6a80fe356d0004cc8f262b41",
                title="Late-night chat | Music and catching up",
            )
            if kind == "live":
                values["started_at"] = started_at
            if kind == "merge_completed":
                values["output_file"] = "ranaelchan_2026-09-30_210000.mp4"
        payload = format_discord_message(Notification(kind=kind, **values))
        cards.append((kind, payload))
    return cards


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Send 8 simulated Discord cards to the configured webhook channel."
    )
    parser.add_argument(
        "--include-offline",
        action="store_true",
        help="Also send the stream-ended card (9 total).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print payloads without reading credentials or sending HTTP requests.",
    )
    args = parser.parse_args(argv)
    cards = build_cards(args.include_offline)
    if args.dry_run:
        print(
            json.dumps([payload for _, payload in cards], indent=2, ensure_ascii=True)
        )
        return 0

    # Environment wins, including an explicitly empty value. Never print the URL.
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if url is None:
        url = dotenv_values(ROOT / ".env", interpolate=False).get("DISCORD_WEBHOOK_URL")
    try:
        url = EnvConfig.validate_discord_url(SecretStr(url or "")).get_secret_value()
    except ValueError:
        print(
            "Invalid DISCORD_WEBHOOK_URL: use an HTTPS Discord incoming webhook URL without query parameters.",
            file=sys.stderr,
        )
        return 2
    if not url:
        print(
            "Set DISCORD_WEBHOOK_URL in the project .env or your environment first.",
            file=sys.stderr,
        )
        return 2

    # No worker: this developer harness calls the existing delivery routine
    # synchronously to reuse rate limits/retries and report actual HTTP outcomes.
    sender = DiscordNotifier(url, events=())
    sent = 0
    print(
        f"Sending {len(cards)} simulated cards. Check the webhook's Discord channel.",
        flush=True,
    )
    try:
        with requests.Session() as session:
            for index, (kind, payload) in enumerate(cards, 1):
                if not sender._deliver(session, payload):
                    print(
                        f"[{index}/{len(cards)}] FAILED: {kind}. Stopping; later cards were not attempted.",
                        file=sys.stderr,
                    )
                    return 1
                sent += 1
                print(f"[{index}/{len(cards)}] Sent: {kind}", flush=True)
    except KeyboardInterrupt:
        print(
            "Interrupted. An in-flight card may already have arrived.", file=sys.stderr
        )
        return 130
    except Exception:
        # Transport exceptions can contain the webhook token. Do not echo them.
        print(
            "Unexpected delivery failure. Check Discord before rerunning; a card may already have arrived.",
            file=sys.stderr,
        )
        return 1
    finally:
        sender.close()
        print(f"Confirmed deliveries: {sent}/{len(cards)}.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
