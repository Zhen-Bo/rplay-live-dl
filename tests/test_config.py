"""Tests for configuration module."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from core.config import (
    ConfigError,
    DEFAULT_CONFIG_PATH,
    LEGACY_CONFIG_PATH,
    read_app_config,
    read_config,
    validate_config,
    validate_startup_config_path,
)


class TestReadConfig:
    """Tests for read_config function."""

    def test_valid_config(self, tmp_path):
        """Test reading a valid configuration file."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
creators:
  - name: "Creator One"
    id: "abc123"
  - name: "Creator Two"
    id: "def456"
""")
        result = read_config(str(config_file))
        assert len(result) == 2
        assert result[0].creator_name == "Creator One"
        assert result[0].creator_oid == "abc123"

    def test_empty_file(self, tmp_path):
        """Test reading an empty configuration file."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")
        result = read_config(str(config_file))
        assert len(result) == 0

    def test_missing_file(self, tmp_path):
        """Test reading a non-existent configuration file."""
        with pytest.raises(ConfigError):
            read_config(str(tmp_path / "nonexistent.yaml"))

    def test_invalid_yaml(self, tmp_path):
        """Test reading an invalid YAML file."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("invalid: yaml: content:")
        with pytest.raises(ConfigError):
            read_config(str(config_file))

    def test_missing_creators_key(self, tmp_path):
        """Test reading a file without creators key."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("other_key: value")
        result = read_config(str(config_file))
        assert len(result) == 0

    def test_creators_not_list(self, tmp_path):
        """Test reading a file where creators is not a list."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("creators: not_a_list")
        result = read_config(str(config_file))
        assert len(result) == 0

    def test_missing_name(self, tmp_path):
        """Test skipping entries with missing name."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
creators:
  - id: "abc123"
  - name: "Valid Creator"
    id: "def456"
""")
        result = read_config(str(config_file))
        assert len(result) == 1
        assert result[0].creator_name == "Valid Creator"

    def test_missing_id(self, tmp_path):
        """Test skipping entries with missing id."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
creators:
  - name: "No ID Creator"
  - name: "Valid Creator"
    id: "def456"
""")
        result = read_config(str(config_file))
        assert len(result) == 1
        assert result[0].creator_name == "Valid Creator"


class TestValidateConfig:
    """Tests for validate_config function."""

    def test_valid_config(self, tmp_path):
        """Test validating a valid configuration."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
creators:
  - name: "Creator"
    id: "abc123"
""")
        is_valid, error = validate_config(str(config_file))
        assert is_valid is True
        assert error is None

    def test_empty_config(self, tmp_path):
        """Test validating an empty configuration."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")
        is_valid, error = validate_config(str(config_file))
        assert is_valid is False
        assert error is not None

    def test_missing_file(self, tmp_path):
        """Test validating a non-existent file."""
        is_valid, error = validate_config(str(tmp_path / "nonexistent.yaml"))
        assert is_valid is False
        assert error is not None


class TestReadAppConfig:
    """Tests for read_app_config function."""

    def test_missing_api_base_url_defaults_and_persists(self, tmp_path):
        """Test missing apiBaseUrl defaults to api.rplay.live and writes back."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            """
creators:
  - name: "Creator One"
    id: "abc123"
""",
            encoding="utf-8",
        )

        result = read_app_config(str(config_file))

        assert result.api_base_url == "https://api.rplay.live"
        assert len(result.creators) == 1
        assert result.creators[0].creator_name == "Creator One"

        updated_text = config_file.read_text(encoding="utf-8")
        assert updated_text.startswith("apiBaseUrl: https://api.rplay.live")
        assert "creators:" in updated_text

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

    def test_missing_api_base_url_uses_default_when_persist_fails(self, tmp_path):
        """Test write-back failures do not block using the in-memory default URL."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("creators: []\n", encoding="utf-8")

        with patch("pathlib.Path.write_text", side_effect=OSError("read only")):
            result = read_app_config(str(config_file))

        assert result.api_base_url == "https://api.rplay.live"


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
