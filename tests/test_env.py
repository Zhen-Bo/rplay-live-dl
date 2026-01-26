"""Tests for environment configuration module."""

import pytest

from core.env import EnvConfigError, load_env
from models.env import EnvConfig


@pytest.fixture
def no_dotenv_file(monkeypatch):
    """Prevent pydantic-settings from reading .env file.

    This fixture patches EnvConfig.model_config to disable .env file reading,
    ensuring tests only use environment variables set via monkeypatch.
    """
    original_config = EnvConfig.model_config.copy()
    patched_config = original_config.copy()
    patched_config["env_file"] = None
    monkeypatch.setattr(EnvConfig, "model_config", patched_config)
    return monkeypatch


class TestLoadEnv:
    """Tests for load_env function."""

    def test_load_env_success(self, monkeypatch):
        """Test successfully loading environment variables."""
        monkeypatch.setenv("AUTH_TOKEN", "test_token_123")
        monkeypatch.setenv("USER_OID", "test_user_456")
        monkeypatch.setenv("INTERVAL", "120")

        config = load_env()

        assert config.auth_token == "test_token_123"
        assert config.user_oid == "test_user_456"
        assert config.interval == 120

    def test_load_env_default_interval(self, monkeypatch):
        """Test that interval has default value of 60."""
        monkeypatch.setenv("AUTH_TOKEN", "test_token")
        monkeypatch.setenv("USER_OID", "test_oid")
        # Don't set INTERVAL to test default

        config = load_env()

        assert config.interval == 60

    def test_load_env_missing_auth_token(self, no_dotenv_file):
        """Test that missing AUTH_TOKEN raises EnvConfigError."""
        # Clear any existing env vars
        no_dotenv_file.delenv("AUTH_TOKEN", raising=False)
        no_dotenv_file.setenv("USER_OID", "test_oid")

        with pytest.raises(EnvConfigError) as exc_info:
            load_env()

        assert "AUTH_TOKEN" in str(exc_info.value)
        assert "Missing required" in str(exc_info.value)

    def test_load_env_missing_user_oid(self, no_dotenv_file):
        """Test that missing USER_OID raises EnvConfigError."""
        no_dotenv_file.setenv("AUTH_TOKEN", "test_token")
        no_dotenv_file.delenv("USER_OID", raising=False)

        with pytest.raises(EnvConfigError) as exc_info:
            load_env()

        assert "USER_OID" in str(exc_info.value)
        assert "Missing required" in str(exc_info.value)

    def test_load_env_missing_both_required(self, no_dotenv_file):
        """Test that missing both required vars raises EnvConfigError."""
        no_dotenv_file.delenv("AUTH_TOKEN", raising=False)
        no_dotenv_file.delenv("USER_OID", raising=False)

        with pytest.raises(EnvConfigError) as exc_info:
            load_env()

        error_msg = str(exc_info.value)
        assert "AUTH_TOKEN" in error_msg
        assert "USER_OID" in error_msg

    def test_load_env_invalid_interval_too_low(self, monkeypatch):
        """Test that interval below minimum (10) raises ValueError."""
        monkeypatch.setenv("AUTH_TOKEN", "test_token")
        monkeypatch.setenv("USER_OID", "test_oid")
        monkeypatch.setenv("INTERVAL", "5")

        with pytest.raises(ValueError) as exc_info:
            load_env()

        assert "Invalid environment configuration" in str(exc_info.value)

    def test_load_env_invalid_interval_too_high(self, monkeypatch):
        """Test that interval above maximum (3600) raises ValueError."""
        monkeypatch.setenv("AUTH_TOKEN", "test_token")
        monkeypatch.setenv("USER_OID", "test_oid")
        monkeypatch.setenv("INTERVAL", "4000")

        with pytest.raises(ValueError) as exc_info:
            load_env()

        assert "Invalid environment configuration" in str(exc_info.value)

    def test_load_env_whitespace_auth_token(self, monkeypatch):
        """Test that whitespace-only AUTH_TOKEN raises ValueError."""
        monkeypatch.setenv("AUTH_TOKEN", "   ")
        monkeypatch.setenv("USER_OID", "test_oid")

        with pytest.raises(ValueError) as exc_info:
            load_env()

        assert "Invalid environment configuration" in str(exc_info.value)

    def test_load_env_whitespace_user_oid(self, monkeypatch):
        """Test that whitespace-only USER_OID raises ValueError."""
        monkeypatch.setenv("AUTH_TOKEN", "test_token")
        monkeypatch.setenv("USER_OID", "   ")

        with pytest.raises(ValueError) as exc_info:
            load_env()

        assert "Invalid environment configuration" in str(exc_info.value)

    def test_load_env_strips_whitespace(self, monkeypatch):
        """Test that auth_token and user_oid are stripped of whitespace."""
        monkeypatch.setenv("AUTH_TOKEN", "  test_token  ")
        monkeypatch.setenv("USER_OID", "  test_oid  ")

        config = load_env()

        assert config.auth_token == "test_token"
        assert config.user_oid == "test_oid"

    def test_load_env_interval_at_minimum(self, monkeypatch):
        """Test that interval at minimum boundary (10) is accepted."""
        monkeypatch.setenv("AUTH_TOKEN", "test_token")
        monkeypatch.setenv("USER_OID", "test_oid")
        monkeypatch.setenv("INTERVAL", "10")

        config = load_env()

        assert config.interval == 10

    def test_load_env_interval_at_maximum(self, monkeypatch):
        """Test that interval at maximum boundary (3600) is accepted."""
        monkeypatch.setenv("AUTH_TOKEN", "test_token")
        monkeypatch.setenv("USER_OID", "test_oid")
        monkeypatch.setenv("INTERVAL", "3600")

        config = load_env()

        assert config.interval == 3600


