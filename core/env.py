"""
Environment configuration module.

Provides functionality to load and validate environment variables
for the rplay-live-dl application using pydantic-settings.
"""

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


def load_env() -> EnvConfig:
    """
    Load and validate environment variables.

    Uses pydantic-settings to automatically load configuration from
    environment variables and .env file.

    Returns:
        EnvConfig: Validated environment configuration object

    Raises:
        EnvConfigError: If required environment variables are missing
        ValueError: If environment variables have invalid values
    """
    try:
        return EnvConfig()
    except ValidationError as e:
        # Extract missing field names from validation errors
        missing_vars = []
        other_errors = []

        for error in e.errors():
            field = error.get("loc", [None])[0]
            error_type = error.get("type", "")

            if error_type == "missing":
                # Convert field name to env var format (e.g., auth_token -> AUTH_TOKEN)
                env_var = field.upper() if field else "UNKNOWN"
                missing_vars.append(env_var)
            else:
                other_errors.append(error.get("msg", str(error)))

        if missing_vars:
            raise EnvConfigError(
                f"Missing required environment variable(s): {', '.join(missing_vars)}. "
                f"Please set them in .env file or as system environment variables."
            ) from e

        if other_errors:
            raise ValueError(
                f"Invalid environment configuration: {'; '.join(other_errors)}"
            ) from e

        raise EnvConfigError(f"Configuration error: {e}") from e
