# Authentication

`REFRESH_TOKEN` and `USER_OID` are both required. Startup fails when `REFRESH_TOKEN` is empty after trimming.

`AUTH_TOKEN` was removed in v2.5.0. By 2026-09-29 RPlay had stopped issuing an `authKey` for the old static JWT (key2 returned only `region`, as for a guest), and the website no longer stores that credential. When only `AUTH_TOKEN` is set, startup fails with a message that points to the migration steps.

## Refresh protocol

The client sends `POST {apiBaseUrl}/rplay/account/refresh-token` with JSON `{"requestorOid":"<USER_OID>"}` and headers `refresh-token: <REFRESH_TOKEN>`, `platform-type: rplay`, and `Content-Type: application/json`.

The response must contain a non-empty `accessToken` with a readable JWT payload and a finite numeric `exp` in the future. Expiration parsing schedules renewal; it does not verify the JWT signature. The server validates the credential on key2. A malformed response leaves any previous in-memory token untouched and prevents the pending key2 request.

Key2 requests use `loginType=rplay`. `main` creates and closes the API client; startup validation, the scheduler, and the monitor share it. Before every key2 attempt, an expired JWT or one with fewer than `TOKEN_REFRESH_LEEWAY_SECONDS` remaining is renewed. The default is 300 seconds; exactly 300 seconds remaining does not trigger renewal. Zero renews only at expiration. Public status polling and an ongoing media download do not themselves trigger refresh.

HTTP 401/403 from refresh raises an authentication error without retry. Timeouts, connection failures, and HTTP 429/500/502/503/504 receive at most three attempts with existing exponential backoff. Other HTTP failures and malformed responses raise API errors. Credential-bearing response bodies and transport exception messages are excluded from authentication logs and errors.

JWTs remain in memory. No credentials are persisted by the refresh flow.

## Website behavior

Observed on 2026-09-30 with a browser capture of a signed-in session:

- The website keeps the refresh token in IndexedDB (`rplay-account-session`, store `records`, record `session`, field `refreshToken`). The access JWT sits next to it in `token`. The old `localStorage` credential is gone.
- The access JWT lives 600 seconds and carries `email`, `accountType`, `appType`, `iat`, and `exp`. It has no user ID, so the refresh token is what ties a session to an account.
- The refresh endpoint rejects a wrong or missing `requestorOid` with 401.
- Across the capture the website sent one refresh token value on every API call and received no replacement. Refresh responses contained only `accessToken`.

Refresh-token lifetime, revocation, and rotation remain unverified. Replacement refresh tokens are not persisted or adopted by this implementation. If the service changes to require rotation, the flow will need an update; users can recover by copying current browser credentials and restarting.

## Verification

- Focused tests cover configuration precedence, missing credentials, startup/client reuse, expiration boundaries, response validation, bounded retries, and redaction.
- `tests/test_token_refresh_integration.py` uses a local HTTP server, synthetic credentials, and an FFmpeg-generated video. It validates startup, holds an actual HLS download open, advances the API clock beyond JWT expiration, obtains a new key with a refreshed JWT, completes the download, merges to MP4, and decodes the result with FFmpeg. It requires FFmpeg and skips when unavailable.
- Browser/API investigation confirmed new JWT key2 success with `rplay`, refresh without an access JWT, and recovery after access-JWT expiration. This is separate from the deterministic local recording test; no long-running production recording or refresh-token rotation test is claimed.
