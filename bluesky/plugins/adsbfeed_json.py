"""BlueSky plugin: JSON ADS-B data feed.

Receives pre-decoded ADS-B position data as newline-delimited JSON over TCP.
This is an alternative to BlueSky's built-in adsbfeed.py (Mode-S Beast) that
accepts clean, parsed data — no raw protocol decoding needed.

Protocol:
    Connect via TCP to the configured host:port.
    Each line is a JSON object with aircraft state:

    {"icao": "A1B2C3", "callsign": "UAL123", "lat": 29.18, "lon": -81.06,
     "alt": 35000, "spd": 450, "hdg": 270, "type": "B738"}

    Required fields: icao, lat, lon, alt, spd, hdg
    Optional fields: callsign (defaults to icao), type (defaults to B738)

    Send {"cmd": "quit"} to stop the feed cleanly.

Configuration (settings.cfg):
    adsb_json_host = "localhost"
    adsb_json_port = 10901

Mixed mode:
    All ADS-B aircraft receive a "T" callsign prefix (e.g., UAL123 -> TUAL123)
    to distinguish them from user-defined scenario aircraft. NORESO is applied
    to each ADS-B aircraft so conflict resolution doesn't interfere with their
    trajectories, while conflict detection still works between user and ADS-B
    aircraft.

Area filtering:
    Plugin-level geographic filtering uses BlueSky's native areafilter system.
    When an area is configured, only aircraft inside the area are injected into
    the simulation. Aircraft that leave the area are automatically removed.

    Configure via scenario commands:
        ADSBFEEDJSON AREA lat,lon,radius_nm     (circle filter)
        ADSBFEEDJSON AREA OFF                   (disable filter)

    Or the plugin auto-detects BlueSky's EXP area if one is defined.

Multi-area filter (MMM2):
    For scenarios with multiple concurrent focus areas (e.g. two static
    departures plus a dynamic follow-the-user zone), pass a JSON blob:

        ADSBFEEDJSON AREAS [{"type":"static","name":"A","radius_nm":50,
                             "center_lat":29.18,"center_lon":-81.06},
                            {"type":"dynamic","name":"B","radius_nm":30,
                             "follow_callsign":"UAL1","update_interval_s":30}]

    Each area has: type ("static"|"dynamic"), name, radius_nm, center_lat,
    center_lon, follow_callsign (dynamic only), update_interval_s (dynamic).
    An aircraft is kept if it falls within ANY area. Dynamic areas recenter
    themselves from ``traf`` at ``update_interval_s`` cadence. Pass
    ``ADSBFEEDJSON AREAS OFF`` (or ``[]``) to clear multi-area mode and
    restore legacy single-area behavior.

Dynamic follow-the-user filter (Level 3, 2026-04-16):
    The plugin can follow a user aircraft and dynamically re-center the
    bridge's area filter every 5 seconds so live traffic stays relevant
    to the user's current position. Configured via:

        ADSBFEEDJSON TRACK <callsign>       (start following; radius default 50 nm)
        ADSBFEEDJSON TRACK OFF              (stop following)
        ADSBFEEDJSON RADIUS <nm>            (change follow radius)

    While tracking, the plugin reads the tracked aircraft's lat/lon from
    BlueSky's traffic module every 5 s and POSTs a new area filter to the
    bridge at ``http://<host>:8400/api/bridge/area`` so the cache-side
    filter moves with the user aircraft. If the tracked aircraft is not
    yet in traf (e.g. before its CRE has run), the update is silently
    skipped and retried on the next tick. If the aircraft is deleted
    mid-scenario, tracking stops until a new TRACK is issued.

    Design reference:
        docs/findings/adsb-stream-architecture-and-filtering-2026-04-16.md

Usage in BlueSky scenario files:
    00:00:00>ADSBFEEDJSON PORT 10901
    00:00:00>ADSBFEEDJSON AREA 29.18,-81.06,50
    00:00:01>ADSBFEEDJSON ON

Usage interactively:
    ADSBFEEDJSON ON
    ADSBFEEDJSON OFF
    ADSBFEEDJSON PORT 10902
    ADSBFEEDJSON AREA 29.18,-81.06,50
    ADSBFEEDJSON AREA OFF
    ADSBFEEDJSON TRACK POC001
    ADSBFEEDJSON RADIUS 75
    ADSBFEEDJSON TRACK OFF
"""

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import math

from bluesky import settings, stack, traf
from bluesky.tools import aero, areafilter
from bluesky.tools.aero import ft, kts, nm

# Default settings
settings.set_variable_defaults(adsb_json_host="localhost", adsb_json_port=10901)

# ADS-B callsign prefix — distinguishes live feed aircraft from user scenario aircraft
ADSB_CALLSIGN_PREFIX = "T"

# UG-01 / UG-02 — module-level decode-rejection counters. Protected by a
# lock so the receiver thread and the BlueSky-sim thread can both increment
# safely. Logging is verbatim for the first 3 of each class so operators
# see the exact failure; after that, one-in-N rate limiting prevents log
# spam. A periodic summary is emitted every SUMMARY_INTERVAL_SEC wall-clock
# seconds (driven from update()), and a final summary on shutdown.
_decode_lock = threading.Lock()
_decode_counters = {
    "accepted_records": 0,
    "json_parse_failures": 0,
    "missing_required_field_drops": 0,
    # Per-field drop breakdown for missing-required-field (UG-02).
    "missing_field_icao": 0,
    "missing_field_lat": 0,
    "missing_field_lon": 0,
}
# How many of each class to log verbatim before rate limiting kicks in.
_VERBOSE_LOG_FIRST_N = 3
# After verbose limit, log every Nth occurrence.
_RATE_LIMIT_SAMPLE_EVERY = 100
# Wall-clock interval between periodic summaries from update().
_SUMMARY_INTERVAL_SEC = 60.0


