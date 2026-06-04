"""BlueSky plugin: CONFLICTCAM — conditional screenshot capture.

Takes screenshots based on configurable trigger conditions. Default trigger:
rising edge of conflict state for user aircraft (non-"T" prefixed callsigns).

Trigger conditions are extensible — see TRIGGER_CONDITIONS dict for all
supported triggers. Custom conditions can be added by defining a function
that returns (should_trigger: bool, description: str).

Enable in scenario:
    PLUGIN CONFLICTCAM
    CONFLICTCAM ON

Configuration:
    CONFLICTCAM ON              # Enable with default trigger (user_conflict)
    CONFLICTCAM OFF             # Disable
    CONFLICTCAM COOLDOWN 10     # Min seconds between screenshots (default 5)
    CONFLICTCAM DIR path        # Output directory
    CONFLICTCAM TRIGGER name    # Set trigger condition (see below)
    CONFLICTCAM FILTER pattern  # Filter aircraft by callsign pattern

Trigger conditions:
    user_conflict   — Any user aircraft enters conflict (default)
    any_conflict    — Any aircraft enters conflict
    los             — Loss of separation occurs
    user_los        — User aircraft in loss of separation
    all_user_conf   — ALL user aircraft simultaneously in conflict
    new_pair        — A new conflict pair is detected

Deployment:
    This plugin lives in the BluePlan parent repository
    (bluesky_extensions/plugins/) and is deployed to bluesky/bluesky/plugins/
    via the deploy script (RR1: inside the Python package so BlueSky's
    dynamic plugin loader can discover it).
    DO NOT edit the copy in bluesky/bluesky/plugins/ directly.
"""
import os

import bluesky as bs
from bluesky import core, stack, traf, sim


# Plugin state
conflictcam = None


# BluePlan-obs B5 (Pattern P-F, UG-21/UG-25): per-plugin update-hook error
# counter. Without this guard a single traceback in update() silently kills
# the timed-function callback and the plugin appears "loaded" but inert. The
# `[PLUGIN_UPDATE_ERROR]` prefix is the durable stdout signal consumed by
# the runner's output_handler scanner.
_update_errors_total = 0
_update_errors_logged = 0


def _b5_record_update_error(plugin, e):
    global _update_errors_total, _update_errors_logged
    _update_errors_total += 1
    if _update_errors_logged < 3 or _update_errors_total % 1000 == 0:
        _update_errors_logged += 1
        print(
            f'[PLUGIN_UPDATE_ERROR] plugin={plugin} '
            f'reason={type(e).__name__}: {e}',
            flush=True,
        )


def _to_tuple_set(pairs):
    """Convert a collection of pairs to a set of tuples.

    BlueSky may return lospairs/confpairs as lists of lists or lists of tuples.
    set() requires hashable elements, so convert each pair to a tuple first.
    """
    if not pairs:
        return set()
    return set(tuple(p) for p in pairs)


def init_plugin():
    global conflictcam
    conflictcam = ConflictCam()

    # WWW8 verbosity: emit one banner so operators can grep init without
    # waiting for the first trigger event (which may never fire if the
    # scenario never produces a conflict).
    print("[CONFLICTCAM] Plugin registered, default trigger=%s, cooldown=%.1fs, dir=%s" % (
        conflictcam.trigger_name, conflictcam.cooldown_sec, conflictcam.output_dir))

    config = {
        'plugin_name': 'CONFLICTCAM',
        'plugin_type': 'sim',
    }

    stackfunctions = {
        'CONFLICTCAM': [
            'CONFLICTCAM ON/OFF | COOLDOWN sec | DIR path | TRIGGER name | FILTER pattern',
            '[txt,txt]',
            conflictcam.command,
            'Conditional screenshot capture (conflict-triggered by default)',
        ],
    }

    return config, stackfunctions


# ---------------------------------------------------------------------------
# Trigger condition functions
# Each returns (should_trigger: bool, description: str)
# They receive the ConflictCam instance for access to state
# ---------------------------------------------------------------------------

def _trigger_user_conflict(cam):
    """Trigger when any user aircraft transitions INTO conflict."""
    for i in cam._user_indices():
        acid = traf.id[i]
        currently = bool(traf.cd.inconf[i])
        was = cam.prev_state.get(acid, False)
        cam.prev_state[acid] = currently
        if currently and not was:
            return True, f'{acid} entered conflict'
    return False, ''


