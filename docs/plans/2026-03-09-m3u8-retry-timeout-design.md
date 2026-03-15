# M3U8 Retry and Downloader Timeout Design

## Goal

Make freshly-started streams less likely to be marked inaccessible too early by retrying transient `404`/timeout failures before blocking a creator session, and reduce per-request downloader socket wait time to `10` seconds.

## Current Behavior

- `core/live_stream_monitor.py` blocks a session when `validate_m3u8_url()` returns `False`.
- `core/downloader.py` notifies the monitor immediately when a download error looks like an M3U8 access failure.
- `core/rplay.py` already retries validation attempts in a loop, but downloader-side media playlist failures are not retried at the monitor level before blocking.
- yt-dlp retries are enabled, but socket timeout is not explicitly configured in downloader options.

## Desired Behavior

- Treat early `404` failures as transient during the retry window instead of blocking immediately.
- Retry downloader-side transient M3U8 failures before calling the monitor callback that marks a stream blocked.
- Keep the final blocking behavior after retries are exhausted so actual paid/inaccessible streams are still suppressed for the current session.
- Set yt-dlp socket timeout to `10` seconds.

## Recommended Approach

### Option A — Explicit transient retry before block

Add a small downloader-level retry loop around `yt_dlp.YoutubeDL.download()` for retryable transient errors such as `404` and timeout-related failures. Only invoke the monitor's `on_download_error` callback after those retries are exhausted. Keep `validate_m3u8_url()` retry behavior explicit and covered by tests. Add `socket_timeout=10` to yt-dlp options.

**Why this option:**

- Matches the user's requested behavior with minimal architectural change.
- Preserves current session-blocking semantics for truly inaccessible streams.
- Works for both pre-download validation failures and downloader media-playlist failures.

### Option B — Remove downloader-side block, retry only on next poll

Do not block on downloader 404 at all; let the next monitor poll retry.

**Trade-off:** simpler, but recovery is limited by the poll interval and may wait much longer than necessary.

### Option C — Add configurable retry policy via environment/config

Expose retry attempts, retry delay, and socket timeout as user configuration.

**Trade-off:** more flexible, but larger change surface than needed for this fix.

## Chosen Design

Implement Option A.

### Code Changes

- `core/constants.py`
  - Add downloader socket timeout constant.
  - Add downloader transient retry attempt/delay constants.
- `core/downloader.py`
  - Add retryable download error classification for transient failures.
  - Retry transient download failures before invoking the block callback.
  - Add `socket_timeout` to yt-dlp options.
- `core/rplay.py`
  - Keep validation retries explicit and make retry intent around `404` clear in code/tests.
- `tests/test_downloader.py`
  - Add failing tests for transient retry behavior and `socket_timeout=10`.
- `tests/test_rplay.py`
  - Keep or extend tests proving `404` validation retries succeed when the URL becomes available on a later attempt.

## Risks

- Over-broad retry classification could delay real failures unnecessarily.
- Under-broad classification could still block too early.

## Validation

- Run focused tests for `tests/test_downloader.py` and `tests/test_rplay.py`.
- Confirm red/green cycle for the new downloader retry tests.
