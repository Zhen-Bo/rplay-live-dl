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


def test_release_image_references_use_latest():
    """Test that published image references track :latest instead of stale pinned tags."""
    root = Path(__file__).resolve().parents[1]
    compose = (root / "docker-compose.yaml").read_text()
    readme = (root / "README.md").read_text()

    assert re.search(r"^\s*image:\s+paverz/rplay-live-dl:latest\s*$", compose, re.MULTILINE)
    assert "paverz/rplay-live-dl:latest" in readme
    for filename, text in (("docker-compose.yaml", compose), ("README.md", readme)):
        assert not re.search(r"paverz/rplay-live-dl:v\d", text), (
            f"{filename} still pins an old image tag; use :latest"
        )