def _trigger_any_conflict(cam):
    """Trigger when any aircraft transitions INTO conflict."""
    for i in range(traf.ntraf):
        acid = traf.id[i]
        currently = bool(traf.cd.inconf[i])
        was = cam.prev_state.get(acid, False)
        cam.prev_state[acid] = currently
        if currently and not was:
            return True, f'{acid} entered conflict'
    return False, ''


def _trigger_los(cam):
    """Trigger when any loss of separation occurs."""
    current_los = _to_tuple_set(traf.cd.lospairs)
    prev_los = cam.prev_los_pairs
    new_los = current_los - prev_los
    cam.prev_los_pairs = current_los
    if new_los:
        pair = list(new_los)[0]
        return True, f'LoS: {pair[0]} vs {pair[1]}'
    return False, ''


def _trigger_user_los(cam):
    """Trigger when a user aircraft is in a new loss of separation."""
    current_los = _to_tuple_set(traf.cd.lospairs)
    prev_los = cam.prev_los_pairs
    new_los = current_los - prev_los
    cam.prev_los_pairs = current_los
    for pair in new_los:
        if not pair[0].startswith('T') or not pair[1].startswith('T'):
            return True, f'User LoS: {pair[0]} vs {pair[1]}'
    return False, ''


def _trigger_all_user_conf(cam):
    """Trigger when ALL user aircraft are simultaneously in conflict."""
    indices = cam._user_indices()
    if not indices:
        return False, ''
    all_conf = all(bool(traf.cd.inconf[i]) for i in indices)
    was_all = cam.prev_state.get('__all_conf__', False)
    cam.prev_state['__all_conf__'] = all_conf
    if all_conf and not was_all:
        return True, 'All user aircraft in conflict'
    return False, ''


def _trigger_new_pair(cam):
    """Trigger when a new conflict pair is detected (any aircraft)."""
    current_pairs = _to_tuple_set(traf.cd.confpairs)
    prev_pairs = cam.prev_conf_pairs
    new_pairs = current_pairs - prev_pairs
    cam.prev_conf_pairs = current_pairs
    if new_pairs:
        pair = list(new_pairs)[0]
        return True, f'New conflict: {pair[0]} vs {pair[1]}'
    return False, ''


# Registry of available trigger conditions
TRIGGER_CONDITIONS = {
    'user_conflict': (_trigger_user_conflict, 'User aircraft enters conflict'),
    'any_conflict': (_trigger_any_conflict, 'Any aircraft enters conflict'),
    'los': (_trigger_los, 'Loss of separation occurs'),
    'user_los': (_trigger_user_los, 'User aircraft in LoS'),
    'all_user_conf': (_trigger_all_user_conf, 'All user aircraft in conflict'),
    'new_pair': (_trigger_new_pair, 'New conflict pair detected'),
}


