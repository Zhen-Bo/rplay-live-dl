import re

import main


def test_version_is_readable():
    """Test that __version__ is successfully read from pyproject.toml."""
    assert isinstance(main.__version__, str)
    assert len(main.__version__) > 0


def test_version_is_valid_semver():
    """Test that __version__ follows semver (major.minor.patch) format."""
    assert re.match(r"^\d+\.\d+\.\d+", main.__version__)
