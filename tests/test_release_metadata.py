import re
from pathlib import Path

import main


def test_version_is_readable():
    """Test that __version__ is successfully read from pyproject.toml."""
    assert isinstance(main.__version__, str)
    assert len(main.__version__) > 0


def test_version_is_valid_semver():
    """Test that __version__ follows semver (major.minor.patch) format."""
    assert re.match(r"^\d+\.\d+\.\d+", main.__version__)


def test_release_version_is_consistent():
    """Test that release metadata uses the same version in every published location."""
    root = Path(__file__).resolve().parents[1]
    versions = {
        "pyproject.toml": re.search(
            r'^\s*version\s*=\s*"(\d+\.\d+\.\d+)',
            (root / "pyproject.toml").read_text(),
            re.MULTILINE,
        ).group(1),
        "docker-compose.yaml": re.search(
            r"^\s*image:\s+\S+:v?(\d+\.\d+\.\d+)",
            (root / "docker-compose.yaml").read_text(),
            re.MULTILINE,
        ).group(1),
        "README.md": re.search(
            r"paverz/rplay-live-dl:v?(\d+\.\d+\.\d+)",
            (root / "README.md").read_text(),
        ).group(1),
    }
    expected = versions["pyproject.toml"]

    for filename, version in versions.items():
        assert version == expected, (
            f"{filename} version {version!r} disagrees with "
            f"pyproject.toml version {expected!r}"
        )
