"""Tests for environment configuration module."""

import pytest

from core.env import EnvConfigError, load_env


@pytest.fixture
def valid_env(monkeypatch):
    """Minimal valid credentials. Tests about other settings build on this."""
    monkeypatch.setenv("REFRESH_TOKEN", "test_token")
    monkeypatch.setenv("USER_OID", "test_oid")


class TestLoadEnv:
    """Tests for load_env function."""

    def test_load_env_success(self, monkeypatch):
        """Test successfully loading environment variables."""
        monkeypatch.setenv("REFRESH_TOKEN", "test_token_123")
        monkeypatch.setenv("USER_OID", "test_user_456")
        monkeypatch.setenv("INTERVAL", "120")

        config = load_env()

        assert config.refresh_token == "test_token_123"
        assert config.user_oid == "test_user_456"
        assert config.interval == 120

    def test_load_env_default_interval(self, valid_env):
        """Test that interval has default value of 60."""
        # Don't set INTERVAL to test default

        config = load_env()

        assert config.interval == 60

    def test_load_env_missing_refresh_token(self, no_dotenv_file):
        """Without REFRESH_TOKEN, startup fails with a clear message."""
        # Clear any existing env vars
        no_dotenv_file.delenv("REFRESH_TOKEN", raising=False)
        no_dotenv_file.setenv("USER_OID", "test_oid")

        with pytest.raises(ValueError) as exc_info:
            load_env()

        assert "REFRESH_TOKEN" in str(exc_info.value)
        assert "Invalid environment configuration" in str(exc_info.value)

    def test_load_env_missing_both_required(self, no_dotenv_file):
        """Test that missing both required vars raises EnvConfigError."""
        no_dotenv_file.delenv("REFRESH_TOKEN", raising=False)
        no_dotenv_file.delenv("USER_OID", raising=False)

        with pytest.raises(EnvConfigError) as exc_info:
            load_env()

        error_msg = str(exc_info.value)
        assert "REFRESH_TOKEN" in error_msg
        assert "USER_OID" in error_msg

    def test_load_env_whitespace_user_oid(self, monkeypatch, valid_env):
        """Test that whitespace-only USER_OID raises ValueError."""
        monkeypatch.setenv("USER_OID", "   ")

        with pytest.raises(ValueError) as exc_info:
            load_env()

        assert "Invalid environment configuration" in str(exc_info.value)

    def test_load_env_strips_whitespace(self, monkeypatch):
        """Test that refresh_token and user_oid are stripped of whitespace."""
        monkeypatch.setenv("REFRESH_TOKEN", "  test_token  ")
        monkeypatch.setenv("USER_OID", "  test_oid  ")

        config = load_env()

        assert config.refresh_token == "test_token"
        assert config.user_oid == "test_oid"


class TestRefreshConfig:
    @pytest.mark.parametrize("legacy", [None, "", "  ", "unused-token"])
    def test_legacy_auth_token_is_ignored_when_refresh_token_is_set(
        self, monkeypatch, legacy
    ):
        monkeypatch.delenv("AUTH_TOKEN", raising=False)
        if legacy is not None:
            monkeypatch.setenv("AUTH_TOKEN", legacy)
        monkeypatch.setenv("USER_OID", "test-oid")
        monkeypatch.setenv("REFRESH_TOKEN", "  test-refresh  ")
        config = load_env()
        assert config.refresh_token == "test-refresh"
        assert config.token_refresh_leeway_seconds == 300
        assert "test-refresh" not in repr(config)
        assert "unused-token" not in repr(config)

    def test_legacy_auth_token_alone_explains_the_migration(self, no_dotenv_file):
        no_dotenv_file.setenv("AUTH_TOKEN", "legacy-secret")
        no_dotenv_file.setenv("USER_OID", "test-oid")
        no_dotenv_file.delenv("REFRESH_TOKEN", raising=False)
        with pytest.raises(ValueError, match="no longer supported") as caught:
            load_env()
        assert "REFRESH_TOKEN" in str(caught.value)
        assert "legacy-secret" not in str(caught.value)

    def test_blank_refresh_token_is_rejected(self, monkeypatch, valid_env):
        monkeypatch.setenv("REFRESH_TOKEN", "  ")
        with pytest.raises(ValueError, match="REFRESH_TOKEN"):
            load_env()

    def test_refresh_only_still_requires_user_oid(self, no_dotenv_file):
        no_dotenv_file.setenv("REFRESH_TOKEN", "private-refresh")
        no_dotenv_file.delenv("USER_OID", raising=False)
        with pytest.raises(EnvConfigError, match="USER_OID") as caught:
            load_env()
        assert "Missing required" in str(caught.value)
        assert "private-refresh" not in str(caught.value)

    @pytest.mark.parametrize("value", ["0", "60", "300"])
    def test_valid_leeway(self, monkeypatch, valid_env, value):
        monkeypatch.setenv("TOKEN_REFRESH_LEEWAY_SECONDS", value)
        assert load_env().token_refresh_leeway_seconds == int(value)

    @pytest.mark.parametrize("value", ["-1", "", "nan", "1.5"])
    def test_invalid_leeway(self, monkeypatch, value):
        monkeypatch.setenv("REFRESH_TOKEN", "private-refresh")
        monkeypatch.setenv("USER_OID", "test-oid")
        monkeypatch.setenv("TOKEN_REFRESH_LEEWAY_SECONDS", value)
        with pytest.raises(ValueError, match="TOKEN_REFRESH_LEEWAY_SECONDS") as caught:
            load_env()
        assert "private-refresh" not in str(caught.value)


