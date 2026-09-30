"""Reads the YAML config with the monitored creator profiles."""

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
    "validate_startup_config_path",
]

# Use lazy logger initialization to allow test patching
_logger: Optional[logging.Logger] = None


DEFAULT_CONFIG_PATH = "./config/config.yaml"
LEGACY_CONFIG_PATH = "./config.yaml"


def _get_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        _logger = setup_logger("Config")
    return _logger


class ConfigError(Exception):
    pass


def validate_startup_config_path(config_path: str) -> None:
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


def read_app_config(config_path: str) -> AppConfig:
    """Raises ConfigError if the file cannot be read or parsed."""
    path = Path(config_path)

    if not path.exists():
        error_msg = f"Configuration file not found: {config_path}"
        _get_logger().error(error_msg)
        raise ConfigError(error_msg)

    if not path.is_file():
        error_msg = f"Configuration path is not a file: {config_path}"
        _get_logger().error(error_msg)
        raise ConfigError(error_msg)

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file)

            if data is None:
                _get_logger().warning("Configuration file is empty")
                data = {}

            if not isinstance(data, dict):
                error_msg = "Configuration file must contain a YAML dictionary"
                _get_logger().error(error_msg)
                raise ConfigError(error_msg)

            api_base_url = _resolve_api_base_url(data)
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
        _get_logger().exception(error_msg)
        raise ConfigError(error_msg) from e


def _resolve_api_base_url(yaml_data: Dict[str, Any]) -> str:
    raw_value = yaml_data.get("apiBaseUrl")
    if raw_value is None:
        return DEFAULT_RPLAY_API_BASE_URL

    api_base_url = str(raw_value).strip().rstrip("/")
    parsed = urlparse(api_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"Invalid apiBaseUrl: {raw_value}")
    return api_base_url


def _parse_creators(yaml_data: Dict[str, Any]) -> List[CreatorProfile]:
    """Invalid entries are logged and skipped so valid creators keep being monitored."""
    creators: List[CreatorProfile] = []
    creators_data = yaml_data.get("creators", [])

    if not isinstance(creators_data, list):
        _get_logger().warning("'creators' key must contain a list")
        return []

    for index, item in enumerate(creators_data):
        if item is None:
            _get_logger().warning(f"Skipping empty entry at index {index}")
            continue

        if not isinstance(item, dict):
            _get_logger().warning(
                f"Skipping invalid entry at index {index}: not a dictionary"
            )
            continue

        try:
            name = item.get("name")
            creator_id = item.get("id")

            if not name:
                _get_logger().warning(
                    f"Skipping entry at index {index}: missing 'name'"
                )
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
