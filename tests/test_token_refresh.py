"""Behavioral coverage for the two RPlay authentication flows."""

import base64
import json
from unittest.mock import Mock, patch

import pytest
import requests

from core.rplay import RPlayAPI, RPlayAPIError, RPlayAuthError, RPlayConnectionError


def access_token(exp: object = 1600) -> str:
    """Build a deterministic, unsigned test JWT; never read real credentials."""
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    )
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.test-signature"


def response(data, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data).encode()
    return result


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("core.rplay.time.time", lambda: now[0])
    monkeypatch.setattr("core.rplay.time.sleep", lambda _: None)
    return now


@pytest.fixture
def api(clock):
    with RPlayAPI(
        base_url="https://api.rplay.live",
        user_oid="test-oid",
        refresh_token="test-refresh",
    ) as client:
        with (
            patch.object(
                client._session,
                "post",
                return_value=response({"accessToken": access_token()}),
            ),
            patch.object(
                client._session, "get", return_value=response({"authKey": "test-key"})
            ),
        ):
            yield client


@pytest.mark.parametrize("configured_token", ["", "not-a-jwt", access_token(1)])
def test_refresh_precedes_key2_and_ignores_configured_auth_token(
    clock, configured_token
):
    with RPlayAPI(
        base_url="https://api.example.test/",
        user_oid="test-oid",
        auth_token=configured_token,
        refresh_token="test-refresh",
    ) as api:
        calls = Mock()
        with (
            patch.object(
                api._session,
                "post",
                return_value=response({"accessToken": access_token()}),
            ) as post,
            patch.object(
                api._session, "get", return_value=response({"authKey": "test-key"})
            ) as get,
        ):
            calls.attach_mock(post, "post")
            calls.attach_mock(get, "get")
            api.validate_credentials()
            assert [call[0] for call in calls.mock_calls] == ["post", "get"]
            assert post.call_args.args == (
                "https://api.example.test/rplay/account/refresh-token",
            )
            assert post.call_args.kwargs["json"] == {"requestorOid": "test-oid"}
            assert post.call_args.kwargs["headers"]["refresh-token"] == "test-refresh"
            assert "Authorization" not in post.call_args.kwargs["headers"]
            assert get.call_args.args[0].endswith("loginType=rplay")
            assert get.call_args.kwargs["headers"]["Authorization"] == access_token()
            assert "refresh-token" not in get.call_args.kwargs["headers"]


def test_static_flow_does_not_decode_or_refresh(clock):
    # Static mode deliberately accepts the same opaque string as before.
    with RPlayAPI(
        base_url="https://api.rplay.live",
        user_oid="test-oid",
        auth_token="static-token",
        refresh_token="  ",
    ) as api:
        with (
            patch.object(
                api,
                "_access_token_expiry",
                side_effect=AssertionError("must not decode"),
            ),
            patch.object(api._session, "post") as post,
            patch.object(
                api._session, "get", return_value=response({"authKey": "key"})
            ) as get,
        ):
            api.validate_credentials()
            assert get.call_args.args[0].endswith("loginType=plax")
            assert get.call_args.kwargs["headers"]["Authorization"] == "static-token"
            post.assert_not_called()


@pytest.mark.parametrize(
    "now,refreshes", [(1200, 1), (1300, 1), (1301, 2), (1600, 2), (1700, 2)]
)
def test_refresh_boundary_and_expired_jwt(api, clock, now, refreshes):
    api.validate_credentials()
    clock[0] = now
    api._session.post.return_value = response({"accessToken": access_token(now + 600)})
    api._get_stream_key()
    assert api._session.post.call_count == refreshes
    expected = access_token() if refreshes == 1 else access_token(now + 600)
    assert api._session.get.call_args.kwargs["headers"]["Authorization"] == expected


def test_custom_leeway_and_zero_expiry_boundary(api, clock):
    api.token_refresh_leeway_seconds = 0
    api.validate_credentials()
    clock[0] = 1599
    api._get_stream_key()
    assert api._session.post.call_count == 1
    clock[0] = 1600
    api._session.post.return_value = response({"accessToken": access_token(2200)})
    api._get_stream_key()
    assert api._session.post.call_count == 2


