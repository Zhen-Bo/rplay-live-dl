"""Real HTTP and media pipeline with synthetic credentials and generated video."""

import base64
import json
import shutil
import subprocess
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from core.downloader import StreamDownloader
from core.live_stream_monitor import LiveStreamMonitor
from core.rplay import RPlayAPI
from models.download import MergeCompleted, MergeJobSpec


def test_recording_survives_refresh_boundary(tmp_path, monkeypatch):
    """Refresh during an active HLS download, merge it, and acquire another key."""
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required for the real media integration test")
    monkeypatch.chdir(tmp_path)
    segment = tmp_path / "fixture.ts"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:r=10",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-f",
            "mpegts",
            str(segment),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    clock = [1000]
    # Replace only the API module's clock, leaving HTTP and downloader timing real.
    monkeypatch.setattr(
        "core.rplay.time", SimpleNamespace(time=lambda: clock[0], sleep=time.sleep)
    )
    refreshes, key_tokens = [], []
    segment_requested, release_segment = Event(), Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def respond(self, body, content_type="application/json", status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            assert self.path == "/rplay/account/refresh-token"
            assert self.headers["refresh-token"] == "synthetic-refresh"
            assert self.headers["platform-type"] == "rplay"
            assert self.headers.get("Authorization") is None
            body = self.rfile.read(int(self.headers["Content-Length"]))
            assert json.loads(body) == {"requestorOid": "synthetic-user"}
            payload = (
                base64.urlsafe_b64encode(json.dumps({"exp": clock[0] + 600}).encode())
                .decode()
                .rstrip("=")
            )
            token = f"e30.{payload}.synthetic"
            refreshes.append(token)
            self.respond(json.dumps({"accessToken": token}).encode())

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/live/key2":
                assert parse_qs(parsed.query)["loginType"] == ["rplay"]
                token = self.headers["Authorization"]
                key_tokens.append(token)
                assert token == refreshes[-1]
                self.respond(b'{"authKey":"synthetic-key"}')
            elif parsed.path.endswith(".m3u8"):
                assert parse_qs(parsed.query)["key2"] == ["synthetic-key"]
                self.respond(
                    b"#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:0\n"
                    b"#EXTINF:1.0,\n/segment.ts\n#EXT-X-ENDLIST\n",
                    "application/vnd.apple.mpegurl",
                )
            elif parsed.path == "/segment.ts":
                segment_requested.set()
                assert release_segment.wait(20)
                self.respond(segment.read_bytes(), "video/mp2t")
            else:
                self.respond(b"", status=404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    api = RPlayAPI(
        base_url=f"http://127.0.0.1:{server.server_port}",
        user_oid="synthetic-user",
        auth_token="",
        refresh_token="synthetic-refresh",
    )
    monitor = LiveStreamMonitor(api_client=api)
    completed, failed = [], []
    output_dir = tmp_path / "archive" / "Fixture"
    downloader = StreamDownloader(
        "Fixture",
        session_key="fixture-session",
        output_dir=output_dir,
        output_extension=".ts",
        filename_prefix="20260911_120000_",
        on_download_complete=completed.append,
        on_download_failure=failed.append,
    )
    try:
        api.validate_credentials()
        key = monitor.api_client._get_stream_key()
        assert len(refreshes) == 1
        downloader.download(api.get_stream_url("fixture", key), "Generated video")
        assert segment_requested.wait(20)
        assert downloader.is_alive()
        clock[0] = 1601  # JWT expired while media download remains active.
        assert monitor.api_client._get_stream_key() == "synthetic-key"
        assert len(refreshes) == 2
        assert key_tokens == [refreshes[0], refreshes[0], refreshes[1]]
        release_segment.set()
        assert downloader.download_thread is not None
        downloader.download_thread.join(30)
        assert not downloader.is_alive()
        assert completed and not failed
        result = monitor._merge_session_to_mp4(
            MergeJobSpec(
                "fixture-session",
                "Fixture",
                "Generated video",
                datetime(2026, 9, 11),
                output_dir,
                "20260911_120000_",
            )
        )
        assert isinstance(result, MergeCompleted)
        assert result.output_path.stat().st_size > 0
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(result.output_path),
                "-f",
                "null",
                "-",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        monitor.api_client._get_stream_key()
        assert len(refreshes) == 2
    finally:
        release_segment.set()
        if downloader.download_thread:
            downloader.download_thread.join(30)
        monitor.shutdown()
        api.close()
        server.shutdown()
        server.server_close()
        server_thread.join(5)
