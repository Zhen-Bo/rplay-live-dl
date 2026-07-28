"""Pytest configuration and fixtures."""

import logging
from types import SimpleNamespace

import pytest

_GIB = 1024 ** 3


@pytest.fixture(autouse=True)
def plenty_of_free_disk(monkeypatch):
    """Keep suite independent of real free disk (MIN_FREE_DISK_GB default is 5).

    Guard-specific tests override this by patching disk_usage themselves.
    """
    usage = SimpleNamespace(total=100 * _GIB, used=10 * _GIB, free=90 * _GIB)
    monkeypatch.setattr("shutil.disk_usage", lambda path: usage)


@pytest.fixture(autouse=True)
def disable_file_logging(monkeypatch):
    """
    Disable file logging during tests to prevent test output pollution.

    This fixture automatically runs for all tests and prevents log files
    from being created or written to during test execution.
    """
    from core import logger as logger_module

    # Store original function
    original_setup_logger = logger_module.setup_logger

    def patched_setup_logger(name, level=logging.INFO, log_to_file=True, log_to_console=True):
        # Always disable file logging in tests
        return original_setup_logger(
            name=name,
            level=level,
            log_to_file=False,
            log_to_console=log_to_console,
        )

    # Patch at the source module
    monkeypatch.setattr(logger_module, "setup_logger", patched_setup_logger)

    # Patch where it's imported in other modules
    try:
        from core import rplay as rplay_module
        monkeypatch.setattr(rplay_module, "setup_logger", patched_setup_logger)
    except (ImportError, AttributeError):
        pass

    try:
        from core import downloader as downloader_module
        monkeypatch.setattr(downloader_module, "setup_logger", patched_setup_logger)
    except (ImportError, AttributeError):
        pass

    try:
        from core import live_stream_monitor as monitor_module
        monkeypatch.setattr(monitor_module, "setup_logger", patched_setup_logger)
    except (ImportError, AttributeError):
        pass

    # For config module, we need to create a patched _get_logger
    try:
        from core import config as config_module

        def patched_get_logger():
            # Always return a console-only logger
            logger = logging.getLogger("Config")
            if not logger.handlers:
                handler = logging.StreamHandler()
                handler.setFormatter(logging.Formatter(
                    fmt="%(asctime)s | %(name)-15s | %(levelname)-8s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                ))
                logger.addHandler(handler)
                logger.setLevel(logging.INFO)
            return logger

        monkeypatch.setattr(config_module, "_get_logger", patched_get_logger)
    except (ImportError, AttributeError):
        pass
