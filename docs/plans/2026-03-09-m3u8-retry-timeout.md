# M3U8 Retry and Downloader Timeout Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Retry transient stream startup failures such as early `404` and timeout conditions before blocking a creator session, and set downloader socket timeout to `10` seconds.

**Architecture:** Keep the monitor/session model intact. Make downloader-side transient failures retry inside `StreamDownloader` before notifying the monitor callback that marks a session blocked. Keep playlist validation retries explicit and covered by tests.

**Tech Stack:** Python 3.11, `pytest`, `responses`, `yt-dlp`, `requests`.

---

### Task 1: Add failing downloader tests

**Files:**
- Modify: `tests/test_downloader.py`
- Reference: `core/downloader.py`

**Step 1: Write the failing tests**

- Add a test proving transient `404` download errors are retried and can succeed on a later attempt.
- Add a test proving callback is invoked only after transient retries are exhausted.
- Add a test proving yt-dlp options include `socket_timeout=10`.

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_downloader.py -k "socket_timeout or retry" -v`

Expected: failures showing downloader does not yet retry transient errors and does not set socket timeout.

**Step 3: Commit checkpoint**

- Do not commit yet if tests are red.

### Task 2: Implement downloader retry and timeout

**Files:**
- Modify: `core/constants.py`
- Modify: `core/downloader.py`
- Test: `tests/test_downloader.py`

**Step 1: Add minimal constants**

- Add constants for downloader socket timeout and downloader transient retry policy.

**Step 2: Add minimal implementation**

- Add `socket_timeout` to yt-dlp options.
- Add retryable error classification for transient `404`/timeout failures.
- Wrap `yt_dlp` download execution in a small retry loop.
- Only notify the monitor callback after transient retries are exhausted.

**Step 3: Run test to verify it passes**

Run: `poetry run pytest tests/test_downloader.py -k "socket_timeout or retry" -v`

Expected: PASS.

**Step 4: Commit**

```bash
git add core/constants.py core/downloader.py tests/test_downloader.py
git commit -m "feat: retry transient download failures before blocking"
```

### Task 3: Verify validation retry behavior remains correct

**Files:**
- Modify: `tests/test_rplay.py` (only if clearer coverage is needed)
- Reference: `core/rplay.py`

**Step 1: Confirm existing tests cover 404 retry recovery**

- Reuse or extend the existing `validate_m3u8_url()` tests so `404 -> 404 -> 200` remains covered.

**Step 2: Run focused tests**

Run: `poetry run pytest tests/test_rplay.py -k "validate_m3u8_url or retry" -v`

Expected: PASS.

**Step 3: Commit**

```bash
git add tests/test_rplay.py core/rplay.py
git commit -m "test: cover m3u8 validation retry behavior"
```

### Task 4: Run final verification

**Files:**
- Reference: `tests/test_downloader.py`
- Reference: `tests/test_rplay.py`

**Step 1: Run the focused suite**

Run: `poetry run pytest tests/test_downloader.py tests/test_rplay.py -v`

Expected: PASS with `0` failures.

**Step 2: Review git diff and commit history**

Run:

```bash
git status --short
git log --oneline -3
```

**Step 3: Push**

Run: `git push origin HEAD`
