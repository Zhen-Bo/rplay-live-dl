"""
Configuration module for rplay-live-dl.

Provides functionality to read and parse YAML configuration files
containing creator profiles for monitoring.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError

from core.constants import DEFAULT_RPLAY_API_BASE_URL
from core.logger import setup_logger
from models.config import AppConfig, CreatorProfile

__all__ = [
    "ConfigError",
    "DEFAULT_CONFIG_PATH",
    "LEGACY_CONFIG_PATH",
    "DEFAULT_RPLAY_API_BASE_URL",
    "read_app_config",
    "read_config",
    "validate_config",
    "validate_startup_config_path",
]

# Use lazy logger initialization to allow test patching
_logger: Optional[logging.Logger] = None


DEFAULT_CONFIG_PATH = "./config/config.yaml"
LEGACY_CONFIG_PATH = "./config.yaml"


def _get_logger() -> logging.Logger:
    """Get or create the module logger."""
    global _logger
    if _logger is None:
        _logger = setup_logger("Config")
    return _logger


class ConfigError(Exception):
    """
    Custom exception for configuration-related errors.

    Raised when the configuration file cannot be read, parsed,
    or contains invalid data.
    """

    pass


def validate_startup_config_path(config_path: str) -> None:
    """Validate startup config path and surface legacy-path migration errors early."""
    path = Path(config_path)
    if path.exists():
        return

    default_path = Path(DEFAULT_CONFIG_PATH)
    legacy_path = Path(LEGACY_CONFIG_PATH)
    if path == default_path and legacy_path.exists():
        raise ConfigError(
            f"Detected legacy config at {LEGACY_CONFIG_PATH}. "
            f"Since 2.0.0-vibe, move it to {DEFAULT_CONFIG_PATH}. "
            "If using Docker, mount ./config:/app/config."
        )

    raise ConfigError(f"Configuration file not found: {config_path}")


def read_config(config_path: str) -> List[CreatorProfile]:
    """Backward-compatible helper that returns only creator profiles."""
    return read_app_config(config_path).creators


def read_app_config(config_path: str) -> AppConfig:
    """
    Read and parse the YAML configuration file to extract application config.

    The configuration file may include:
    - apiBaseUrl: Base URL for the RPlay API
    - creators: The monitored creators list

    Args:
        config_path: Path to the YAML configuration file

    Returns:
        AppConfig: Validated application configuration

    Raises:
        ConfigError: If the file cannot be read or parsed
    """
    path = Path(config_path)

    # Check if file exists
    if not path.exists():
        error_msg = f"Configuration file not found: {config_path}"
        _get_logger().error(error_msg)
        raise ConfigError(error_msg)

    # Check if file is readable
    if not path.is_file():
        error_msg = f"Configuration path is not a file: {config_path}"
        _get_logger().error(error_msg)
        raise ConfigError(error_msg)

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file)

            # Handle empty file
            if data is None:
                _get_logger().warning("Configuration file is empty")
                data = {}

            # Validate structure
            if not isinstance(data, dict):
                error_msg = "Configuration file must contain a YAML dictionary"
                _get_logger().error(error_msg)
                raise ConfigError(error_msg)

            api_base_url = _resolve_api_base_url(data, path)
            creators = _parse_creators(data)
            if "creators" not in data:
                _get_logger().warning("No 'creators' key found in configuration")

            config = AppConfig(api_base_url=api_base_url, creators=creators)
            _get_logger().debug(f"Loaded {len(creators)} creator(s) from configuration")
            return config

    except yaml.YAMLError as e:
        error_msg = f"YAML format error: {e}"
        _get_logger().error(error_msg)
        raise ConfigError(error_msg) from e

    except PermissionError as e:
        error_msg = f"Permission denied reading configuration file: {config_path}"
        _get_logger().error(error_msg)
        raise ConfigError(error_msg) from e

    except ConfigError:
        raise

    except Exception as e:
        error_msg = f"Unexpected error while reading configuration: {e}"
        _get_logger().error(error_msg)
        raise ConfigError(error_msg) from e


def _resolve_api_base_url(yaml_data: Dict[str, Any], config_path: Path) -> str:
    """Return the configured API base URL, persisting the default when missing."""
    raw_value = yaml_data.get("apiBaseUrl")
    if raw_value is None:
        _persist_default_api_base_url(config_path)
        return DEFAULT_RPLAY_API_BASE_URL

    api_base_url = str(raw_value).strip().rstrip("/")
    parsed = urlparse(api_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"Invalid apiBaseUrl: {raw_value}")
    return api_base_url


def _persist_default_api_base_url(config_path: Path) -> None:
    """Best-effort migration that writes the default apiBaseUrl into config.yaml."""
    line = f"apiBaseUrl: {DEFAULT_RPLAY_API_BASE_URL}"

    try:
        original = config_path.read_text(encoding="utf-8")
        updated = _prepend_top_level_yaml_key(original, line)
        config_path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        _get_logger().warning(
            f"Failed to persist default apiBaseUrl to {config_path}: {exc}"
        )


def _prepend_top_level_yaml_key(original: str, line: str) -> str:
    """Insert one YAML key near the top while preserving the rest of the file."""
    bom = ""
    if original.startswith("\ufeff"):
        bom = "\ufeff"
        original = original[1:]

    if not original.strip():
        return f"{bom}{line}\n"

    lines = original.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        remainder = "".join(lines[1:])
        separator = "" if remainder.startswith("\n") else "\n"
        return f"{bom}{lines[0]}{line}\n{separator}{remainder}"

    return f"{bom}{line}\n\n{original}"


def _parse_creators(yaml_data: Dict[str, Any]) -> List[CreatorProfile]:
    """
    Parse and validate creator profiles from YAML data.

    Args:
        yaml_data: Dictionary containing the parsed YAML data

    Returns:
        List of validated CreatorProfile objects

    Note:
        Invalid entries are logged but skipped to allow partial processing.
        This allows the application to continue monitoring valid creators
        even if some entries are malformed.
    """
    creators: List[CreatorProfile] = []
    creators_data = yaml_data.get("creators", [])

    # Handle case where creators is not a list
    if not isinstance(creators_data, list):
        _get_logger().warning("'creators' key must contain a list")
        return []

    for index, item in enumerate(creators_data):
        # Skip None entries
        if item is None:
            _get_logger().warning(f"Skipping empty entry at index {index}")
            continue

        # Validate item is a dictionary
        if not isinstance(item, dict):
            _get_logger().warning(f"Skipping invalid entry at index {index}: not a dictionary")
            continue

        try:
            # Get values with explicit None handling
            name = item.get("name")
            creator_id = item.get("id")

            # Check for required fields
            if not name:
                _get_logger().warning(f"Skipping entry at index {index}: missing 'name'")
                continue

            if not creator_id:
                _get_logger().warning(f"Skipping entry at index {index}: missing 'id'")
                continue

            creator = CreatorProfile(
                creator_name=str(name),
                creator_oid=str(creator_id),
            )
            creators.append(creator)

        except ValidationError as e:
            _get_logger().warning(f"Validation error for entry at index {index}: {e}")
            _get_logger().debug(f"Problematic data: {item}")
            continue

    return creators


def validate_config(config_path: str) -> tuple[bool, Optional[str]]:
    """
    Validate a configuration file without loading it for use.

    Args:
        config_path: Path to the YAML configuration file

    Returns:
        Tuple of (is_valid, error_message)
        - is_valid: True if configuration is valid
        - error_message: Error description if invalid, None otherwise
    """
    try:
        result = read_config(config_path)
        if len(result) == 0:
            return False, "No valid creator profiles found"
        return True, None
    except ConfigError as e:
        return False, str(e)
