"""Tests for application startup helpers."""

import logging

from main import _warn_about_orphaned_downloads


def test_warns_about_each_kind_of_leftover(tmp_path, monkeypatch, caplog):
    """Test startup reports each supported orphaned download file kind."""
    monkeypatch.chdir(tmp_path)
    archive_dir = tmp_path / "archive" / "Creator"
    archive_dir.mkdir(parents=True)
    for filename in (
        "20260727_020000_#Creator x.ts",
        "video.ts.part",
        "video.ts.ytdl",
        "video.ts.part-Frag3",
    ):
        (archive_dir / filename).write_bytes(b"leftover")

    caplog.set_level(logging.WARNING)
    _warn_about_orphaned_downloads(logging.getLogger("test_main"))

    assert any("4 file(s)" in record.getMessage() for record in caplog.records)


def test_silent_when_archive_is_clean(tmp_path, monkeypatch, caplog):
    """Test startup does not warn when only completed files remain."""
    monkeypatch.chdir(tmp_path)
    archive_dir = tmp_path / "archive" / "Creator"
    archive_dir.mkdir(parents=True)
    (archive_dir / "final.mp4").write_bytes(b"complete")

    caplog.set_level(logging.WARNING)
    _warn_about_orphaned_downloads(logging.getLogger("test_main"))

    assert not any(record.levelno >= logging.WARNING for record in caplog.records)


def test_silent_when_archive_missing(tmp_path, monkeypatch, caplog):
    """Test startup ignores a missing archive directory."""
    monkeypatch.chdir(tmp_path)

    _warn_about_orphaned_downloads(logging.getLogger("test_main"))

    assert not caplog.records
