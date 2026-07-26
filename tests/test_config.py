"""Tests for configuration module."""

import tempfile
from pathlib import Path

import pytest

from core.config import (
    ConfigError,
    DEFAULT_CONFIG_PATH,
    LEGACY_CONFIG_PATH,
    read_app_config,
    validate_startup_config_path,
)


class TestReadAppConfig:
    """Tests for read_app_config function."""

    def test_explicit_api_base_url_is_respected(self, tmp_path):
        """Test explicit apiBaseUrl is returned without being overwritten."""
        config_file = tmp_path / "config.yaml"
        original_text = """
apiBaseUrl: https://api.example.com/
creators:
  - name: "Creator One"
    id: "abc123"
"""
        config_file.write_text(original_text, encoding="utf-8")

        result = read_app_config(str(config_file))

        assert result.api_base_url == "https://api.example.com"
        assert config_file.read_text(encoding="utf-8") == original_text

    def test_invalid_api_base_url_raises_config_error(self, tmp_path):
        """Test invalid apiBaseUrl is rejected as a config error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
apiBaseUrl: not-a-url
creators: []
""",
            encoding="utf-8",
        )

        with pytest.raises(ConfigError, match="Invalid apiBaseUrl"):
            read_app_config(str(config_file))


class TestValidateStartupConfigPath:
    """Tests for startup-time config path validation."""

    def test_accepts_existing_new_default_config(self, tmp_path, monkeypatch):
        """Test startup validation passes when the new config path exists."""
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("creators: []", encoding="utf-8")

        validate_startup_config_path(DEFAULT_CONFIG_PATH)

    def test_raises_migration_error_when_only_legacy_config_exists(self, tmp_path, monkeypatch):
        """Test startup validation points users to the new config location."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text("creators: []", encoding="utf-8")

        with pytest.raises(ConfigError) as exc_info:
            validate_startup_config_path(DEFAULT_CONFIG_PATH)

        message = str(exc_info.value)
        assert LEGACY_CONFIG_PATH in message
        assert DEFAULT_CONFIG_PATH in message
        assert "./config:/app/config" in message

    def test_missing_custom_config_keeps_normal_not_found_error(self, tmp_path):
        """Test custom config paths do not trigger the legacy migration hint."""
        custom_path = tmp_path / "custom.yaml"

        with pytest.raises(ConfigError) as exc_info:
            validate_startup_config_path(str(custom_path))

        assert str(exc_info.value) == f"Configuration file not found: {custom_path}"
