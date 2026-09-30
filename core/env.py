"""Load and validate environment configuration."""

from pydantic import ValidationError

from models.env import EnvConfig

__all__ = [
    "EnvConfigError",
    "EnvConfig",
    "load_env",
]


class EnvConfigError(Exception):
    pass


def load_env() -> EnvConfig:
    """Raise EnvConfigError for missing variables and ValueError for invalid values."""
    try:
        return EnvConfig.model_validate({})
    except ValidationError as e:
        missing_vars = []
        other_errors = []

        for error in e.errors():
            field = error.get("loc", [None])[0]
            error_type = error.get("type", "")

            env_var = str(field).upper() if field else "UNKNOWN"
            if error_type == "missing":
                missing_vars.append(env_var)
            else:
                other_errors.append(f"{env_var}: {error.get('msg', str(error))}")

        if missing_vars:
            details = f" {'; '.join(other_errors)}" if other_errors else ""
            raise EnvConfigError(
                f"Missing required environment variable(s): {', '.join(missing_vars)}. "
                f"Please set them in .env file or as system environment variables.{details}"
            ) from e

        if other_errors:
            raise ValueError(
                f"Invalid environment configuration: {'; '.join(other_errors)}"
            ) from e

        raise EnvConfigError(f"Configuration error: {e}") from e