def _increment_counter(name: str, n: int = 1) -> int:
    """Thread-safe counter increment. Returns the post-increment value."""
    with _decode_lock:
        new = _decode_counters.get(name, 0) + n
        _decode_counters[name] = new
        return new


def _get_counter_snapshot() -> dict:
    """Thread-safe snapshot of all counters."""
    with _decode_lock:
        return dict(_decode_counters)


def _log_counter_summary(tag: str = "periodic") -> None:
    """Emit a one-line summary of decode counters. Safe to call any time."""
    snap = _get_counter_snapshot()
    print(
        "[ADSBFEEDJSON] decode_summary (%s): accepted=%d json_parse_failures=%d "
        "missing_required_field_drops=%d (icao=%d lat=%d lon=%d)" % (
            tag,
            snap.get("accepted_records", 0),
            snap.get("json_parse_failures", 0),
            snap.get("missing_required_field_drops", 0),
            snap.get("missing_field_icao", 0),
            snap.get("missing_field_lat", 0),
            snap.get("missing_field_lon", 0),
        )
    )

# Connection retry settings
RETRY_INTERVAL_SEC = 5.0    # Seconds between connection retry attempts
MAX_RETRY_INTERVAL = 30.0   # Maximum backoff interval

# Dynamic-follow (TRACK) defaults — Level 3 per docs/findings/adsb-stream-
# architecture-and-filtering-2026-04-16.md. The plugin's update() is called
# every 2 s by BlueSky; we throttle area pushes by wall-clock so the bridge
# isn't spammed if the tick rate ever changes.
TRACK_UPDATE_INTERVAL_SEC = 5.0   # Seconds between bridge AREA re-issues
TRACK_DEFAULT_RADIUS_NM = 50.0    # Default follow radius (nm)
TRACK_BRIDGE_API_PORT = 8400      # ADSB Analytics HTTP port (bridge is on 10901,
                                   # but the area-update REST API is on 8400).

# Global state
reader = None


def _safe_float(val, default=0.0):
    """Safely convert a value to float, returning default on failure.

    Handles None, empty strings, and non-numeric strings like "N/A".
    """
    try:
        return float(val) if val else default
    except (ValueError, TypeError):
        return default


def init_plugin():
    global reader
    reader = JsonFeedReader()

    # WWW8 verbosity: print a registration banner so operators see the
    # plugin reached init_plugin(), not just "Successfully loaded plugin"
    # from BlueSky's loader. Includes default host:port so mis-configs
    # surface before the first ADSBFEEDJSON ON is issued.
    print("[ADSBFEEDJSON] Plugin registered, default %s:%s, stale_timeout=%ss, update_interval=2.0s" % (
        getattr(settings, "adsb_json_host", "localhost"),
        getattr(settings, "adsb_json_port", 10901),
        reader.stale_timeout))

    config = {
        "plugin_name": "ADSBFEEDJSON",
        "plugin_type": "sim",
        "update_interval": 2.0,  # Run every 2s (data arrives at ~5s intervals)
        "update": reader.update,
    }

    stackfunctions = {
        "ADSBFEEDJSON": [
            "ADSBFEEDJSON ON/OFF/PORT port/AREA lat,lon,radius/AREAS json/TRACK callsign/RADIUS nm",
            "[txt,txt,txt,txt]",
            reader.command,
            "Connect to a JSON ADS-B data feed (from BluePlan relay)",
        ]
    }

    return config, stackfunctions


