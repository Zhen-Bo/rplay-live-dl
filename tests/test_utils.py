"""Tests for utility functions."""

import pytest

from core.utils import format_file_size


class TestFormatFileSize:
    """Tests for format_file_size function."""

    @pytest.mark.parametrize(
        "size_bytes,expected",
        [
            # Zero
            (0, "0.0 B"),
            # Bytes range (0-1023)
            (1, "1.0 B"),
            (512, "512.0 B"),
            (1023, "1023.0 B"),
            # Kilobytes range
            (1024, "1.0 KB"),
            (1536, "1.5 KB"),
            (10240, "10.0 KB"),
            (1024 * 1023, "1023.0 KB"),
            # Megabytes range
            (1024 * 1024, "1.0 MB"),
            (1024 * 1024 * 5, "5.0 MB"),
            (int(1024 * 1024 * 2.5), "2.5 MB"),
            # Gigabytes range
            (1024 * 1024 * 1024, "1.0 GB"),
            (1024 * 1024 * 1024 * 4, "4.0 GB"),
            (int(1024 * 1024 * 1024 * 1.5), "1.5 GB"),
            # Terabytes range
            (1024 * 1024 * 1024 * 1024, "1.0 TB"),
        ],
        ids=[
            "zero_bytes",
            "1_byte",
            "512_bytes",
            "1023_bytes",
            "1_KB",
            "1.5_KB",
            "10_KB",
            "1023_KB",
            "1_MB",
            "5_MB",
            "2.5_MB",
            "1_GB",
            "4_GB",
            "1.5_GB",
            "1_TB",
        ],
    )
    def test_format_file_size(self, size_bytes: int, expected: str):
        """Test formatting file sizes across all unit ranges."""
        assert format_file_size(size_bytes) == expected