class TestLogConfigEnvVars:
    """Tests for log configuration environment variables."""

    def test_log_config_defaults(self, monkeypatch):
        """Test default values for log configuration."""
        monkeypatch.setenv("AUTH_TOKEN", "test_token")
        monkeypatch.setenv("USER_OID", "test_oid")

        config = load_env()

        assert config.log_max_size_mb == 5
        assert config.log_backup_count == 5
        assert config.log_retention_days == 30

    def test_log_max_size_mb_custom(self, monkeypatch):
        """Test custom LOG_MAX_SIZE_MB value."""
        monkeypatch.setenv("AUTH_TOKEN", "test_token")
        monkeypatch.setenv("USER_OID", "test_oid")
        monkeypatch.setenv("LOG_MAX_SIZE_MB", "10")

        config = load_env()

        assert config.log_max_size_mb == 10

    def test_log_backup_count_custom(self, monkeypatch):
        """Test custom LOG_BACKUP_COUNT value."""
        monkeypatch.setenv("AUTH_TOKEN", "test_token")
        monkeypatch.setenv("USER_OID", "test_oid")
        monkeypatch.setenv("LOG_BACKUP_COUNT", "10")

        config = load_env()

        assert config.log_backup_count == 10

    def test_log_retention_days_custom(self, monkeypatch):
        """Test custom LOG_RETENTION_DAYS value."""
        monkeypatch.setenv("AUTH_TOKEN", "test_token")
        monkeypatch.setenv("USER_OID", "test_oid")
        monkeypatch.setenv("LOG_RETENTION_DAYS", "90")

        config = load_env()

        assert config.log_retention_days == 90

    def test_log_max_size_mb_minimum(self, monkeypatch):
        """Test LOG_MAX_SIZE_MB at minimum boundary (1)."""
        monkeypatch.setenv("AUTH_TOKEN", "test_token")
        monkeypatch.setenv("USER_OID", "test_oid")
        monkeypatch.setenv("LOG_MAX_SIZE_MB", "1")

        config = load_env()

        assert config.log_max_size_mb == 1

    def test_log_max_size_mb_below_minimum_raises(self, monkeypatch):
        """Test LOG_MAX_SIZE_MB below minimum raises error."""
        monkeypatch.setenv("AUTH_TOKEN", "test_token")
        monkeypatch.setenv("USER_OID", "test_oid")
        monkeypatch.setenv("LOG_MAX_SIZE_MB", "0")

        with pytest.raises(ValueError):
            load_env()

    def test_log_backup_count_minimum(self, monkeypatch):
        """Test LOG_BACKUP_COUNT at minimum boundary (1)."""
        monkeypatch.setenv("AUTH_TOKEN", "test_token")
        monkeypatch.setenv("USER_OID", "test_oid")
        monkeypatch.setenv("LOG_BACKUP_COUNT", "1")

        config = load_env()

        assert config.log_backup_count == 1

    def test_log_retention_days_maximum(self, monkeypatch):
        """Test LOG_RETENTION_DAYS at maximum boundary (365)."""
        monkeypatch.setenv("AUTH_TOKEN", "test_token")
        monkeypatch.setenv("USER_OID", "test_oid")
        monkeypatch.setenv("LOG_RETENTION_DAYS", "365")

        config = load_env()

        assert config.log_retention_days == 365
