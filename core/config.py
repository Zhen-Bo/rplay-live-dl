import logging
from typing import Dict, List, Union

import yaml
from pydantic import ValidationError

from models.config import CreatorProfile

logger = logging.getLogger("Config")


class ConfigError(Exception):
    """
    Custom exception for configuration-related errors.
    """

    pass


def read_config(config_path: str) -> Union[List[CreatorProfile], ConfigError]:
    """
    Read and parse the YAML configuration file to extract creator profiles.

    The configuration file must contain a 'creators' list where each item has:
    - name: The display name of the creator
    - id: The unique identifier (OID) of the creator

    Args:
        config_path: Path to the YAML configuration file

    Returns:
        List[CreatorProfile]: List of validated creator profiles
        ConfigError: If any error occurs during parsing or validation

    Example YAML format:
        creators:
            - name: "Creator Name"
              id: "creator_unique_id"
    """
    try:
        with open(config_path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
            creators = __parse_creators(data)
            return creators
    except yaml.YAMLError as e:
        return ConfigError(f"YAML format error: {e}")
    except Exception as e:
        return ConfigError(f"Unexpected error while reading file: {e}")


def __parse_creators(
    yaml_data: Dict[str, List[Dict[str, str]]]
) -> List[CreatorProfile]:
    """
    Parse and validate creator profiles from YAML data.

    Args:
        yaml_data: Dictionary containing the parsed YAML data

    Returns:
        List of validated CreatorProfile objects

    Note:
        Invalid entries are logged but skipped to allow partial processing
    """
    creators = []

    for item in yaml_data["creators"]:
        try:
            creator = CreatorProfile(
                creator_name=item.get("name"), creator_oid=item.get("id")
            )
            creators.append(creator)
        except ValidationError as e:
            # Log validation failures but continue processing other entries
            logger.warning(f"Error creating CreatorProfile: {e}")
            logger.warning(f"Problematic data: {item}")
            continue
    return creators
