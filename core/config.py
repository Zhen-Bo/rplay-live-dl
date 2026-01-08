"""
Configuration module for rplay-live-dl.

Provides functionality to read and parse YAML configuration files
containing creator profiles for monitoring.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import ValidationError

from core.logger import setup_logger
from models.config import CreatorProfile

__all__ = [
    "ConfigError",
    "read_config",
    "validate_config",
]

# Use lazy logger initialization to allow test patching
_logger: Optional[logging.Logger] = None


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


def read_config(config_path: str) -> List[CreatorProfile]:
    """
    Read and parse the YAML configuration file to extract creator profiles.

    The configuration file must contain a 'creators' list where each item has:
    - name: The display name of the creator
    - id: The unique identifier (OID) of the creator

    Args:
        config_path: Path to the YAML configuration file

    Returns:
        List[CreatorProfile]: List of validated creator profiles

    Raises:
        ConfigError: If the file cannot be read or parsed

    Example YAML format:
        creators:
            - name: "Creator Name"
              id: "creator_unique_id"
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
                return []

            # Validate structure
            if not isinstance(data, dict):
                error_msg = "Configuration file must contain a YAML dictionary"
                _get_logger().error(error_msg)
                raise ConfigError(error_msg)

            if "creators" not in data:
                _get_logger().warning("No 'creators' key found in configuration")
                return []

            creators = _parse_creators(data)
            _get_logger().debug(f"Loaded {len(creators)} creator(s) from configuration")
            return creators

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
