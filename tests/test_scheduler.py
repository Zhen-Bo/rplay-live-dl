"""Tests for scheduler module."""

import logging
import signal
from unittest.mock import MagicMock, patch

import pytest

from core.scheduler import LiveStreamScheduler, run_scheduler, _signal_handler
from models.env import EnvConfig


@pytest.fixture
def mock_env():
    """Create mock EnvConfig."""
    return EnvConfig(auth_token="test_token", user_oid="test_oid", interval=60)


@pytest.fixture
def mock_logger():
    """Create mock logger."""
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def patched_scheduler_deps():
    """Patch LiveStreamMonitor and BlockingScheduler for scheduler tests."""
    with patch('core.scheduler.LiveStreamMonitor') as mock_monitor_class, \
         patch('core.scheduler.BlockingScheduler') as mock_scheduler_class:
        yield mock_scheduler_class, mock_monitor_class


class TestLiveStreamSchedulerInit:
    """Tests for LiveStreamScheduler initialization."""

    def test_init_stores_env(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test that env config is stored correctly."""
        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="1.0.0")
        assert scheduler.env is mock_env

    def test_init_stores_logger(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test that logger is stored correctly."""
        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="1.0.0")
        assert scheduler.logger is mock_logger

    def test_init_stores_version(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test that version is stored correctly."""
        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="2.0.0")
        assert scheduler.version == "2.0.0"

    def test_init_creates_monitor(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test that LiveStreamMonitor is created."""
        mock_scheduler_class, mock_monitor_class = patched_scheduler_deps
        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="1.0.0")
        mock_monitor_class.assert_called_once_with("test_token", "test_oid")
        assert scheduler.monitor is mock_monitor_class.return_value

    def test_init_creates_scheduler(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test that BlockingScheduler is created."""
        mock_scheduler_class, mock_monitor_class = patched_scheduler_deps
        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="1.0.0")
        mock_scheduler_class.assert_called_once()
        assert scheduler.scheduler is mock_scheduler_class.return_value

    def test_init_default_version(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test default version is 'unknown'."""
        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger)
        assert scheduler.version == "unknown"


class TestCheckAndDownload:
    """Tests for check_and_download method."""

    def test_calls_monitor_check(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test that check_and_download calls monitor's check method."""
        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="1.0.0")
        scheduler.check_and_download()
        scheduler.monitor.check_live_streams_and_start_download.assert_called_once()

    def test_handles_exception_gracefully(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test that exceptions are logged but don't crash."""
        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="1.0.0")
        scheduler.monitor.check_live_streams_and_start_download.side_effect = RuntimeError("Test error")

        # Should not raise
        scheduler.check_and_download()
        mock_logger.error.assert_called_once()
        assert "Test error" in str(mock_logger.error.call_args)


class TestStartScheduler:
    """Tests for start method."""

    def test_start_logs_banner(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test that start logs version banner."""
        mock_scheduler_class, mock_monitor_class = patched_scheduler_deps
        mock_scheduler = MagicMock()
        mock_scheduler_class.return_value = mock_scheduler
        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="1.2.3")

        # Stop scheduler immediately to avoid blocking
        mock_scheduler.start.side_effect = KeyboardInterrupt()

        scheduler.start()

        # Check version is logged
        log_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("1.2.3" in call for call in log_calls)

    def test_start_adds_job(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test that start adds scheduled job."""
        mock_scheduler_class, mock_monitor_class = patched_scheduler_deps
        mock_scheduler = MagicMock()
        mock_scheduler_class.return_value = mock_scheduler
        mock_scheduler.start.side_effect = KeyboardInterrupt()

        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="1.0.0")
        scheduler.start()

        mock_scheduler.add_job.assert_called_once()

    def test_start_performs_initial_check(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test that start performs initial check."""
        mock_scheduler_class, mock_monitor_class = patched_scheduler_deps
        mock_scheduler = MagicMock()
        mock_scheduler_class.return_value = mock_scheduler
        mock_scheduler.start.side_effect = KeyboardInterrupt()

        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="1.0.0")
        scheduler.start()

        # Monitor's check should be called for initial check
        scheduler.monitor.check_live_streams_and_start_download.assert_called()

    def test_start_logs_interval(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test that start logs check interval."""
        mock_scheduler_class, mock_monitor_class = patched_scheduler_deps
        mock_scheduler = MagicMock()
        mock_scheduler_class.return_value = mock_scheduler
        mock_scheduler.start.side_effect = KeyboardInterrupt()

        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="1.0.0")
        scheduler.start()

        log_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("60 seconds" in call for call in log_calls)

    def test_start_handles_keyboard_interrupt(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test that KeyboardInterrupt is handled gracefully."""
        mock_scheduler_class, mock_monitor_class = patched_scheduler_deps
        mock_scheduler = MagicMock()
        mock_scheduler_class.return_value = mock_scheduler
        mock_scheduler.start.side_effect = KeyboardInterrupt()

        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="1.0.0")

        # Should not raise
        scheduler.start()

        log_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("manually stopped" in call for call in log_calls)

    def test_start_propagates_other_exceptions(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test that other exceptions are propagated."""
        mock_scheduler_class, mock_monitor_class = patched_scheduler_deps
        mock_scheduler = MagicMock()
        mock_scheduler_class.return_value = mock_scheduler
        mock_scheduler.start.side_effect = RuntimeError("System error")

        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="1.0.0")

        with pytest.raises(RuntimeError, match="System error"):
            scheduler.start()