def test_public_status_does_not_acquire_or_renew_jwt(api):
    api._session.get.return_value = response([])
    assert api.get_livestream_status() == []
    api._session.post.assert_not_called()


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_refresh_is_not_retried_or_used_for_key2(api, status):
    api._session.post.return_value = response({}, status)
    with pytest.raises(RPlayAuthError, match="REFRESH_TOKEN.*USER_OID"):
        api.validate_credentials()
    api._session.post.assert_called_once()
    api._session.get.assert_not_called()


@pytest.mark.parametrize("status", [400, 404])
def test_nontransient_refresh_error_is_not_retried(api, status):
    api._session.post.return_value = response({}, status)
    with pytest.raises(RPlayAPIError, match=f"HTTP {status}"):
        api.validate_credentials()
    api._session.post.assert_called_once()
    api._session.get.assert_not_called()


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_refresh_status_is_retried(api, status):
    api._session.post.side_effect = [
        response({}, status),
        response({"accessToken": access_token()}),
    ]
    api.validate_credentials()
    assert api._session.post.call_count == 2
    api._session.get.assert_called_once()


def test_refresh_retry_exhaustion_preserves_the_previous_token(api, clock):
    api.validate_credentials()
    clock[0] = 1700
    api._session.get.reset_mock()
    api._session.post.reset_mock()
    api._session.post.return_value = response({}, 503)
    with pytest.raises(RPlayAPIError, match="HTTP 503"):
        api._get_stream_key()
    assert api._session.post.call_count == 3
    api._session.get.assert_not_called()
    assert api.auth_token == access_token()


@pytest.mark.parametrize(
    "bad_token",
    [
        "not-a-jwt",
        "a..b",
        access_token(None),
        access_token(True),
        access_token("1600"),
        access_token(float("nan")),
        access_token(float("inf")),
        access_token(1000),
        access_token(999),
        "a.eyJpYXQiOjEwMDB9.b",  # no exp
        "a.W10.b",  # array payload
    ],
)
def test_invalid_refreshed_jwt_never_reaches_key2(api, bad_token):
    api._session.post.return_value = response({"accessToken": bad_token})
    with pytest.raises(RPlayAPIError, match="invalid or expired access token"):
        api.validate_credentials()
    api._session.get.assert_not_called()
    assert api.auth_token == ""


@pytest.mark.parametrize(
    "data", [{}, [], None, {"accessToken": " "}, {"accessToken": 1}]
)
def test_invalid_refresh_response_never_reaches_key2(api, data):
    api._session.post.return_value = response(data)
    with pytest.raises(RPlayAPIError, match="Invalid token refresh response"):
        api.validate_credentials()
    api._session.get.assert_not_called()


@pytest.mark.parametrize("operation", ["post", "get"])
@pytest.mark.parametrize(
    "exception_type",
    [requests.Timeout, requests.ConnectionError, requests.HTTPError, RuntimeError],
)
def test_authenticated_errors_and_retries_do_not_log_secrets(
    api, caplog, operation, exception_type
):
    secrets = ["sensitive-access", "sensitive-refresh", "sensitive-key"]
    getattr(api._session, operation).side_effect = exception_type(" ".join(secrets))
    with pytest.raises(RPlayAPIError) as caught:
        api.validate_credentials()
    assert all(secret not in f"{caplog.text}{caught.value}" for secret in secrets)
    if exception_type in (requests.Timeout, requests.ConnectionError):
        assert isinstance(caught.value, RPlayConnectionError)
        assert getattr(api._session, operation).call_count == 3


def test_token_expiry_is_rechecked_before_a_key2_retry(api, clock):
    def first_key_request(*args, **kwargs):
        clock[0] = 1700
        api._session.get.side_effect = None
        api._session.post.return_value = response({"accessToken": access_token(2300)})
        raise requests.Timeout()

    api._session.get.side_effect = first_key_request
    api.validate_credentials()
    assert api._session.post.call_count == 2
    assert api._session.get.call_args.kwargs["headers"][
        "Authorization"
    ] == access_token(2300)
