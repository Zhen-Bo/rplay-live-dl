"""Tests for application startup helpers."""

import logging
from unittest.mock import MagicMock

import pytest

from core.rplay import RPlayAPIError, RPlayAuthError, RPlayConnectionError
from main import _warn_about_orphaned_downloads, main
from models.config import AppConfig
from models.env import EnvConfig


def _patch_main_startup(monkeypatch, *, api_side_effect=None, calls=None):
    """Patch main() deps; optionally record call order in ``calls``."""
    monkeypatch.setenv("AUTH_TOKEN", "test-token")
    monkeypatch.setenv("USER_OID", "test-oid")
    monkeypatch.setattr("main.load_dotenv", lambda: None)
    monkeypatch.setattr(
        EnvConfig,
        "model_config",
        {**EnvConfig.model_config, "env_file": None},
    )

    def _track(name, fn):
        def wrapper(*args, **kwargs):
            if calls is not None:
                calls.append(name)
            return fn(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(
        "main.configure_logging",
        _track("configure_logging", MagicMock()),
    )
    monkeypatch.setattr(
        "main.setup_logger",
        _track("setup_logger", MagicMock(return_value=MagicMock(spec=logging.Logger))),
    )
    monkeypatch.setattr("main.cleanup_old_logs", MagicMock(return_value=0))
    monkeypatch.setattr("main._warn_about_orphaned_downloads", MagicMock())
    monkeypatch.setattr(
        "main.read_app_config",
        MagicMock(
            return_value=AppConfig(
                api_base_url="https://api.example.com",
                creators=[],
            )
        ),
    )

    api = MagicMock()
    if api_side_effect is not None:
        api.validate_credentials.side_effect = api_side_effect

    def _make_api(*args, **kwargs):
        if calls is not None:
            calls.append("validate_credentials")
        return api

    # Track when the validation client is constructed (stands in for the probe).
    monkeypatch.setattr("main.RPlayAPI", _make_api)

    run_scheduler_mock = MagicMock()
    if calls is not None:

        def _run_scheduler(*args, **kwargs):
            calls.append("run_scheduler")
            return run_scheduler_mock(*args, **kwargs)

        monkeypatch.setattr("main.run_scheduler", _run_scheduler)
    else:
        monkeypatch.setattr("main.run_scheduler", run_scheduler_mock)

    # load_env runs before configure_logging; record it via a thin wrapper.
    real_load_env = __import__("main", fromlist=["load_env"]).load_env

    def _load_env():
        if calls is not None:
            calls.append("load_env")
        return real_load_env()

    monkeypatch.setattr("main.load_env", _load_env)
    return api, run_scheduler_mock


def test_warns_about_each_kind_of_leftover(tmp_path, monkeypatch, caplog):
    """Test startup reports each supported orphaned download file kind."""
    monkeypatch.chdir(tmp_path)
    archive_dir = tmp_path / "archive" / "Creator"
    archive_dir.mkdir(parents=True)
    for filename in (
        "20260727_020000_#Creator x.ts",
        "video.ts.part",
        "video.ts.ytdl",
        "video.ts.part-Frag3",
    ):
        (archive_dir / filename).write_bytes(b"leftover")

    caplog.set_level(logging.WARNING)
    _warn_about_orphaned_downloads(logging.getLogger("test_main"))

    assert any("4 file(s)" in record.getMessage() for record in caplog.records)


def test_silent_when_archive_is_clean(tmp_path, monkeypatch, caplog):
    """Test startup does not warn when only completed files remain."""
    monkeypatch.chdir(tmp_path)
    archive_dir = tmp_path / "archive" / "Creator"
    archive_dir.mkdir(parents=True)
    (archive_dir / "final.mp4").write_bytes(b"complete")

    caplog.set_level(logging.WARNING)
    _warn_about_orphaned_downloads(logging.getLogger("test_main"))

    assert not any(record.levelno >= logging.WARNING for record in caplog.records)


def test_silent_when_archive_missing(tmp_path, monkeypatch, caplog):
    """Test startup ignores a missing archive directory."""
    monkeypatch.chdir(tmp_path)

    _warn_about_orphaned_downloads(logging.getLogger("test_main"))

    assert not caplog.records


def test_main_invalid_log_level_exits_before_setup_logger(monkeypatch, capsys):
    """Regression: invalid LOG_LEVEL fails before setup_logger is called."""
    secret_token = "secret-auth-token-xyz"
    secret_oid = "secret-user-oid-abc"

    monkeypatch.setenv("AUTH_TOKEN", secret_token)
    monkeypatch.setenv("USER_OID", secret_oid)
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    monkeypatch.setattr("main.load_dotenv", lambda: None)
    # Avoid reading a real .env via pydantic-settings during this startup path.
    monkeypatch.setattr(
        EnvConfig,
        "model_config",
        {**EnvConfig.model_config, "env_file": None},
    )

    setup_logger_mock = MagicMock()
    configure_logging_mock = MagicMock()
    run_scheduler_mock = MagicMock()
    monkeypatch.setattr("main.setup_logger", setup_logger_mock)
    monkeypatch.setattr("main.configure_logging", configure_logging_mock)
    monkeypatch.setattr("main.run_scheduler", run_scheduler_mock)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    setup_logger_mock.assert_not_called()
    configure_logging_mock.assert_not_called()
    run_scheduler_mock.assert_not_called()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "LOG_LEVEL" in captured.err
    assert secret_token not in captured.err
    assert secret_oid not in captured.err
    assert secret_token not in captured.out
    assert secret_oid not in captured.out


def test_main_success_order(monkeypatch):
    """Test main runs load_env → logging → credential validation → scheduler."""
    calls: list[str] = []
    api, _ = _patch_main_startup(monkeypatch, calls=calls)

    main()

    assert calls == [
        "load_env",
        "configure_logging",
        "setup_logger",
        "validate_credentials",
        "run_scheduler",
    ]
    api.validate_credentials.assert_called_once_with()
    api.close.assert_called_once_with()


def test_main_auth_failure_exits_once_and_never_starts_scheduler(monkeypatch):
    """Test auth failure exits 1 with exactly one ERROR; scheduler never starts."""
    logger = MagicMock(spec=logging.Logger)
    api, run_scheduler_mock = _patch_main_startup(
        monkeypatch,
        api_side_effect=RPlayAuthError(
            "Authentication failed. Please check your AUTH_TOKEN."
        ),
    )
    monkeypatch.setattr("main.setup_logger", MagicMock(return_value=logger))

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    run_scheduler_mock.assert_not_called()
    assert logger.error.call_count == 1
    error_msg = str(logger.error.call_args.args[0])
    assert "Authentication failed" in error_msg
    assert "AUTH_TOKEN" in error_msg
    api.close.assert_called_once_with()


@pytest.mark.parametrize(
    "side_effect",
    [
        RPlayConnectionError("Request timed out"),
        RPlayAPIError("HTTP error: 500"),
    ],
)
def test_main_transient_api_error_continues_to_scheduler(monkeypatch, side_effect):
    """Test network/API probe failures warn and still start the scheduler."""
    logger = MagicMock(spec=logging.Logger)
    api, run_scheduler_mock = _patch_main_startup(
        monkeypatch,
        api_side_effect=side_effect,
    )
    monkeypatch.setattr("main.setup_logger", MagicMock(return_value=logger))

    main()

    run_scheduler_mock.assert_called_once()
    logger.warning.assert_called()
    logger.error.assert_not_called()
    api.close.assert_called_once_with()
