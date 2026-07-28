"""Tests for application startup helpers."""

import logging
from unittest.mock import MagicMock

import pytest

from core.rplay import RPlayAPIError, RPlayAuthError, RPlayConnectionError
from main import _validate_startup_credentials, _warn_about_orphaned_downloads, main
from models.env import EnvConfig


def _valid_env() -> EnvConfig:
    return EnvConfig(auth_token="test_token", user_oid="test_oid", interval=60)


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


def test_validate_startup_credentials_success(monkeypatch):
    """Test credential validation returns True on success."""
    logger = MagicMock(spec=logging.Logger)
    api = MagicMock()
    monkeypatch.setattr("main.RPlayAPI", MagicMock(return_value=api))

    assert _validate_startup_credentials(_valid_env(), logger) is True
    api.validate_credentials.assert_called_once_with()
    api.close.assert_called_once_with()


def test_validate_startup_credentials_auth_failure(monkeypatch):
    """Test credential validation returns False on auth failure."""
    logger = MagicMock(spec=logging.Logger)
    api = MagicMock()
    api.validate_credentials.side_effect = RPlayAuthError(
        "Authentication failed. Please check your AUTH_TOKEN."
    )
    monkeypatch.setattr("main.RPlayAPI", MagicMock(return_value=api))

    assert _validate_startup_credentials(_valid_env(), logger) is False
    assert any("Authentication failed" in str(c) for c in logger.error.call_args_list)
    api.close.assert_called_once_with()


def test_validate_startup_credentials_network_does_not_block(monkeypatch):
    """Test network errors allow startup (monitor owns retries)."""
    logger = MagicMock(spec=logging.Logger)
    api = MagicMock()
    api.validate_credentials.side_effect = RPlayConnectionError("Request timed out")
    monkeypatch.setattr("main.RPlayAPI", MagicMock(return_value=api))

    assert _validate_startup_credentials(_valid_env(), logger) is True
    logger.warning.assert_called()
    api.close.assert_called_once_with()


def test_validate_startup_credentials_api_error_does_not_block(monkeypatch):
    """Test non-auth API errors allow startup."""
    logger = MagicMock(spec=logging.Logger)
    api = MagicMock()
    api.validate_credentials.side_effect = RPlayAPIError("HTTP error: 500")
    monkeypatch.setattr("main.RPlayAPI", MagicMock(return_value=api))

    assert _validate_startup_credentials(_valid_env(), logger) is True
    logger.warning.assert_called()


def test_main_exits_nonzero_on_auth_failure(monkeypatch, capsys):
    """Test main exits before the scheduler when credentials are invalid."""
    monkeypatch.setenv("AUTH_TOKEN", "test-token")
    monkeypatch.setenv("USER_OID", "test-oid")
    monkeypatch.setattr("main.load_dotenv", lambda: None)
    monkeypatch.setattr(
        EnvConfig,
        "model_config",
        {**EnvConfig.model_config, "env_file": None},
    )
    monkeypatch.setattr("main.configure_logging", MagicMock())
    monkeypatch.setattr(
        "main.setup_logger", MagicMock(return_value=MagicMock(spec=logging.Logger))
    )
    monkeypatch.setattr("main.cleanup_old_logs", MagicMock(return_value=0))
    monkeypatch.setattr("main._warn_about_orphaned_downloads", MagicMock())
    monkeypatch.setattr(
        "main._validate_startup_credentials",
        MagicMock(return_value=False),
    )
    run_scheduler_mock = MagicMock()
    monkeypatch.setattr("main.run_scheduler", run_scheduler_mock)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    run_scheduler_mock.assert_not_called()


def test_main_starts_scheduler_when_credentials_ok(monkeypatch):
    """Test main reaches the scheduler after successful credential validation."""
    monkeypatch.setenv("AUTH_TOKEN", "test-token")
    monkeypatch.setenv("USER_OID", "test-oid")
    monkeypatch.setattr("main.load_dotenv", lambda: None)
    monkeypatch.setattr(
        EnvConfig,
        "model_config",
        {**EnvConfig.model_config, "env_file": None},
    )
    monkeypatch.setattr("main.configure_logging", MagicMock())
    monkeypatch.setattr(
        "main.setup_logger", MagicMock(return_value=MagicMock(spec=logging.Logger))
    )
    monkeypatch.setattr("main.cleanup_old_logs", MagicMock(return_value=0))
    monkeypatch.setattr("main._warn_about_orphaned_downloads", MagicMock())
    monkeypatch.setattr(
        "main._validate_startup_credentials",
        MagicMock(return_value=True),
    )
    run_scheduler_mock = MagicMock()
    monkeypatch.setattr("main.run_scheduler", run_scheduler_mock)

    main()

    run_scheduler_mock.assert_called_once()