class TestStopScheduler:
    """Tests for stop method."""

    def test_stop_when_running(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test stop shuts down scheduler when running."""
        mock_scheduler_class, mock_monitor_class = patched_scheduler_deps
        mock_scheduler = MagicMock()
        mock_scheduler.running = True
        mock_scheduler_class.return_value = mock_scheduler

        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="1.0.0")
        scheduler.stop()

        mock_scheduler.shutdown.assert_called_once_with(wait=False)

    def test_stop_when_not_running(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test stop does nothing when not running."""
        mock_scheduler_class, mock_monitor_class = patched_scheduler_deps
        mock_scheduler = MagicMock()
        mock_scheduler.running = False
        mock_scheduler_class.return_value = mock_scheduler

        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="1.0.0")
        scheduler.stop()

        mock_scheduler.shutdown.assert_not_called()

    def test_stop_logs_message(self, patched_scheduler_deps, mock_env, mock_logger):
        """Test stop logs shutdown message."""
        mock_scheduler_class, mock_monitor_class = patched_scheduler_deps
        mock_scheduler = MagicMock()
        mock_scheduler.running = True
        mock_scheduler_class.return_value = mock_scheduler

        scheduler = LiveStreamScheduler(env=mock_env, logger=mock_logger, version="1.0.0")
        scheduler.stop()

        mock_logger.info.assert_called_with("Scheduler stopped")


class TestSignalHandler:
    """Tests for _signal_handler function."""

    @patch('core.scheduler.sys.exit')
    def test_signal_handler_no_scheduler(self, mock_exit):
        """Test signal handler when no scheduler exists."""
        import core.scheduler as scheduler_module
        original = scheduler_module._scheduler
        scheduler_module._scheduler = None
        try:
            _signal_handler(signal.SIGINT, None)
            mock_exit.assert_called_once_with(0)
        finally:
            scheduler_module._scheduler = original

    @patch('core.scheduler.sys.exit')
    def test_signal_handler_with_scheduler(self, mock_exit, patched_scheduler_deps, mock_env, mock_logger):
        """Test signal handler calls stop on scheduler."""
        import core.scheduler as scheduler_module

        mock_scheduler_instance = LiveStreamScheduler(
            env=mock_env, logger=mock_logger, version="1.0.0"
        )
        mock_scheduler_instance.stop = MagicMock()

        original = scheduler_module._scheduler
        scheduler_module._scheduler = mock_scheduler_instance

        try:
            _signal_handler(signal.SIGTERM, None)
            mock_scheduler_instance.stop.assert_called_once()
            mock_exit.assert_called_once_with(0)
        finally:
            scheduler_module._scheduler = original

    @patch('core.scheduler.sys.exit')
    def test_signal_handler_logs_signal_name(self, mock_exit, patched_scheduler_deps, mock_env, mock_logger):
        """Test signal handler logs the signal name."""
        import core.scheduler as scheduler_module

        mock_scheduler_instance = LiveStreamScheduler(
            env=mock_env, logger=mock_logger, version="1.0.0"
        )
        mock_scheduler_instance.stop = MagicMock()

        original = scheduler_module._scheduler
        scheduler_module._scheduler = mock_scheduler_instance

        try:
            _signal_handler(signal.SIGINT, None)
            log_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("SIGINT" in call for call in log_calls)
        finally:
            scheduler_module._scheduler = original


@pytest.fixture
def patched_run_scheduler_deps():
    """Patch signal.signal and LiveStreamScheduler for run_scheduler tests."""
    with patch('core.scheduler.signal.signal') as mock_signal, \
         patch('core.scheduler.LiveStreamScheduler') as mock_scheduler_class:
        yield mock_scheduler_class, mock_signal


class TestRunScheduler:
    """Tests for run_scheduler function."""

    def test_sets_signal_handlers(self, patched_run_scheduler_deps, mock_env, mock_logger):
        """Test that SIGINT and SIGTERM handlers are set."""
        mock_scheduler_class, mock_signal = patched_run_scheduler_deps
        mock_instance = MagicMock()
        mock_scheduler_class.return_value = mock_instance

        run_scheduler(env=mock_env, logger=mock_logger, version="1.0.0")

        # Verify both signal handlers are set
        signal_calls = [call[0][0] for call in mock_signal.call_args_list]
        assert signal.SIGINT in signal_calls
        assert signal.SIGTERM in signal_calls

    def test_creates_scheduler(self, patched_run_scheduler_deps, mock_env, mock_logger):
        """Test that LiveStreamScheduler is created with correct args."""
        mock_scheduler_class, mock_signal = patched_run_scheduler_deps
        mock_instance = MagicMock()
        mock_scheduler_class.return_value = mock_instance

        run_scheduler(env=mock_env, logger=mock_logger, version="2.0.0")

        mock_scheduler_class.assert_called_once_with(
            env=mock_env, logger=mock_logger, version="2.0.0"
        )

    def test_starts_scheduler(self, patched_run_scheduler_deps, mock_env, mock_logger):
        """Test that scheduler.start() is called."""
        mock_scheduler_class, mock_signal = patched_run_scheduler_deps
        mock_instance = MagicMock()
        mock_scheduler_class.return_value = mock_instance

        run_scheduler(env=mock_env, logger=mock_logger, version="1.0.0")

        mock_instance.start.assert_called_once()

    def test_sets_global_reference(self, patched_run_scheduler_deps, mock_env, mock_logger):
        """Test that global _scheduler is set."""
        import core.scheduler as scheduler_module

        mock_scheduler_class, mock_signal = patched_run_scheduler_deps
        mock_instance = MagicMock()
        mock_scheduler_class.return_value = mock_instance

        original = scheduler_module._scheduler
        try:
            run_scheduler(env=mock_env, logger=mock_logger, version="1.0.0")
            assert scheduler_module._scheduler is mock_instance
        finally:
            scheduler_module._scheduler = original
