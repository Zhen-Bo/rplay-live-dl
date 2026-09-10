# Authentication

`REFRESH_TOKEN` selects the flow by its non-empty, trimmed value. With it, the configured `AUTH_TOKEN` is ignored. Without it, `AUTH_TOKEN` is required and remains a static credential: no JWT decoding, expiration checks, or refresh calls. Both flows require `USER_OID`.

## Refresh protocol

The client sends `POST {apiBaseUrl}/rplay/account/refresh-token` with JSON `{"requestorOid":"<USER_OID>"}` and headers `refresh-token: <REFRESH_TOKEN>`, `platform-type: rplay`, and `Content-Type: application/json`.

The response must contain a non-empty `accessToken` with a readable JWT payload and a finite numeric `exp` in the future. Expiration parsing schedules renewal; it does not verify the JWT signature. The server validates the credential on key2. A malformed response leaves any previous in-memory token untouched and prevents the pending key2 request.

Refresh-flow key2 requests use `loginType=rplay`; static-flow requests retain `loginType=plax`. `main` creates and closes the API client; startup validation, the scheduler, and the monitor share it. Before every key2 attempt, an expired JWT or one with fewer than `TOKEN_REFRESH_LEEWAY_SECONDS` remaining is renewed. The default is 300 seconds; exactly 300 seconds remaining does not trigger renewal. Zero renews only at expiration. Public status polling and an ongoing media download do not themselves trigger refresh.

HTTP 401/403 from refresh raises an authentication error without retry. Timeouts, connection failures, and HTTP 429/500/502/503/504 receive at most three attempts with existing exponential backoff. Other HTTP failures and malformed responses raise API errors. Credential-bearing response bodies and transport exception messages are excluded from authentication logs and errors.

JWTs remain in memory. No credentials are persisted by the refresh flow. Observed website responses contained only `accessToken`; refresh-token lifetime, revocation, and rotation remain unverified. Replacement refresh tokens are not persisted or adopted by this implementation. If the service changes to require rotation, the flow will need an update; users can recover by copying current browser credentials and restarting.

## Verification

- Focused tests cover configuration precedence, missing credentials, startup/client reuse, static-flow compatibility, expiration boundaries, response validation, bounded retries, and redaction.
- `tests/test_token_refresh_integration.py` uses a local HTTP server, synthetic credentials, and an FFmpeg-generated video. It starts without `AUTH_TOKEN`, validates startup, holds an actual HLS download open, advances the API clock beyond JWT expiration, obtains a new key with a refreshed JWT, completes the download, merges to MP4, and decodes the result with FFmpeg. It requires FFmpeg and skips when unavailable.
- Browser/API investigation confirmed new JWT key2 success with `rplay`, refresh without an access JWT, and recovery after access-JWT expiration. This is separate from the deterministic local recording test; no long-running production recording or refresh-token rotation test is claimed.