class JsonFeedReader:
    def __init__(self):
        self.sock = None
        self._connected = threading.Event()  # Thread-safe connection state
        self._stop_receiver = threading.Event()  # Signal receiver thread to stop
        self._want_connected = False   # True when user said ON, enables auto-retry
        self._lock = threading.Lock()  # Protects self._buffer
        self._buffer = ""
        self.acpool = {}  # icao -> {callsign, lat, lon, alt, spd, hdg, type, ts}
        self.default_type = "B738"
        self.stale_timeout = 120  # seconds before removing unseen aircraft
        self._recv_thread = None
        self._perf_applied = set()  # icaos that have had performance overrides applied
        self._last_retry = 0.0
        self._retry_interval = RETRY_INTERVAL_SEC
        self._inject_count = 0      # Total aircraft created (diagnostic)
        self._update_count = 0      # Total position updates (diagnostic)
        self._msg_count = 0         # Total messages parsed (diagnostic)
        self._skipped_area = 0      # Aircraft skipped due to area filter
        # Area filter state (plugin-level geographic filtering)
        self._area_name = ""        # BlueSky areafilter shape name (empty = no filter)
        self._area_lat = None       # Center lat for circle area
        self._area_lon = None       # Center lon for circle area
        self._area_radius_nm = None # Radius in nm for circle area

        # Multi-area filter state (MMM2). When non-empty, overrides the
        # single-area ``_area_*`` fields: an aircraft is kept if its position
        # is inside ANY of these areas. Each dict has keys:
        #   type, name, radius_nm, center_lat, center_lon,
        #   follow_callsign, update_interval_s, _last_update (internal)
        self._areas: list[dict] = []

        # Dynamic-follow (TRACK) state — Level 3 dynamic area filter
        self.tracked_callsign = None  # Callsign of user aircraft being followed
        self._track_radius_nm = TRACK_DEFAULT_RADIUS_NM
        self._last_track_push = 0.0   # Wall-clock of last bridge area push
        self._track_push_count = 0    # Diagnostic counter
        self._track_skip_count = 0    # Diagnostic: missed ticks (ac not in traf)
        self._track_last_error = None # Most recent push error (for diag)

        # HHH1 Fix C: headless visibility diagnostics.
        # These emit a single STARTUP line on first connect, a WARNING line
        # if no traffic has been received within 60s, and an ERROR line on
        # ConnectionRefusedError — so CLI/headless operators see SOMETHING
        # in the terminal even when zero aircraft flow through the bridge.
        self._connected_at = 0.0           # Wall-clock of first connect
        self._startup_logged = False       # STARTUP banner emitted once
        self._silence_warned = False       # 60s-no-traffic warning fires once
        self._refused_logged = False       # Refused-connection error fires once

        # UG-01 / UG-02: wall-clock of last periodic decode-counter summary
        # (driven from update() so it runs without a background thread).
        self._last_decode_summary = 0.0

    @property
    def is_connected(self):
        """Backward-compatible property for thread-safe connection state."""
        return self._connected.is_set()

    def command(self, arg1=None, arg2=None, arg3=None, arg4=None):
        """Handle ADSBFEEDJSON command with subcommands: ON, OFF, PORT <n>, AREA lat,lon,radius.

        With [txt,txt,txt,txt] argument spec, BlueSky passes up to four words:
            ADSBFEEDJSON ON                    -> arg1="ON"
            ADSBFEEDJSON PORT 10901            -> arg1="PORT", arg2="10901"
            ADSBFEEDJSON AREA 29.18,-81.06,50  -> arg1="AREA", arg2="29.18", arg3="-81.06", arg4="50"
            ADSBFEEDJSON AREA lat,lon,radius   -> arg1="AREA", arg2="lat,lon,radius"
        """
        if arg1 is None or arg1 == "":
            if self._connected.is_set():
                area_info = ""
                if self._area_name:
                    area_info = ", area: %.1fnm around (%.2f,%.2f), %d filtered" % (
                        self._area_radius_nm, self._area_lat, self._area_lon,
                        self._skipped_area)
                return True, "Connected to %s:%s (%d aircraft, %d msgs%s)" % (
                    settings.adsb_json_host,
                    settings.adsb_json_port,
                    len(self.acpool),
                    self._msg_count,
                    area_info,
                )
            elif self._want_connected:
                return True, "Connecting to %s:%s (retrying...)" % (
                    settings.adsb_json_host,
                    settings.adsb_json_port,
                )
            else:
                return True, "Not connected (port=%s)" % settings.adsb_json_port

        arg_str = str(arg1).strip().upper()

        # Handle "PORT <number>"
        if arg_str == "PORT":
            if arg2 is not None:
                try:
                    port = int(arg2)
                    settings.adsb_json_port = port
                    return True, "Relay port set to %d" % port
                except ValueError:
                    return False, "PORT requires a numeric argument"
            return False, "Usage: ADSBFEEDJSON PORT <number>"

        # Handle "AREAS <json_blob>" or "AREAS OFF" — multi-area filter (MMM2).
        if arg_str == "AREAS":
            # Reassemble all trailing args (BlueSky may split on whitespace,
            # and JSON blobs usually have no whitespace anyway).
            parts = [p for p in [arg2, arg3, arg4] if p is not None]
            blob = " ".join(parts).strip() if parts else ""
            if not blob or blob.upper() in ("OFF", "NONE", "CLEAR", "[]"):
                n = len(self._areas)
                self._areas = []
                return True, "Multi-area filter cleared (%d areas removed)" % n
            return self._set_areas(blob)

        # Handle "AREA lat,lon,radius" or "AREA OFF"
        if arg_str == "AREA":
            if arg2 is not None and arg2.strip().upper() == "OFF":
                return self._clear_area()
            # Reconstruct area string — BlueSky may split "lat,lon,radius" into separate args
            area_parts = [p for p in [arg2, arg3, arg4] if p is not None]
            area_str = ",".join(area_parts) if area_parts else ""
            return self._set_area(area_str)

        # Handle "TRACK <callsign>" or "TRACK OFF" — dynamic follow-the-user filter.
        if arg_str == "TRACK":
            if arg2 is None or arg2.strip() == "":
                if self.tracked_callsign:
                    return True, "Tracking %s (radius %.1f nm, %d pushes, %d skips)" % (
                        self.tracked_callsign, self._track_radius_nm,
                        self._track_push_count, self._track_skip_count)
                return True, "Not tracking any aircraft"
            target = arg2.strip().upper()
            if target in ("OFF", "NONE", "CLEAR"):
                prev = self.tracked_callsign
                self.tracked_callsign = None
                self._last_track_push = 0.0
                if prev:
                    return True, "Stopped tracking %s" % prev
                return True, "Not tracking"
            # Reentrant: replaces any previously tracked callsign.
            self.tracked_callsign = target
            self._last_track_push = 0.0  # Push on next tick
            self._track_push_count = 0
            self._track_skip_count = 0
            self._track_last_error = None
            return True, "Tracking %s (radius %.1f nm, update %.0fs)" % (
                target, self._track_radius_nm, TRACK_UPDATE_INTERVAL_SEC)

        # Handle "RADIUS <nm>" — change the follow radius for TRACK.
        if arg_str == "RADIUS":
            if arg2 is None:
                return True, "Current track radius: %.1f nm" % self._track_radius_nm
            try:
                radius = float(arg2)
                if radius <= 0 or radius > 500:
                    return False, "RADIUS must be between 0 and 500 nm"
                self._track_radius_nm = radius
                # Force a re-push on next tick so the new radius takes effect promptly.
                self._last_track_push = 0.0
                return True, "Track radius set to %.1f nm" % radius
            except ValueError:
                return False, "RADIUS requires a numeric argument"

        if arg_str in ("ON", "TRUE", "1"):
            self._want_connected = True
            self._retry_interval = RETRY_INTERVAL_SEC
            self._connect()
            stack.stack("OP")
            if self._connected.is_set():
                return True, "Connected to %s:%s" % (
                    settings.adsb_json_host,
                    settings.adsb_json_port,
                )
            else:
                return True, "Connecting to %s:%s (will retry every %.0fs)" % (
                    settings.adsb_json_host,
                    settings.adsb_json_port,
                    self._retry_interval,
                )
        elif arg_str in ("OFF", "FALSE", "0"):
            self._want_connected = False
            # Dynamic follow halts when the stream is off — no sense pushing
            # area updates to a bridge we aren't consuming.
            self.tracked_callsign = None
            self._last_track_push = 0.0
            self._disconnect()
            return True, "Disconnected"
        else:
            return False, ("Unknown argument: %s. Use ON, OFF, PORT <n>, "
                           "AREA lat,lon,radius, AREAS <json>, "
                           "TRACK <callsign>, or RADIUS <nm>"
                           % arg1)

    def toggle(self, flag=None):
        """Legacy toggle interface — delegates to command()."""
        if flag is None:
            return self.command(None)
        elif flag:
            return self.command("ON")
        else:
            return self.command("OFF")

    def _connect(self):
        """Attempt TCP connection to the bridge. Non-fatal on failure."""
        # Signal any old receiver thread to stop and wait for it
        self._stop_receiver.set()
        if self._recv_thread is not None and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=2.0)
        self._stop_receiver.clear()

        # Clean up any previous socket
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5)
            self.sock.connect((settings.adsb_json_host, settings.adsb_json_port))
            self.sock.setblocking(False)
            self._connected.set()
            with self._lock:
                self._buffer = ""
            self._retry_interval = RETRY_INTERVAL_SEC  # Reset backoff on success
            # Start background receiver thread
            self._recv_thread = threading.Thread(target=self._receiver, daemon=True)
            self._recv_thread.start()
            print("[ADSBFEEDJSON] Connected to %s:%s" % (
                settings.adsb_json_host, settings.adsb_json_port))
            # HHH1 Fix C: headless STARTUP banner — emitted once per connect
            # so CLI/headless users see the plugin is alive even with 0 traffic.
            self._connected_at = time.time()
            self._silence_warned = False
            self._refused_logged = False
            if not self._startup_logged:
                print("[ADSBFEEDJSON] ADSBFeed connected to %s:%s, waiting for traffic..." % (
                    settings.adsb_json_host, settings.adsb_json_port))
                self._startup_logged = True
        except ConnectionRefusedError:
            self._connected.clear()
            self._last_retry = time.time()
            # HHH1 Fix C: actionable error line on refused (once per refused
            # streak; cleared on successful connect above).
            if not self._refused_logged:
                print(
                    "[ADSBFEEDJSON] ERROR: can't reach %s:%s — is ADSB Analytics running? "
                    "Simulation will continue without live traffic." % (
                        settings.adsb_json_host, settings.adsb_json_port))
                self._refused_logged = True
            print("[ADSBFEEDJSON] Connection refused at %s:%s (bridge not running? will retry)" % (
                settings.adsb_json_host, settings.adsb_json_port))
        except Exception as e:
            self._connected.clear()
            self._last_retry = time.time()
            print("[ADSBFEEDJSON] Connection error: %s (will retry)" % e)

    def _disconnect(self):
        """Disconnect from the bridge and clean up."""
        self._connected.clear()
        # Signal receiver thread to stop and wait for it
        self._stop_receiver.set()
        if self._recv_thread is not None and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=2.0)
        self._stop_receiver.clear()
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None
        # Clear all tracked aircraft from simulation via DEL stack commands
        if self.acpool:
            count = len(self.acpool)
            for ac in list(self.acpool.values()):
                stack.stack("DEL %s" % ac["callsign"])
            self.acpool.clear()
            self._perf_applied.clear()
            print("[ADSBFEEDJSON] Disconnected, removed %d aircraft" % count)
        # UG-01 / UG-02: emit a final summary so the operator sees the
        # session-totals at the moment the stream ends (covers QUIT / OFF
        # paths as well as bridge-side disconnect).
        _log_counter_summary("disconnect")

    def _receiver(self):
        """Background thread: receive data and buffer it (thread-safe)."""
        while self._connected.is_set() and not self._stop_receiver.is_set():
            try:
                data = self.sock.recv(8192)
                if not data:
                    print("[ADSBFEEDJSON] Bridge closed connection")
                    self._connected.clear()
                    break
                decoded = data.decode("utf-8", errors="replace")
                with self._lock:
                    self._buffer += decoded
            except BlockingIOError:
                # Use event wait instead of time.sleep for responsive shutdown
                self._stop_receiver.wait(timeout=0.05)
            except OSError as e:
                if self._connected.is_set():
                    print("[ADSBFEEDJSON] Receive error: %s" % e)
                    self._connected.clear()
                break

    def _parse_buffer(self):
        """Parse complete JSON lines from the buffer (thread-safe)."""
        with self._lock:
            if "\n" not in self._buffer:
                return []
            # Take entire buffer, parse it, put remainder back
            buf = self._buffer
            self._buffer = ""

        messages = []
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                messages.append(msg)
            except json.JSONDecodeError as e:
                # UG-01: previously silently dropped. Now: count + verbose-log
                # the first N exceptions, then sample 1-in-N.
                total = _increment_counter("json_parse_failures")
                if total <= _VERBOSE_LOG_FIRST_N:
                    # Truncate the line so a runaway buffer can't flood logs.
                    snippet = line[:240]
                    print(
                        "[ADSBFEEDJSON] WARNING: json.loads failed "
                        "(failure #%d): %s | line=%r" % (total, e, snippet)
                    )
                elif total % _RATE_LIMIT_SAMPLE_EVERY == 0:
                    print(
                        "[ADSBFEEDJSON] WARNING: json.loads failed "
                        "(failure #%d, sampled): %s" % (total, e)
                    )

        # Put unparsed remainder back
        if buf:
            with self._lock:
                self._buffer = buf + self._buffer

        return messages

    def update(self):
        """Called every simulation tick by BlueSky.

        Handles three states:
        1. Connected: parse messages, inject/update aircraft, remove stale
        2. Want-connected but not connected: retry connection with backoff
        3. Off: do nothing
        """
        # Auto-retry connection if user wants it but we're disconnected
        if self._want_connected and not self._connected.is_set():
            now = time.time()
            if now - self._last_retry >= self._retry_interval:
                self._connect()
                if not self._connected.is_set():
                    # Exponential backoff up to max
                    self._retry_interval = min(
                        self._retry_interval * 1.5, MAX_RETRY_INTERVAL
                    )
            return

        if not self._connected.is_set():
            return

        # HHH1 Fix C: if we've been connected for >60s but parsed zero
        # messages, emit a single actionable WARNING line so headless
        # operators know to check the upstream.
        if (
            not self._silence_warned
            and self._connected_at
            and self._msg_count == 0
            and (time.time() - self._connected_at) >= 60.0
        ):
            print(
                "[ADSBFEEDJSON] WARNING: no traffic received in 60s — "
                "check /api/feed/status and /api/feed/bluesky for aircraft count.")
            self._silence_warned = True

        # Dynamic follow-the-user filter (Level 3). Only runs while the plugin
        # is actively streaming — TRACK is a no-op when OFF. Throttled by wall
        # clock to TRACK_UPDATE_INTERVAL_SEC regardless of tick cadence.
        if self.tracked_callsign:
            self._track_update()

        # Multi-area filter (MMM2): recenter any dynamic areas from traf
        # at their configured update_interval_s cadence. Per-area throttle.
        if self._areas:
            self._refresh_dynamic_areas()

        # Parse received messages
        messages = self._parse_buffer()
        now = time.time()

        for msg in messages:
            # Check for control commands
            if msg.get("cmd") == "quit":
                print("[ADSBFEEDJSON] Received quit command from bridge")
                self._want_connected = False
                self._disconnect()
                return

            # Validate required fields
            icao = msg.get("icao", "").strip().upper()
            lat = msg.get("lat")
            lon = msg.get("lon")
            alt = msg.get("alt")
            spd = msg.get("spd")
            hdg = msg.get("hdg")

            if not icao or lat is None or lon is None:
                # UG-02: previously silently dropped. Now: count which
                # specific required field was missing and log the first N
                # offending payloads, then sample 1-in-N.
                if not icao:
                    _increment_counter("missing_field_icao")
                if lat is None:
                    _increment_counter("missing_field_lat")
                if lon is None:
                    _increment_counter("missing_field_lon")
                total = _increment_counter("missing_required_field_drops")
                if total <= _VERBOSE_LOG_FIRST_N:
                    missing = []
                    if not icao:
                        missing.append("icao")
                    if lat is None:
                        missing.append("lat")
                    if lon is None:
                        missing.append("lon")
                    print(
                        "[ADSBFEEDJSON] WARNING: missing required field(s) %s "
                        "(drop #%d): msg=%r" % (
                            ",".join(missing), total, msg)
                    )
                elif total % _RATE_LIMIT_SAMPLE_EVERY == 0:
                    print(
                        "[ADSBFEEDJSON] WARNING: missing required field "
                        "(drop #%d, sampled)" % total
                    )
                continue

            # UG-01/UG-02: count an accepted record once required-field
            # validation has passed.
            _increment_counter("accepted_records")

            # Build callsign with T-prefix for mixed mode identification
            raw_callsign = msg.get("callsign", icao).strip().upper()[:8] or icao
            # Apply T-prefix, truncate to 8 chars (BlueSky max callsign length)
            callsign = (ADSB_CALLSIGN_PREFIX + raw_callsign)[:8]

            # Update aircraft pool (use _safe_float for resilience to bad data)
            ac_entry = {
                "callsign": callsign,
                "lat": _safe_float(lat),
                "lon": _safe_float(lon),
                "alt": int(_safe_float(alt)),
                "spd": _safe_float(spd),
                "hdg": _safe_float(hdg),
                "type": msg.get("type", self.default_type),
                "ts": now,
            }
            # Preserve optional vehicle performance fields for apply-on-create
            if "mach" in msg:
                ac_entry["mach"] = _safe_float(msg["mach"])
            if "bank" in msg:
                ac_entry["bank"] = _safe_float(msg["bank"])
            self.acpool[icao] = ac_entry

        self._msg_count += len(messages)

        # Inject into BlueSky
        self._inject_traffic()

        # Remove stale aircraft
        self._remove_stale(now)

        # Periodic status (every ~30 seconds = 15 updates at 2s interval)
        if self._msg_count > 0 and self._msg_count % 100 == 0:
            print("[ADSBFEEDJSON] Status: %d aircraft in pool, %d in sim, %d created, %d messages total" % (
                len(self.acpool), traf.ntraf, self._inject_count, self._msg_count))

        # UG-01 / UG-02: wall-clock-driven decode-counter summary so the
        # operator sees totals even when no good messages are flowing (the
        # ``_msg_count % 100`` path above won't fire on a stream of pure
        # garbage). Single-shot, lock-free check against a local timestamp.
        if (
            now - self._last_decode_summary >= _SUMMARY_INTERVAL_SEC
        ):
            _log_counter_summary("periodic")
            self._last_decode_summary = now

    def _set_area(self, area_str):
        """Set area filter from 'lat,lon,radius_nm' string."""
        try:
            parts = [p.strip() for p in area_str.split(",")]
            if len(parts) != 3:
                return False, "Usage: ADSBFEEDJSON AREA lat,lon,radius_nm"
            lat, lon, radius = float(parts[0]), float(parts[1]), float(parts[2])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180 and radius > 0):
                return False, "Invalid area params: lat [-90,90], lon [-180,180], radius > 0"

            # Define a circle area in BlueSky's areafilter system
            shape_name = "ADSBFEED_AREA"
            # Remove old area if exists
            if areafilter.hasArea(shape_name):
                areafilter.deleteArea(shape_name)
            areafilter.defineArea(shape_name, "CIRCLE", [lat, lon, radius])
            self._area_name = shape_name
            self._area_lat = lat
            self._area_lon = lon
            self._area_radius_nm = radius
            self._skipped_area = 0
            print("[ADSBFEEDJSON] Area filter set: %.4f, %.4f, %.1f nm" % (lat, lon, radius))
            return True, "Area filter: %.1f nm around (%.4f, %.4f)" % (radius, lat, lon)
        except (ValueError, IndexError):
            return False, "Usage: ADSBFEEDJSON AREA lat,lon,radius_nm (e.g. 29.18,-81.06,50)"

    def _clear_area(self):
        """Remove area filter."""
        if self._area_name and areafilter.hasArea(self._area_name):
            areafilter.deleteArea(self._area_name)
        self._area_name = ""
        self._area_lat = None
        self._area_lon = None
        self._area_radius_nm = None
        print("[ADSBFEEDJSON] Area filter disabled")
        return True, "Area filter disabled"

    def _set_areas(self, blob):
        """Parse and install a multi-area filter from a JSON blob (MMM2).

        Blob must be a JSON array of area objects. Each object requires
        ``type`` ("static" or "dynamic"), ``name``, ``radius_nm``. Static
        areas also require ``center_lat``/``center_lon``. Dynamic areas
        require ``follow_callsign`` and default ``update_interval_s`` to
        30.0 if missing. Invalid entries cause the whole blob to reject.
        """
        try:
            raw = json.loads(blob)
        except (json.JSONDecodeError, TypeError) as e:
            return False, "AREAS requires valid JSON: %s" % e
        if not isinstance(raw, list):
            return False, "AREAS must be a JSON array"

        parsed: list[dict] = []
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                return False, "AREAS[%d] must be an object" % i
            atype = str(item.get("type", "static")).lower()
            if atype not in ("static", "dynamic"):
                return False, "AREAS[%d] invalid type %r" % (i, atype)
            try:
                radius = float(item.get("radius_nm", 0))
            except (TypeError, ValueError):
                return False, "AREAS[%d] radius_nm not numeric" % i
            if radius <= 0 or radius > 500:
                return False, "AREAS[%d] radius_nm out of range" % i
            name = str(item.get("name") or "area_%d" % i)
            clat = item.get("center_lat")
            clon = item.get("center_lon")
            follow = item.get("follow_callsign")
            if atype == "static":
                if clat is None or clon is None:
                    return False, "AREAS[%d] static needs center_lat/center_lon" % i
            else:  # dynamic
                if not follow:
                    return False, "AREAS[%d] dynamic needs follow_callsign" % i
                follow = str(follow).strip().upper()
            try:
                interval = float(item.get("update_interval_s", 30.0))
            except (TypeError, ValueError):
                interval = 30.0
            parsed.append({
                "type": atype,
                "name": name,
                "radius_nm": radius,
                "center_lat": float(clat) if clat is not None else None,
                "center_lon": float(clon) if clon is not None else None,
                "follow_callsign": follow if atype == "dynamic" else None,
                "update_interval_s": interval if interval > 0 else 30.0,
                "_last_update": 0.0,
            })
        self._areas = parsed
        n_static = sum(1 for a in parsed if a["type"] == "static")
        n_dyn = len(parsed) - n_static
        print("[ADSBFEEDJSON] Multi-area filter: %d static, %d dynamic" % (
            n_static, n_dyn))
        return True, "Multi-area filter: %d areas (%d static, %d dynamic)" % (
            len(parsed), n_static, n_dyn)

    def _refresh_dynamic_areas(self, now=None):
        """Recenter each dynamic area by looking up its follow_callsign in traf.

        Honors each area's ``update_interval_s`` cadence. Called once per
        ``update()`` tick while ``_areas`` is non-empty. Missing callsigns
        leave the previous center in place until the aircraft appears.
        """
        if not self._areas:
            return
        now = now if now is not None else time.time()
        try:
            ids = list(traf.id)
        except AttributeError:
            ids = []
        for area in self._areas:
            if area["type"] != "dynamic":
                continue
            if now - area["_last_update"] < area["update_interval_s"]:
                continue
            callsign = area["follow_callsign"]
            try:
                idx = ids.index(callsign)
                area["center_lat"] = float(traf.lat[idx])
                area["center_lon"] = float(traf.lon[idx])
                area["_last_update"] = now
            except (ValueError, IndexError, TypeError):
                # Aircraft not in traf yet — keep previous center, retry next tick.
                # Still bump _last_update so we don't burn CPU on repeated lookups
                # within the same interval window.
                area["_last_update"] = now

    def _track_update(self):
        """If a tracked callsign is set, push the current position as the bridge
        area filter center every ``TRACK_UPDATE_INTERVAL_SEC`` seconds.

        Silently skips the push if:
            - The tracked aircraft is not yet in ``traf`` (e.g. before its CRE).
            - The interval hasn't elapsed since the last push.
            - The HTTP push to the bridge fails (logged, counted; retried next tick).

        Also updates the local plugin area filter so the post-receive filter
        stays consistent with what the bridge is sending.
        """
        now = time.time()
        if now - self._last_track_push < TRACK_UPDATE_INTERVAL_SEC:
            return

        callsign = self.tracked_callsign
        if not callsign:
            return

        # Look up the aircraft in traf. If it's not there yet, silently skip —
        # this is expected before the user CRE has run, or after a DEL.
        try:
            ids = list(traf.id)  # Copy to avoid race with sim thread
            idx = ids.index(callsign)
        except (ValueError, AttributeError):
            self._track_skip_count += 1
            # If the aircraft has been gone for a very long time, keep
            # skipping — caller can re-TRACK or TRACK OFF explicitly.
            return

        try:
            lat = float(traf.lat[idx])
            lon = float(traf.lon[idx])
        except (IndexError, TypeError, ValueError):
            self._track_skip_count += 1
            return

        # Update local plugin-level filter (the one used by _is_inside_area
        # as a defensive fallback; the bridge does the primary filtering).
        radius = float(self._track_radius_nm)
        try:
            shape_name = "ADSBFEED_AREA"
            if areafilter.hasArea(shape_name):
                areafilter.deleteArea(shape_name)
            areafilter.defineArea(shape_name, "CIRCLE", [lat, lon, radius])
            self._area_name = shape_name
            self._area_lat = lat
            self._area_lon = lon
            self._area_radius_nm = radius
        except Exception:
            # areafilter can raise if BlueSky is mid-reset; non-fatal.
            pass

        # Push to the bridge via REST. Use urllib (stdlib only).
        host = getattr(settings, "adsb_json_host", "localhost")
        url = "http://%s:%d/api/bridge/area?lat=%.6f&lon=%.6f&radius_nm=%.3f" % (
            host, TRACK_BRIDGE_API_PORT, lat, lon, radius)
        try:
            req = urllib.request.Request(url, method="POST")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                resp.read()
            self._track_push_count += 1
            self._last_track_push = now
            self._track_last_error = None
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                TimeoutError) as e:
            # Non-fatal — the local area filter still protects BlueSky from
            # obviously out-of-range injects. Retry next tick.
            self._track_last_error = str(e)
            self._last_track_push = now  # Don't hammer on failure

    def _is_inside_area(self, lat, lon, alt_ft):
        """Check if a position is inside the configured area filter.

        Uses fast haversine check (no BlueSky areafilter overhead for single points).
        Returns True if no area filter is set or if position is inside.

        Multi-area mode (MMM2): if ``_areas`` is non-empty, return True if
        the position falls inside ANY area. Dynamic areas whose center has
        not yet been populated (center_lat/center_lon None) are skipped.
        """
        if self._areas:
            for area in self._areas:
                clat = area.get("center_lat")
                clon = area.get("center_lon")
                if clat is None or clon is None:
                    continue
                if self._haversine_nm(lat, lon, clat, clon) <= area["radius_nm"]:
                    return True
            return False

        if not self._area_name:
            return True

        return self._haversine_nm(
            lat, lon, self._area_lat, self._area_lon) <= self._area_radius_nm

    @staticmethod
    def _haversine_nm(lat1, lon1, lat2, lon2):
        """Great-circle distance in nautical miles between two lat/lon points."""
        dlat = math.radians(lat1 - lat2)
        dlon = math.radians(lon1 - lon2)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(lat2)) *
             math.cos(math.radians(lat1)) *
             math.sin(dlon / 2) ** 2)
        return 2 * math.asin(math.sqrt(a)) * 3440.065  # Earth radius in nm

    def _inject_traffic(self):
        """Create or update aircraft in BlueSky from the pool.

        Uses stack commands (CRE, MOVE, HDG, SPD) for aircraft creation and
        updates, matching the approach of BlueSky's built-in adsbfeed.py plugin
        which is proven to work with the server-sim-GUI architecture.

        Stack commands go through BlueSky's full command pipeline including
        argument parsing, ensuring proper integration with the traffic system,
        autopilot, and GUI state broadcasts.
        """
        if not self.acpool:
            return

        outside_area = []  # Track aircraft that left the area (for removal)

        for icao, ac in list(self.acpool.items()):
            acid = ac["callsign"]
            lat = ac["lat"]
            lon = ac["lon"]
            alt_ft = ac["alt"]       # feet (from JSON)
            spd_kts = ac["spd"]      # TAS in knots (from JSON)
            hdg = ac["hdg"]
            actype = ac["type"]

            # Area filter: skip aircraft outside the configured area
            if not self._is_inside_area(lat, lon, alt_ft):
                idx = traf.id2idx(acid)
                if idx >= 0:
                    # Aircraft was inside but left — mark for removal
                    outside_area.append(icao)
                else:
                    self._skipped_area += 1
                continue

            # Convert TAS to CAS for the CRE/SPD command (in knots)
            if spd_kts > 0.1:
                spd_ms = spd_kts * kts  # knots -> m/s
                alt_m = alt_ft * ft     # feet -> meters
                cas_ms = aero.tas2cas(spd_ms, alt_m)
                cas_kts = cas_ms / kts  # m/s -> knots for stack command
            else:
                cas_kts = 0.0

            # Teleport mode: when the relay tags a message with force=true,
            # skip NORESO on creation and re-assert altitude each tick so
            # BlueSky's autopilot can't drift the aircraft between fixes.
            # See docs/findings/teleport-playback-mode-design.md.
            force = bool(ac.get("force"))

            idx = traf.id2idx(acid)
            if idx < 0:
                # New aircraft — create via stack command (same approach as
                # BlueSky's built-in adsbfeed.py). The CRE stack command
                # handles argument parsing (alt in ft, spd in kts) and proper
                # integration with the GUI state broadcast system.
                stack.stack(
                    "CRE %s, %s, %f, %f, %f, %d, %d" % (
                        acid, actype, lat, lon, hdg,
                        int(alt_ft), int(cas_kts)))
                # In routing mode, disable resolution for ADS-B aircraft —
                # conflicts are still detected between user aircraft and
                # ADS-B traffic, but resolution won't alter ADS-B trajectories.
                # In teleport mode NORESO is unnecessary (aircraft won't move
                # except when we tell them to), so we skip it entirely.
                if not force:
                    stack.stack("NORESO %s" % acid)
                self._inject_count += 1
                # Apply vehicle performance overrides (once per aircraft)
                if icao not in self._perf_applied:
                    if "mach" in ac:
                        stack.stack("SPD %s M%.2f" % (acid, ac["mach"]))
                    if "bank" in ac:
                        stack.stack("BANK %s %g" % (acid, ac["bank"]))
                    self._perf_applied.add(icao)
            else:
                # Existing aircraft — update position via stack commands
                # (same approach as built-in adsbfeed.py)
                stack.stack("MOVE %s, %f, %f, %d" % (
                    acid, lat, lon, int(alt_ft)))
                stack.stack("HDG %s, %f" % (acid, hdg))
                stack.stack("SPD %s, %d" % (acid, int(cas_kts)))
                if force:
                    # Teleport mode: clamp altitude each tick so autopilot
                    # VS / target-alt can't drift between 5 s fixes.
                    stack.stack("ALT %s, %d" % (acid, int(alt_ft)))
                self._update_count += 1

        # Remove aircraft that left the area
        for icao in outside_area:
            acid = self.acpool[icao]["callsign"]
            stack.stack("DEL %s" % acid)
            del self.acpool[icao]
            self._perf_applied.discard(icao)
        if outside_area:
            print("[ADSBFEEDJSON] Removed %d aircraft that left area" % len(outside_area))

        # Log periodically (first batch + every 50 creates)
        if self._inject_count > 0 and (
                self._inject_count <= 5 or self._inject_count % 50 == 0):
            area_info = ""
            if self._area_name:
                area_info = ", %d filtered by area" % self._skipped_area
            print("[ADSBFEEDJSON] Created %d aircraft so far (pool: %d, sim: %d%s)" % (
                self._inject_count, len(self.acpool), traf.ntraf, area_info))

    def _remove_stale(self, now):
        """Remove aircraft not seen for stale_timeout seconds.

        Uses DEL stack commands (same approach as built-in adsbfeed.py)
        for proper integration with the GUI state system.
        """
        stale = [
            icao for icao, ac in self.acpool.items()
            if now - ac["ts"] > self.stale_timeout
        ]
        if not stale:
            return

        for icao in stale:
            acid = self.acpool[icao]["callsign"]
            stack.stack("DEL %s" % acid)
            del self.acpool[icao]
            self._perf_applied.discard(icao)

        if stale:
            print("[ADSBFEEDJSON] Removed %d stale aircraft" % len(stale))