class ConflictCam(core.Entity):
    """Conditional screenshot capture based on configurable triggers."""

    def __init__(self):
        super().__init__()
        self.active = False
        self.cooldown_sec = 5.0
        self.last_screenshot_time = -999.0
        self.screenshot_count = 0
        self.output_dir = './screenshots/conflictcam/'
        self.trigger_name = 'user_conflict'
        self.callsign_filter = None  # Optional: only consider matching aircraft

        # State for trigger functions
        self.prev_state = {}        # Per-aircraft previous conflict state
        self.prev_los_pairs = set()
        self.prev_conf_pairs = set()

    def _user_indices(self):
        """Get indices of user aircraft (non-T-prefix), optionally filtered."""
        indices = []
        for i in range(traf.ntraf):
            acid = traf.id[i]
            if acid.startswith('T'):
                continue
            if self.callsign_filter and self.callsign_filter not in acid:
                continue
            indices.append(i)
        return indices

    def command(self, arg1='', arg2=None):
        """Handle CONFLICTCAM stack command."""
        arg1 = (arg1 or '').upper().strip()
        arg2 = arg2 or ''

        if arg1 in ('ON', ''):
            self.active = True
            os.makedirs(self.output_dir, exist_ok=True)
            trigger_desc = TRIGGER_CONDITIONS.get(
                self.trigger_name, (None, 'unknown')
            )[1]
            return True, (
                f'CONFLICTCAM active. Trigger: {self.trigger_name} '
                f'({trigger_desc}). Output: {self.output_dir}'
            )

        if arg1 == 'OFF':
            self.active = False
            return True, (
                f'CONFLICTCAM disabled. {self.screenshot_count} screenshots taken.'
            )

        if arg1 == 'COOLDOWN':
            try:
                self.cooldown_sec = float(arg2)
                return True, f'CONFLICTCAM cooldown: {self.cooldown_sec}s'
            except (ValueError, TypeError):
                return False, 'CONFLICTCAM COOLDOWN requires a number (seconds)'

        if arg1 == 'DIR':
            if arg2:
                self.output_dir = arg2
                os.makedirs(self.output_dir, exist_ok=True)
                return True, f'CONFLICTCAM output dir: {self.output_dir}'
            return False, 'CONFLICTCAM DIR requires a path'

        if arg1 == 'TRIGGER':
            name = arg2.lower().strip() if arg2 else ''
            if name in TRIGGER_CONDITIONS:
                self.trigger_name = name
                desc = TRIGGER_CONDITIONS[name][1]
                return True, f'CONFLICTCAM trigger: {name} ({desc})'
            available = ', '.join(TRIGGER_CONDITIONS.keys())
            return False, f'Unknown trigger: {name}. Available: {available}'

        if arg1 == 'FILTER':
            if arg2:
                self.callsign_filter = arg2.upper()
                return True, f'CONFLICTCAM filter: aircraft matching "{self.callsign_filter}"'
            self.callsign_filter = None
            return True, 'CONFLICTCAM filter cleared (all user aircraft)'

        if arg1 == 'STATUS':
            trigger_desc = TRIGGER_CONDITIONS.get(
                self.trigger_name, (None, 'unknown')
            )[1]
            filt = self.callsign_filter or 'none'
            return True, (
                f'CONFLICTCAM: {"active" if self.active else "inactive"}, '
                f'trigger={self.trigger_name} ({trigger_desc}), '
                f'cooldown={self.cooldown_sec}s, filter={filt}, '
                f'screenshots={self.screenshot_count}'
            )

        return False, (
            f'Unknown CONFLICTCAM command: {arg1}. '
            'Use ON/OFF/COOLDOWN/DIR/TRIGGER/FILTER/STATUS'
        )

    @core.timed_function(name='conflictcam', dt=1.0)
    def update(self):
        """Check trigger condition every simulation second."""
        # BluePlan-obs B5 (UG-21): guard the tick body so a single bad frame
        # cannot kill the timed-function callback for the rest of the run.
        try:
            if not self.active or traf.ntraf == 0:
                return

            # Get the trigger function
            trigger_entry = TRIGGER_CONDITIONS.get(self.trigger_name)
            if not trigger_entry:
                return

            trigger_fn = trigger_entry[0]
            should_trigger, description = trigger_fn(self)

            # Clean up stale state for deleted aircraft
            current_ids = set(traf.id)
            stale = [k for k in self.prev_state if k not in current_ids and k != '__all_conf__']
            for k in stale:
                del self.prev_state[k]

            # Take screenshot if triggered and cooldown has elapsed
            if should_trigger:
                elapsed = sim.simt - self.last_screenshot_time
                if elapsed >= self.cooldown_sec:
                    self._take_screenshot(description)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            _b5_record_update_error(__name__, e)

    def _take_screenshot(self, description=''):
        """Trigger a screenshot via BlueSky's screen capture."""
        if bs.scr is not None and hasattr(bs.scr, 'savescreen'):
            os.makedirs(self.output_dir, exist_ok=True)

            hours = int(sim.simt // 3600)
            mins = int((sim.simt % 3600) // 60)
            secs = int(sim.simt % 60)
            # Prefer PNG format; fall back to BMP if PIL is unavailable
            try:
                import PIL  # noqa: F401
                ext = 'png'
            except ImportError:
                ext = 'bmp'
            filename = f'conflict_{hours:02d}h{mins:02d}m{secs:02d}s.{ext}'
            filepath = os.path.join(self.output_dir, filename)

            bs.scr.screenshotname = filepath
            bs.scr.screenshot = True

            self.screenshot_count += 1
            self.last_screenshot_time = sim.simt

            desc_part = f' ({description})' if description else ''
            stack.stack(
                f'ECHO CONFLICTCAM: Screenshot #{self.screenshot_count}'
                f' at t={sim.simt:.1f}s{desc_part}'
            )
        else:
            stack.stack('ECHO CONFLICTCAM: No screen available (headless mode?)')
