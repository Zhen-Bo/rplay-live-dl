"""Tests for configuration module."""

import tempfile
from pathlib import Path

import pytest

from core.config import ConfigError, read_config, validate_config


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
