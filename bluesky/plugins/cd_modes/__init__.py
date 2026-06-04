"""BluePlan multi-mode conflict-detection plugin family.

Eight modes, all subclasses of ``StateBased`` via ``BaseFilterDetect``,
auto-registered with BlueSky's ``CDMETHOD`` system simply by being imported
(``ConflictDetection`` uses Python's ``__subclasses__()`` internally).

Stack commands added by this plugin:

    CDMETHOD FULLDETECT | EGOONLYDETECT | EGOVSOTHERDETECT |
             CALLSIGNFILTERDETECT | PREFIXFILTERDETECT |
             TYPEFILTERDETECT | PAIRFILTERDETECT |
             VTOLVSADSBDETECT
    CDCONFIG <param> <value>
        Configure the active BluePlan CD mode. Params:
          ego_prefix, adsb_prefix, callsigns, pair_list, actype, vtol_types
        Use ``CDCONFIG`` or ``CDCONFIG ?`` to dump current state.

See ``docs/findings/egodetect-multi-mode-design.md`` for the full design.
"""
from __future__ import annotations

# Importing each module triggers subclass registration via the Entity
# metaclass used by BlueSky's ConflictDetection base.
from .base import BaseFilterDetect  # noqa: F401
from .full import FullDetect  # noqa: F401
from .ego_only import EgoOnlyDetect  # noqa: F401
from .ego_vs_other import EgoVsOtherDetect  # noqa: F401
from .callsign import CallsignFilterDetect  # noqa: F401
from .prefix import PrefixFilterDetect  # noqa: F401
from .type_filter import TypeFilterDetect  # noqa: F401
from .pair import PairFilterDetect  # noqa: F401
from .vtol_vs_adsb import VtolVsAdsbDetect, DEFAULT_VTOL_TYPES  # noqa: F401


# BluePlan-obs B5 (Pattern P-F, UG-21/UG-25): per-plugin update-hook error
# counter. CD_MODES has no @timed_function tick callback of its own — its
# per-frame work runs inside ``BaseFilterDetect.detect`` (base.py), invoked
# by BlueSky's ConflictDetection layer once per sim step. We still expose the
# `[PLUGIN_UPDATE_ERROR]` counter here so the stack-command path
# (`_cdconfig`) and `init_plugin` can use a single per-plugin reporting
# channel; the runner's output_handler scanner consumes the same prefix
# across all plugins.
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


def init_plugin():
    """BlueSky plugin entry point.

    Declares the plugin as a ``sim`` plugin and registers the ``CDCONFIG``
    stack command. The individual ``CDMETHOD <NAME>`` invocations are
    handled by upstream BlueSky via subclass discovery; we only need to
    provide the config surface.
    """
    config = {
        "plugin_name": "CD_MODES",
        "plugin_type": "sim",
    }
    stackfunctions = {
        "CDCONFIG": [
            "CDCONFIG [param,value]",
            "[txt,txt]",
            _cdconfig,
            "Configure the active BluePlan CD mode "
            "(ego_prefix, adsb_prefix, callsigns, pair_list, actype)",
        ],
    }
    # WWW8 verbosity: advertise which CD modes are discoverable so
    # CDMETHOD <NAME> failures are traceable to registration, not config.
    _modes = "FULLDETECT, EGOONLYDETECT, EGOVSOTHERDETECT, CALLSIGNFILTERDETECT, PREFIXFILTERDETECT, TYPEFILTERDETECT, PAIRFILTERDETECT, VTOLVSADSBDETECT"
    print("[CD_MODES] Plugin registered, 8 CD modes available: " + _modes)
    return config, stackfunctions


def _cdconfig(param: str = "", value: str = ""):
    """Apply a config value to whatever BluePlan CD mode is currently active.

    Returns ``(ok, message)``. Lazy-imports BlueSky so this module can still
    be imported in unit tests.
    """
    # BluePlan-obs B5 (UG-21): outer guard so an unexpected exception inside
    # the dispatch does not propagate to BlueSky's stack handler and silently
    # drop the user's CDCONFIG command. The per-branch try/excepts below stay
    # so callers still get the structured "(ok, message)" contract.
    try:
        return _cdconfig_impl(param, value)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        _b5_record_update_error(__name__, e)
        return False, f"CDCONFIG: internal error ({type(e).__name__}: {e})"


def _cdconfig_impl(param: str = "", value: str = ""):
    try:
        from bluesky.traffic.asas import ConflictDetection  # type: ignore
    except Exception as exc:  # pragma: no cover
        return False, f"CDCONFIG: BlueSky not available ({exc})"

    try:
        inst = ConflictDetection.instance()
    except Exception as exc:  # pragma: no cover
        return False, f"CDCONFIG: no active ConflictDetection instance ({exc})"

    if not isinstance(inst, BaseFilterDetect):
        return False, (
            "CDCONFIG requires a BluePlan CD mode to be active. "
            "Run CDMETHOD <name> first "
            "(FULLDETECT, EGOONLYDETECT, EGOVSOTHERDETECT, "
            "CALLSIGNFILTERDETECT, PREFIXFILTERDETECT, "
            "TYPEFILTERDETECT, PAIRFILTERDETECT, VTOLVSADSBDETECT)"
        )

    param_key = (param or "").lower().strip()

    if param_key == "ego_prefix":
        inst.ego_prefix = (value or "").strip()
        return True, f"ego_prefix = {inst.ego_prefix!r}"

    if param_key == "adsb_prefix":
        inst.adsb_prefix = (value or "").strip() or "T"
        return True, f"adsb_prefix = {inst.adsb_prefix!r}"

    if param_key == "callsigns":
        inst.callsigns = frozenset(
            cs.strip().upper() for cs in (value or "").split(",") if cs.strip()
        )
        return True, f"callsigns = {sorted(inst.callsigns)}"

    if param_key == "pair_list":
        pairs = set()
        for token in (value or "").split(","):
            token = token.strip()
            if ":" not in token:
                continue
            a, b = token.split(":", 1)
            a, b = a.strip().upper(), b.strip().upper()
            if a and b:
                pairs.add(frozenset((a, b)))
        inst.pair_list = frozenset(pairs)
        return True, f"pair_list = {len(inst.pair_list)} pairs loaded"

    if param_key == "actype":
        inst.actype = (value or "").strip().upper()
        return True, f"actype = {inst.actype!r}"

    if param_key == "vtol_types":
        inst.vtol_types = frozenset(
            t.strip().upper() for t in (value or "").split(",") if t.strip()
        )
        return True, (
            f"vtol_types = {sorted(inst.vtol_types)}"
            if inst.vtol_types
            else f"vtol_types = [] (using built-in default {sorted(DEFAULT_VTOL_TYPES)})"
        )

    if param_key in ("", "?", "list", "status"):
        vtol_cfg = getattr(inst, "vtol_types", frozenset())
        return True, (
            f"CDCONFIG status for {type(inst).__name__}:\n"
            f"  ego_prefix  = {inst.ego_prefix!r}\n"
            f"  adsb_prefix = {inst.adsb_prefix!r}\n"
            f"  callsigns   = {sorted(inst.callsigns)}\n"
            f"  pair_list   = {len(inst.pair_list)} pairs\n"
            f"  actype      = {inst.actype!r}\n"
            f"  vtol_types  = {sorted(vtol_cfg) if vtol_cfg else '(default)'}"
        )

    return False, (
        f"Unknown CDCONFIG param: {param!r}. "
        "Valid: ego_prefix, adsb_prefix, callsigns, pair_list, actype, vtol_types"
    )
