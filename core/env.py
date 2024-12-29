import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import ValidationError

from models.env import EnvConfig


def load_env() -> EnvConfig:
    """
    Load and validate environment variables.

    Returns:
        EnvConfig: Validated environment configuration object

    Raises:
        FileNotFoundError: If .env file is not found
        ValueError: If environment variables are invalid or missing
    """
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        load_dotenv()

    auth_token: Optional[str] = os.getenv("AUTH_TOKEN")
    user_oid: Optional[str] = os.getenv("USER_OID")
    interval: str = os.getenv("INTERVAL", "10")

    if not auth_token or not user_oid:
        raise FileNotFoundError(
            "Required environment variables AUTH_TOKEN and USER_OID not found in either .env file or system environment"
        )

    try:
        return EnvConfig(
            auth_token=auth_token, user_oid=user_oid, interval=int(interval)
        )
    except ValidationError as e:
        raise ValueError(f"Invalid environment variables: {str(e)}") from e
    except ValueError as e:
        raise ValueError(
            f"Invalid interval value '{interval}': must be an integer"
        ) from e
