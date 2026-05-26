"""BlueSky Logging and Debugging Manager

This module provides comprehensive logging and debugging capabilities for BlueSky
simulations, including:
- Console-to-file logging with timestamps
- Debug mode for detailed event logging
- Health check heartbeats
- Error aggregation and tracking
"""

import os
import sys
import time
import datetime
import psutil
import threading
from pathlib import Path
from collections import deque
from typing import Optional, Dict, List
import io


class LoggingManager:
    """Manages all logging and debugging for BlueSky"""

    # Simulation states
    STATE_INIT = "INIT"
    STATE_READY = "READY"
    STATE_RUNNING = "RUNNING"
    STATE_PAUSED = "PAUSED"
    STATE_STOPPED = "STOPPED"

    # Log levels
    LEVEL_DEBUG = "DEBUG"
    LEVEL_INFO = "INFO"
    LEVEL_WARNING = "WARNING"
    LEVEL_ERROR = "ERROR"
    LEVEL_HEARTBEAT = "HEARTBEAT"

    # Configuration
    MAX_LOG_SIZE = 100 * 1024 * 1024  # 100MB
    HEARTBEAT_INTERVAL = 30  # seconds
    ERROR_BUFFER_SIZE = 10

    def __init__(self, debug: bool = False, log_dir: str = "/tmp"):
        """
        Initialize the logging manager.

        Args:
            debug: Enable debug mode with detailed logging
            log_dir: Directory for log files (default: /tmp)
        """
        self.debug = debug
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Generate log file path based on process ID
        self.pid = os.getpid()
        self.log_file = self.log_dir / f"bluesky_session_{self.pid}.log"

        # State tracking
        self.current_state = self.STATE_INIT
        self.process = psutil.Process(self.pid)

        # Error tracking
        self.error_buffer = deque(maxlen=self.ERROR_BUFFER_SIZE)
        self.error_counts: Dict[str, int] = {}

        # Heartbeat thread
        self.heartbeat_thread: Optional[threading.Thread] = None
        self.should_run = True

        # Original stdout/stderr for fallback
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr

        # Statistics
        self.stats = {
            'scenarios_active': 0,
            'aircraft_count': 0,
            'memory_mb': 0,
            'cpu_percent': 0,
        }

        # Initialize logging
        self._initialize_logging()

    def _initialize_logging(self):
        """Initialize file logging and stdout/stderr redirection"""
        # Open log file
        try:
            self.log_handle = open(self.log_file, 'w', buffering=1)
        except Exception as e:
            print(f"Warning: Could not open log file {self.log_file}: {e}")
            self.log_handle = None

        # Write initialization message
        self._write_log(self.LEVEL_INFO, "BlueSky Logging Manager initialized",
                       f"Debug mode: {self.debug}, Log file: {self.log_file}")

        # Start heartbeat thread only when explicitly requested (debug mode or
        # BLUESKY_HEARTBEAT=1). The heartbeat writes to stdout every
        # HEARTBEAT_INTERVAL seconds, which defeats stdout-staleness watchdogs
        # in downstream wrappers (e.g. BluePlan's runner.py
        # LOG_STALENESS_THRESHOLD fast-path that relies on stdout going silent
        # after "BlueSky normal end." to clean up the script-pty-wrapped
        # server-gui process tree). With heartbeats always-on the wrapper
        # never sees staleness, never fires the normal_end_detected fast-path,
        # and the run appears to hang for the full WALL_TIMEOUT_GUI (3600s)
        # before the watchdog kills it.
        # See docs/handoffs/canary-sim-time-bisect-2026-05-26.md.
        heartbeat_enabled = (
            self.debug or os.getenv('BLUESKY_HEARTBEAT', '0') == '1'
        )
        if heartbeat_enabled:
            self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            self.heartbeat_thread.start()
        else:
            self.heartbeat_thread = None

    def _write_log(self, level: str, message: str, extra_info: str = ""):
        """
        Write a log entry with timestamp and state.

        Args:
            level: Log level (DEBUG, INFO, WARNING, ERROR, HEARTBEAT)
            message: Main log message
            extra_info: Additional information to append
        """
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        # Format: [TIMESTAMP] [STATE] [LEVEL] [MESSAGE]
        if level == self.LEVEL_HEARTBEAT:
            log_line = f"[{timestamp}] [{level}] {message}"
        else:
            log_line = f"[{timestamp}] [{self.current_state}] [{level}] {message}"

        if extra_info:
            log_line += f" | {extra_info}"

        # Write to file
        if self.log_handle and not self.log_handle.closed:
            try:
                self.log_handle.write(log_line + "\n")
                self.log_handle.flush()
            except Exception as e:
                self.original_stderr.write(f"Error writing to log: {e}\n")

        # Also write to stderr for visibility
        self.original_stderr.write(log_line + "\n")
        self.original_stderr.flush()

        # Check for log rotation
        self._check_log_rotation()

    def _check_log_rotation(self):
        """Check if log file needs rotation and rotate if necessary"""
        if not self.log_handle or self.log_handle.closed:
            return

        try:
            log_size = os.path.getsize(self.log_file)
            if log_size > self.MAX_LOG_SIZE:
                self._rotate_log()
        except Exception as e:
            self.original_stderr.write(f"Error checking log rotation: {e}\n")

    def _rotate_log(self):
        """Rotate log file when it exceeds max size"""
        try:
            # Close current log
            if self.log_handle and not self.log_handle.closed:
                self.log_handle.close()

            # Rename current log with timestamp
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            rotated_path = self.log_dir / f"bluesky_session_{self.pid}_{timestamp}.log"
            os.rename(self.log_file, rotated_path)

            # Open new log file
            self.log_handle = open(self.log_file, 'w', buffering=1)
            self._write_log(self.LEVEL_INFO, "Log rotated", f"Previous: {rotated_path.name}")
        except Exception as e:
            self.original_stderr.write(f"Error rotating log: {e}\n")

    def set_state(self, state: str):
        """
        Update current simulation state.

        Args:
            state: New state (INIT, READY, RUNNING, PAUSED, STOPPED)
        """
        if self.current_state != state:
            prev_state = self.current_state
            self.current_state = state
            self._write_log(self.LEVEL_INFO, "State transition",
                           f"{prev_state} -> {state}")

    def log_simulation_tick(self, sim_time: float, wall_time: float = None):
        """Log a simulation time update (only in debug mode)"""
        if self.debug:
            extra = ""
            if wall_time is not None:
                extra = f"| Wall time: {wall_time:.3f}s"
            self._write_log(self.LEVEL_DEBUG, f"Simulation tick: {sim_time:.3f}s", extra)

    def log_aircraft_update(self, aircraft_id: str, position: tuple,
                          altitude: float = None, speed: float = None):
        """
        Log aircraft position update (only in debug mode).

        Args:
            aircraft_id: Aircraft identifier
            position: (lat, lon) tuple
            altitude: Aircraft altitude in feet
            speed: Aircraft speed in knots
        """
        if self.debug:
            lat, lon = position
            extra = f"Pos: ({lat:.2f}N, {lon:.2f}W)"
            if altitude is not None:
                extra += f" | Alt: {altitude:.0f}ft"
            if speed is not None:
                extra += f" | Spd: {speed:.0f}kt"
            self._write_log(self.LEVEL_DEBUG, f"Aircraft {aircraft_id} update", extra)

    def log_plugin_invocation(self, plugin_name: str, function_name: str, params: dict = None):
        """
        Log plugin invocation (only in debug mode).

        Args:
            plugin_name: Name of the plugin
            function_name: Function being called
            params: Parameters passed to the function
        """
        if self.debug:
            extra = f"Func: {function_name}"
            if params:
                param_str = ", ".join(f"{k}={v}" for k, v in list(params.items())[:3])
                extra += f" | Params: {param_str}"
            self._write_log(self.LEVEL_DEBUG, f"Plugin: {plugin_name}", extra)

    def log_command_received(self, command: str, status: str = "PROCESSING"):
        """
        Log a command received and its processing status.

        Args:
            command: The command text
            status: Processing status (PROCESSING, SUCCESS, ERROR)
        """
        if self.debug:
            self._write_log(self.LEVEL_DEBUG, f"Command: {command}", f"Status: {status}")

    def log_error(self, error_type: str, message: str, stack_trace: str = None):
        """
        Log an error and track it in the error buffer.

        Args:
            error_type: Type of error (timeout, connection, validation, etc.)
            message: Error message
            stack_trace: Optional stack trace
        """
        # Update error counts
        self.error_counts[error_type] = self.error_counts.get(error_type, 0) + 1
        count = self.error_counts[error_type]

        # Add to error buffer
        error_info = {
            'timestamp': datetime.datetime.now(),
            'type': error_type,
            'message': message,
            'count': count,
        }
        self.error_buffer.append(error_info)

        # Log the error
        extra = f"Type: {error_type} | Count: {count}"
        self._write_log(self.LEVEL_ERROR, message, extra)

        # Log stack trace if provided
        if stack_trace and self.debug:
            for line in stack_trace.split('\n'):
                if line.strip():
                    self._write_log(self.LEVEL_DEBUG, f"Traceback: {line}")

    def update_stats(self, scenarios_active: int = None, aircraft_count: int = None):
        """
        Update simulation statistics.

        Args:
            scenarios_active: Number of active scenarios
            aircraft_count: Number of aircraft in simulation
        """
        if scenarios_active is not None:
            self.stats['scenarios_active'] = scenarios_active
        if aircraft_count is not None:
            self.stats['aircraft_count'] = aircraft_count

    def _get_system_stats(self):
        """Get current system statistics"""
        try:
            memory_mb = self.process.memory_info().rss / (1024 * 1024)
            cpu_percent = self.process.cpu_percent(interval=0.1)
            self.stats['memory_mb'] = memory_mb
            self.stats['cpu_percent'] = cpu_percent
        except Exception as e:
            self.original_stderr.write(f"Error getting system stats: {e}\n")

    def _heartbeat_loop(self):
        """Main loop for heartbeat logging"""
        while self.should_run:
            try:
                time.sleep(self.HEARTBEAT_INTERVAL)

                if not self.should_run:
                    break

                # Update system stats
                self._get_system_stats()

                # Format heartbeat message
                scenarios = self.stats.get('scenarios_active', 0)
                aircraft = self.stats.get('aircraft_count', 0)
                memory = self.stats.get('memory_mb', 0)
                cpu = self.stats.get('cpu_percent', 0)

                message = (f"Scenario: {scenarios} | Aircraft: {aircraft} | "
                          f"Memory: {memory:.1f}MB | CPU: {cpu:.1f}%")

                self._write_log(self.LEVEL_HEARTBEAT, message)

            except Exception as e:
                self.original_stderr.write(f"Heartbeat thread error: {e}\n")

    def shutdown(self):
        """Cleanly shut down the logging manager"""
        self.should_run = False

        # Wait for heartbeat thread
        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            self.heartbeat_thread.join(timeout=5)

        # Write final message
        self._write_log(self.LEVEL_INFO, "BlueSky logging manager shutdown")

        # Close log file
        if self.log_handle and not self.log_handle.closed:
            try:
                self.log_handle.close()
            except Exception as e:
                self.original_stderr.write(f"Error closing log file: {e}\n")


# Global instance
_logging_manager: Optional[LoggingManager] = None


def init_logging(debug: bool = False, log_dir: str = "/tmp") -> LoggingManager:
    """
    Initialize the global logging manager.

    Args:
        debug: Enable debug mode
        log_dir: Directory for log files

    Returns:
        LoggingManager instance
    """
    global _logging_manager
    if _logging_manager is None:
        _logging_manager = LoggingManager(debug=debug, log_dir=log_dir)
    return _logging_manager


def get_logger() -> Optional[LoggingManager]:
    """Get the global logging manager instance"""
    return _logging_manager


def shutdown_logging():
    """Shut down the global logging manager"""
    global _logging_manager
    if _logging_manager is not None:
        _logging_manager.shutdown()
        _logging_manager = None
