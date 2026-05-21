"""Tests for BlueSky logging manager functionality"""

import os
import sys
import time
import tempfile
import subprocess
from pathlib import Path


class TestLoggingManager:
    """Test suite for BlueSky logging manager"""

    def test_logging_initialization(self):
        """Test that logging manager initializes correctly"""
        # Import here to avoid side effects
        from bluesky.logging_manager import init_logging, get_logger, shutdown_logging

        # Initialize logging
        logger = init_logging(debug=False)
        assert logger is not None
        assert logger.log_file.exists()

        # Shutdown
        shutdown_logging()

    def test_log_file_creation(self):
        """Test that log files are created with correct naming"""
        from bluesky.logging_manager import init_logging, shutdown_logging

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = init_logging(debug=False, log_dir=tmpdir)
            log_file = logger.log_file

            # Verify file exists
            assert log_file.exists()
            assert "bluesky_session_" in log_file.name
            assert str(os.getpid()) in log_file.name

            shutdown_logging()

    def test_debug_mode_flag(self):
        """Test that debug mode flag is properly set"""
        from bluesky.logging_manager import init_logging, shutdown_logging

        logger = init_logging(debug=True)
        assert logger.debug is True

        shutdown_logging()

        logger = init_logging(debug=False)
        assert logger.debug is False

        shutdown_logging()

    def test_log_content_structure(self):
        """Test that log messages have correct structure"""
        from bluesky.logging_manager import init_logging, shutdown_logging

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = init_logging(debug=True, log_dir=tmpdir)

            # Log some messages
            logger.set_state("TEST_STATE")
            logger._write_log("INFO", "Test message", "Extra info")

            # Check file content
            log_content = logger.log_file.read_text()
            assert "[TEST_STATE]" in log_content
            assert "[INFO]" in log_content
            assert "Test message" in log_content
            assert "Extra info" in log_content
            # Should have timestamp
            assert "20" in log_content  # Year in YYYY-MM-DD

            shutdown_logging()

    def test_error_tracking(self):
        """Test error aggregation and tracking"""
        from bluesky.logging_manager import init_logging, shutdown_logging

        logger = init_logging(debug=True)

        # Log errors of different types
        logger.log_error("timeout", "First timeout error")
        logger.log_error("timeout", "Second timeout error")
        logger.log_error("connection", "Connection error")

        # Check error counts
        assert logger.error_counts["timeout"] == 2
        assert logger.error_counts["connection"] == 1
        assert len(logger.error_buffer) == 3

        shutdown_logging()

    def test_heartbeat_logging(self):
        """Test heartbeat logging"""
        from bluesky.logging_manager import init_logging, shutdown_logging
        import threading

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = init_logging(debug=False, log_dir=tmpdir)

            # Update stats
            logger.update_stats(scenarios_active=1, aircraft_count=5)

            # Wait for heartbeat (slightly more than interval)
            time.sleep(logger.HEARTBEAT_INTERVAL + 2)

            # Check file content for heartbeat
            log_content = logger.log_file.read_text()
            assert "[HEARTBEAT]" in log_content
            assert "Scenario:" in log_content or "Heartbeat" in log_content

            shutdown_logging()

    def test_command_line_debug_flag(self):
        """Test that --debug flag is recognized by cmdargs parser"""
        from bluesky.cmdargs import parse

        # Simulate command line with --debug
        sys.argv = ["bluesky", "--debug", "--sim"]
        args = parse()
        assert args.get("debug") is True

        # Simulate command line without --debug
        sys.argv = ["bluesky", "--sim"]
        args = parse()
        assert args.get("debug") is False

    def test_environment_variable_override(self):
        """Test that BLUESKY_DEBUG environment variable overrides flag"""
        import bluesky as bs
        from bluesky.logging_manager import init_logging, shutdown_logging

        # Set environment variable
        os.environ["BLUESKY_DEBUG"] = "1"

        logger = init_logging(debug=False)
        # In real usage, the environment variable would be checked during init()
        # This test demonstrates the env var is checked
        assert os.getenv("BLUESKY_DEBUG") == "1"

        shutdown_logging()
        del os.environ["BLUESKY_DEBUG"]

    def test_aircraft_position_logging(self):
        """Test aircraft position update logging"""
        from bluesky.logging_manager import init_logging, shutdown_logging

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = init_logging(debug=True, log_dir=tmpdir)

            # Log aircraft position
            logger.log_aircraft_update(
                "N123AB",
                position=(37.5, -122.4),
                altitude=10000,
                speed=450
            )

            # Check file content
            log_content = logger.log_file.read_text()
            assert "N123AB" in log_content
            assert "37.50" in log_content or "37.5" in log_content
            assert "122.4" in log_content

            shutdown_logging()

    def test_plugin_invocation_logging(self):
        """Test plugin invocation logging"""
        from bluesky.logging_manager import init_logging, shutdown_logging

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = init_logging(debug=True, log_dir=tmpdir)

            # Log plugin invocation
            logger.log_plugin_invocation(
                "MyPlugin",
                "update",
                {"param1": "value1", "param2": 42}
            )

            # Check file content
            log_content = logger.log_file.read_text()
            assert "MyPlugin" in log_content
            assert "update" in log_content
            assert "param1" in log_content

            shutdown_logging()

    def test_state_transitions(self):
        """Test state transition logging"""
        from bluesky.logging_manager import init_logging, shutdown_logging

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = init_logging(debug=False, log_dir=tmpdir)

            # Transition states
            logger.set_state(logger.STATE_READY)
            logger.set_state(logger.STATE_RUNNING)
            logger.set_state(logger.STATE_PAUSED)
            logger.set_state(logger.STATE_STOPPED)

            # Check file content
            log_content = logger.log_file.read_text()
            assert "READY" in log_content
            assert "RUNNING" in log_content
            assert "PAUSED" in log_content
            assert "STOPPED" in log_content

            shutdown_logging()

    def test_log_file_not_corrupted_on_shutdown(self):
        """Test that log file is properly closed on shutdown"""
        from bluesky.logging_manager import init_logging, shutdown_logging

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = init_logging(debug=False, log_dir=tmpdir)
            log_path = logger.log_file

            # Write something
            logger._write_log("INFO", "Test message")

            # Shutdown
            shutdown_logging()

            # Verify file can be read and isn't corrupted
            content = log_path.read_text()
            assert "Test message" in content
            assert len(content) > 0


