"""
Environment configuration module.

Provides functionality to load and validate environment variables
for the rplay-live-dl application.
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import ValidationError

from models.env import EnvConfig

__all__ = [
    "EnvConfigError",
    "EnvConfig",
    "load_env",
    "MIN_INTERVAL",
    "MAX_INTERVAL",
]

# Minimum and maximum values for interval
MIN_INTERVAL = 10  # seconds
MAX_INTERVAL = 3600  # 1 hour


class EnvConfigError(Exception):
    """Exception raised for environment configuration errors."""

    pass


def load_env(env_path: Optional[str] = None) -> EnvConfig:
    """
    Load and validate environment variables.

    Loads configuration from a .env file if present, then validates
    the required environment variables for the application.

    Args:
        env_path: Optional path to .env file. If not provided,
                  looks for .env in the current working directory.

    Returns:
        EnvConfig: Validated environment configuration object

    Raises:
        EnvConfigError: If required environment variables are missing
        ValueError: If environment variables have invalid values
    """
    # Determine .env file path
    if env_path:
        dotenv_path = Path(env_path)
    else:
        dotenv_path = Path.cwd() / ".env"

    # Load .env file if it exists
    if dotenv_path.exists():
        load_dotenv(dotenv_path)

    # Get required environment variables
    auth_token: Optional[str] = os.getenv("AUTH_TOKEN")
    user_oid: Optional[str] = os.getenv("USER_OID")
    interval_str: str = os.getenv("INTERVAL", "60")

    # Validate required variables
    missing_vars = []
    if not auth_token:
        missing_vars.append("AUTH_TOKEN")
    if not user_oid:
        missing_vars.append("USER_OID")

    if missing_vars:
        raise EnvConfigError(
            f"Missing required environment variable(s): {', '.join(missing_vars)}. "
            f"Please set them in .env file or as system environment variables."
        )

    # Parse interval as integer
    try:
        interval = int(interval_str)
    except ValueError:
        raise ValueError(
            f"Invalid INTERVAL value '{interval_str}': must be an integer"
        )

    # Create and return validated config (Pydantic handles range validation)
    try:
        return EnvConfig(
            auth_token=auth_token,
            user_oid=user_oid,
            interval=interval,
        )
    except ValidationError as e:
        raise ValueError(f"Invalid environment configuration: {e}") from e
