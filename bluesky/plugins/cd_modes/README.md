# cd_modes — BluePlan multi-mode conflict detection

> Ecosystem overview: [../../../docs/ECOSYSTEM_INDEX.md](../../../docs/ECOSYSTEM_INDEX.md)

Family of eight conflict-detection modes, all subclasses of BlueSky's
`StateBased`, switchable at runtime via `CDMETHOD <name>`. Full design:
[`../../../docs/findings/egodetect-multi-mode-design.md`](../../../docs/findings/egodetect-multi-mode-design.md).

## Modes

| Class                  | CDMETHOD name          | Keeps a pair when...                                                                    |
| ---------------------- | ---------------------- | --------------------------------------------------------------------------------------- |
| `FullDetect`           | `FULLDETECT`           | always (baseline)                                                                       |
| `EgoOnlyDetect`        | `EGOONLYDETECT`        | at least one side is ego                                                                |
| `EgoVsOtherDetect`     | `EGOVSOTHERDETECT`     | exactly one side is ego (XOR, no user-vs-user)                                          |
| `CallsignFilterDetect` | `CALLSIGNFILTERDETECT` | at least one side is in the configured callsign list                                    |
| `PrefixFilterDetect`   | `PREFIXFILTERDETECT`   | at least one side matches `ego_prefix` (`!` negates)                                    |
| `TypeFilterDetect`     | `TYPEFILTERDETECT`     | at least one side has the configured ICAO type                                          |
| `PairFilterDetect`     | `PAIRFILTERDETECT`     | unordered pair is in the configured pair list                                           |
| `VtolVsAdsbDetect`     | `VTOLVSADSBDETECT`     | exactly one side is ADSB-prefixed AND the non-ADSB side's ICAO type is in `vtol_types`  |

"Ego" = callsign starts with `ego_prefix`, or (if unset) does NOT start with
`adsb_prefix` (default `T`, BluePlan's ADSB relay convention).

## Stack commands

```
CDMETHOD FULLDETECT
CDMETHOD EGOONLYDETECT
CDMETHOD EGOVSOTHERDETECT
CDMETHOD CALLSIGNFILTERDETECT
CDMETHOD PREFIXFILTERDETECT
CDMETHOD TYPEFILTERDETECT
CDMETHOD PAIRFILTERDETECT
CDMETHOD VTOLVSADSBDETECT

CDCONFIG                            # dump current state
CDCONFIG ego_prefix USER
CDCONFIG adsb_prefix T
CDCONFIG callsigns VTOL1,KLM101
CDCONFIG pair_list VTOL1:KLM101,VTOL1:N12345
CDCONFIG actype EVTOL
CDCONFIG vtol_types EH101,B407,B412,VTOL,UAM,EVTOL
```

## Recipe: VTOL-vs-ADSB only

The professor's bug report — "it is counting all airplanes as conflicts and
not VTOL to airplanes" — maps directly onto `VtolVsAdsbDetect`. Unlike
`ego_vs_other` (which only checks the T-prefix) or `type` (which only checks
actype), this mode requires BOTH: one side must be ADSB-prefixed AND the
other side's ICAO type must be in the VTOL allow-list.

```text
0:00:00.00>ASAS ON
0:00:00.00>CDMETHOD VTOLVSADSBDETECT
0:00:00.00>CDCONFIG adsb_prefix T
0:00:00.00>CDCONFIG vtol_types EH101,B407,B412,VTOL,UAM,EVTOL
0:00:00.00>CRELOG CONFLICTS 1 CONFLICT PAIRS
```

Leaving `vtol_types` unset falls back to the plugin's built-in default list
(`EH101, B407, B412, B429, EC35, EC45, H60, VTOL, UAM, EVTOL`). Unknown
aircraft types are default-excluded so stray background traffic cannot
masquerade as a VTOL when `bs.traf.type` is empty.

## Example `.scn`

```
0:00:00.00>ASAS ON
0:00:00.00>CDMETHOD EGOONLYDETECT
0:00:00.00>CDCONFIG ego_prefix USER
0:00:00.00>CDCONFIG adsb_prefix T
0:00:00.00>CRELOG CONFLICTS 1 CONFLICT PAIRS
```

## Adding a new mode

1. Create `new_mode.py` with a class subclassing `BaseFilterDetect` and
   implementing `should_include_pair(a, b)`.
2. Import it in `__init__.py` so Python registers the subclass.
3. Add the literal to `ConflictDetectionConfig.mode` in
   `app/routes/scenarios_shared.py`.
4. Add the class name to `_MODE_TO_CLASS` in `config_renderer.py`.
5. Add a unit test in `../tests/test_cd_modes.py`.

No base-class, stack-command, or other-mode changes required — the
predicate pattern keeps modes fully independent.

## File layout

```
cd_modes/
├── __init__.py          # plugin entry, CDCONFIG stack command
├── base.py              # BaseFilterDetect + detect() wrapper
├── full.py              # FullDetect
├── ego_only.py          # EgoOnlyDetect
├── ego_vs_other.py      # EgoVsOtherDetect
├── callsign.py          # CallsignFilterDetect
├── prefix.py            # PrefixFilterDetect
├── type_filter.py       # TypeFilterDetect
├── pair.py              # PairFilterDetect
├── config_renderer.py   # ConflictDetectionConfig -> stack commands
└── README.md            # this file
```

## Testing

```bash
# From the blueplan repo root:
python3 -m pytest bluesky_extensions/plugins/tests/test_cd_modes.py -v
```

Unit tests run without BlueSky installed — imports are lazy and
`BaseFilterDetect.detect()` is gated by a runtime import.
