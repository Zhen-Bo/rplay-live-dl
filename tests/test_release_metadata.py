import tomllib
from pathlib import Path

import main


def _normalize_runtime_version_for_package(version: str) -> str:
    base, separator, suffix = version.partition("-")
    if not separator:
        return version
    return f"{base}+{suffix}"


def test_pyproject_version_matches_runtime_release_with_pep440_normalization():
    """Test pyproject metadata tracks the same release as the runtime version."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["tool"]["poetry"]["version"] == _normalize_runtime_version_for_package(
        main.__version__
    )