class TestLogConfigEnvVars:
    """Tests for log configuration environment variables."""

    def test_log_config_defaults(self, valid_env):
        """Test public default values for log configuration."""
        config = load_env()

        assert config.log_level == "INFO"
        assert config.log_ytdlp_internal is False
        assert config.log_max_size_mb == 5
        assert config.log_backup_count == 5
        assert config.log_retention_days == 30

    def test_log_level_valid_debug(self, monkeypatch, valid_env):
        """Test valid LOG_LEVEL=DEBUG is accepted and normalized."""
        monkeypatch.setenv("LOG_LEVEL", "debug")

        config = load_env()

        assert config.log_level == "DEBUG"

    def test_log_level_invalid_raises(self, monkeypatch, valid_env):
        """Test invalid LOG_LEVEL is a startup validation error."""
        monkeypatch.setenv("LOG_LEVEL", "LOUD")

        with pytest.raises(ValueError) as exc_info:
            load_env()

        assert "Invalid environment configuration" in str(exc_info.value)
        assert "LOG_LEVEL" in str(exc_info.value)

    def test_log_ytdlp_internal_parsing(self, monkeypatch, valid_env):
        """Test LOG_YTDLP_INTERNAL truthy/falsy/invalid spellings."""

        cases = [
            ("1", True),
            ("true", True),
            ("TRUE", True),
            ("  true  ", True),
            ("yes", True),
            ("on", True),
            ("0", False),
            ("false", False),
            ("FALSE", False),
            ("no", False),
            ("off", False),
            ("", False),
            ("  ", False),
        ]
        for value, expected in cases:
            monkeypatch.setenv("LOG_YTDLP_INTERNAL", value)
            config = load_env()
            assert (
                config.log_ytdlp_internal is expected
            ), f"expected {expected} for {value!r}"

        for value in ("y", "t", "maybe", "2"):
            monkeypatch.setenv("LOG_YTDLP_INTERNAL", value)
            with pytest.raises(ValueError) as exc_info:
                load_env()
            message = str(exc_info.value)
            assert "Invalid environment configuration" in message
            assert "LOG_YTDLP_INTERNAL" in message

    @pytest.mark.parametrize(
        "name,attr,raw",
        [
            ("LOG_MAX_SIZE_MB", "log_max_size_mb", "1"),
            ("LOG_BACKUP_COUNT", "log_backup_count", "1"),
            ("LOG_RETENTION_DAYS", "log_retention_days", "365"),
        ],
    )
    def test_log_rotation_boundary_values_accepted(
        self, monkeypatch, valid_env, name, attr, raw
    ):
        """Test that a rotation setting at its boundary maps to its field."""
        monkeypatch.setenv(name, raw)

        config = load_env()

        assert getattr(config, attr) == int(raw)

    def test_log_max_size_mb_below_minimum_raises(self, monkeypatch, valid_env):
        """Test LOG_MAX_SIZE_MB below minimum raises error."""
        monkeypatch.setenv("LOG_MAX_SIZE_MB", "0")

        with pytest.raises(ValueError):
            load_env()


class TestMinFreeDiskGbEnv:
    """Tests for MIN_FREE_DISK_GB environment variable."""

    @pytest.mark.parametrize("raw,expected", [("10.5", 10.5), ("0", 0.0)])
    def test_valid_values_accepted(self, monkeypatch, valid_env, raw, expected):
        """Test MIN_FREE_DISK_GB is read, and 0 is accepted (disables the guard)."""
        monkeypatch.setenv("MIN_FREE_DISK_GB", raw)

        config = load_env()

        assert config.min_free_disk_gb == expected

    @pytest.mark.parametrize(
        "raw",
        ["", "nan", "inf", "-inf", "-1"],
    )
    def test_invalid_values_raise_actionable_error(self, monkeypatch, valid_env, raw):
        """Blank, non-finite, and negative values fail startup with field name."""
        monkeypatch.setenv("MIN_FREE_DISK_GB", raw)

        with pytest.raises(ValueError) as exc_info:
            load_env()

        message = str(exc_info.value)
        assert "Invalid environment configuration" in message
        assert "MIN_FREE_DISK_GB" in message