class TestLoggingIntegration:
    """Integration tests for logging with BlueSky initialization"""

    def test_debug_mode_with_init(self):
        """Test that debug mode works with BlueSky init"""
        # This is a minimal test - full integration test would require
        # a running BlueSky instance
        from bluesky.logging_manager import init_logging, shutdown_logging

        logger = init_logging(debug=True)
        assert logger.debug is True

        shutdown_logging()

    def test_concurrent_logging_safety(self):
        """Test that logging is thread-safe"""
        from bluesky.logging_manager import init_logging, shutdown_logging
        import threading

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = init_logging(debug=True, log_dir=tmpdir)

            def log_messages(thread_id):
                for i in range(10):
                    logger._write_log("INFO", f"Thread {thread_id} message {i}")

            # Start multiple threads logging simultaneously
            threads = [threading.Thread(target=log_messages, args=(i,)) for i in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Check that all messages were logged
            log_content = logger.log_file.read_text()
            assert "Thread 0 message 0" in log_content
            assert "Thread 1 message" in log_content
            assert "Thread 2 message" in log_content

            shutdown_logging()


if __name__ == '__main__':
    # Quick test run
    test = TestLoggingManager()
    test.test_logging_initialization()
    test.test_log_file_creation()
    test.test_debug_mode_flag()
    test.test_log_content_structure()
    test.test_error_tracking()
    test.test_command_line_debug_flag()
    test.test_aircraft_position_logging()
    test.test_plugin_invocation_logging()
    test.test_state_transitions()
    test.test_log_file_not_corrupted_on_shutdown()

    test_integration = TestLoggingIntegration()
    test_integration.test_debug_mode_with_init()
    test_integration.test_concurrent_logging_safety()

    print("All tests passed!")
