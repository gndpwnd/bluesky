# BlueSky - The Open Air Traffic Simulator

[![Open in Visual Studio Code](https://img.shields.io/static/v1?logo=visualstudiocode&label=&message=Open%20in%20Visual%20Studio%20Code&labelColor=2c2c32&color=007acc&logoColor=007acc)](https://open.vscode.dev/TUDelft-CNS-ATM/bluesky)
[![GitHub release](https://img.shields.io/github/release/TUDelft-CNS-ATM/bluesky.svg)](https://GitHub.com/TUDelft-CNS-ATM/bluesky/releases/)
![GitHub all releases](https://img.shields.io/github/downloads/TUDelft-CNS-ATM/bluesky/total?style=social)
[![Discord](https://img.shields.io/discord/1359446056877690970?style=flat&logo=discord&logoColor=green&logoSize=auto&label=BlueSky%20discussion)](https://discord.gg/wkBKgXCHYN)


[![PyPI version shields.io](https://img.shields.io/pypi/v/bluesky-simulator.svg)](https://pypi.python.org/pypi/bluesky-simulator/)
![PyPI - Downloads](https://img.shields.io/pypi/dm/bluesky-simulator?style=plastic)
[![PyPI license](https://img.shields.io/pypi/l/bluesky-simulator?style=plastic)](https://pypi.python.org/pypi/bluesky-simulator/)
[![PyPI pyversions](https://img.shields.io/pypi/pyversions/bluesky-simulator?style=plastic)](https://pypi.python.org/pypi/bluesky-simulator/)

BlueSky is meant as a tool to perform research on Air Traffic Management and Air Traffic Flows, and is distributed under the MIT license.

The goal of BlueSky is to provide everybody who wants to visualize, analyze or simulate air
traffic with a tool to do so without any restrictions, licenses or limitations. It can be copied,
modified, cited, etc. without any limitations.

**Citation info:** J. M. Hoekstra and J. Ellerbroek, "[BlueSky ATC Simulator Project: an Open Data and Open Source Approach](https://www.researchgate.net/publication/304490055_BlueSky_ATC_Simulator_Project_an_Open_Data_and_Open_Source_Approach)", Proceedings of the seventh International Conference for Research on Air Transport (ICRAT), 2016.

## BlueSky Releases
BlueSky is also available as a pip package, for which periodically version releases are made. You can find the latest release here:
https://github.com/TUDelft-CNS-ATM/bluesky/releases
The BlueSky pip package is installed with the following command:

    pip install bluesky-simulator[full]

Using ZSH? Add quotes around the package name: `"bluesky-simulator[full]"`. For more installation instructions go to the Wiki.

## BlueSky Wiki
Installation and user guides are accessible at:
https://github.com/TUDelft-CNS-ATM/bluesky/wiki

## Some features of BlueSky:
- Written in the freely available, ultra-simple-hence-easy-to-learn, multi-platform language
Python 3 (using numpy and either pygame or Qt+OpenGL for visualisation) with source
- Extensible by means of self-contained [plugins](https://github.com/TUDelft-CNS-ATM/bluesky/wiki/plugin)
- Contains open source data on navaids, performance data of aircraft and geography
- Global coverage navaid and airport data
- Contains simulations of aircraft performance, flight management system (LNAV, VNAV under construction),
autopilot, conflict detection and resolution and airborne separation assurance systems
- Compatible with BADA 3.x data
- Compatible wth NLR Traffic Manager TMX as used by NLR and NASA LaRC
- Traffic is controlled via user inputs in a console window or by playing scenario files (.SCN)
containing the same commands with a time stamp before the command ("HH:MM:SS.hh>")
- Mouse clicks in traffic window are use in console for lat/lon/heading and position inputs

## Questions or suggestions?
Visit us on [Discord](https://discord.gg/wkBKgXCHYN), open a topic on the GitHub discussion board, or open an issue.

## BlueSky Integration with BluePlan

This BlueSky fork (`gndpwnd/bluesky`, branch `blueplan`) is customized for use
in the **BluePlan ecosystem**. See
[../docs/ecosystem/BLUESKY_FORK_POLICY.md](../docs/ecosystem/BLUESKY_FORK_POLICY.md)
for the canonical fork/upstream contract (merge base, rebase policy, where
fork-source changes live versus runtime extensions).

### Fork relationship

- **Origin remote:** `git@github.com:gndpwnd/bluesky.git`, branch `blueplan` (tracked by `origin/blueplan`).
- **Upstream remote:** `https://github.com/TUDelft-CNS-ATM/bluesky.git`.
- **Merge base with `upstream/master`:** `6dee8d3a` (last upstream sync). BluePlan-specific commits sit on top of that base.
- **Submodule pin (2026-05-26):** `a652ec4c` (psutil dep + `settings.cfg.template`).

### Fork-source modifications

Patches that used to live in `../patches/` as runtime monkey-patches were
**migrated into the fork source** during the 2026-05-21 ecosystem reset
(commits `45079865` and `92856b8b`). The legacy runtime-patches system has been
retired; there is no `../patches/` directory and `apply_all.py` no longer
exists.

Current BluePlan-specific commits on top of `6dee8d3a`:

| Commit | Change |
|--------|--------|
| `45079865` | BluePlan customizations migrated from runtime patches into fork source |
| `92856b8b` | Additional BluePlan mods migrated into fork source |
| `54a94278` | CRELOG datalog: skip leading simt token (`bluesky/tools/datalog.py`) |
| `efcd2a68` | Heartbeat thread gated behind `BLUESKY_HEARTBEAT` env var (`bluesky/logging_manager.py`) |
| `a652ec4c` | `pyproject.toml`: add `psutil>=5.9` dep; ship `settings.cfg.template` |

### Custom Extensions

BluePlan adds plugins for ADSB data integration:

| Plugin | Purpose |
|--------|---------|
| `adsbfeed_json` | Receive live NDJSON ADSB positions on TCP port |
| `conflictcam` | Screenshot plugin for conflict detection |

Plugins auto-deploy via `./deploy.sh ext` before BlueSky starts.

### Subprocess Integration

BluePlan launches BlueSky as a subprocess with:

- **Scenario file** — `.scn` file generated from web form
- **NDJSON feed** — Live ADSB positions on TCP 10900
- **Screen recording** — H.264 MP4 via `bluesky_screen_recorder` (Xvfb on Linux, native on Windows)
- **Output capture** — CRELOG CSV and run metadata

See [../app/bluesky_runner.py](../app/bluesky_runner.py) for subprocess orchestration.

### Subprocess Lifecycle

```
BluePlan WebUI
    ↓
app/bluesky_runner.py spawns subprocess
    ↓
bluesky_process.py (state machine)
    ├── Launch xvfb (virtual display)
    ├── Start recorder (record to MP4)
    ├── Launch BlueSky with scenario
    ├── Connect NDJSON feed (ADSB Analytics)
    ├── Wait for simulation (timeout: 600s)
    ├── Stop recording (finalize MP4)
    └── Capture output (CRELOG, run_info.json)
    ↓
Output files saved to data/output/{run_id}/
    ↓
AfterSky reads results and displays dashboards
```

### Data Flow

```
BluePlan Form
    ↓
app/bluesky_scenario.py (generate .scn)
    ↓
BlueSky Subprocess (run simulation)
    ├── Read: scenario.scn (commands, aircraft)
    ├── Read: NDJSON ADSB feed (live positions)
    ├── Write: crelog.csv (conflicts)
    └── Write: run_info.json (metadata)
    ↓
AfterSky Analytics (parse CRELOG, show results)
    ↓
Browser Dashboard (visualize outcomes)
```

### Configuration

**Setup-time environment variables:**

```bash
BLUESKY_DIR=./bluesky                    # Path to BlueSky submodule
BLUESKY_HEADLESS=1                       # 1=server mode (Xvfb), 0=GUI
BLUESKY_TIMEOUT=600                      # Simulation timeout (seconds)
BLUESKY_HEARTBEAT=0                      # 1=enable the 30s heartbeat thread (debug); default 0 (off)
RECORD_SCREEN=1                          # Enable screen recording
RECORD_QUALITY=23                        # H.264 QP (lower=better, slower)
RECORD_FPS=5                             # Frames per second
```

`BLUESKY_HEARTBEAT` was added by commit `efcd2a68`: when unset/0 the
heartbeat thread in `bluesky/logging_manager.py` is gated off; set to `1` to
re-enable the periodic liveness log for debug runs. (Heartbeat is also enabled
when BlueSky's internal `debug` flag is on.)

### Runtime dependencies

`psutil>=5.9` is now a **hard dependency** of this fork (declared in
`pyproject.toml` via commit `a652ec4c`). It is consumed by
`bluesky/logging_manager.py` to capture per-process resource samples in the
heartbeat path. Installing the fork via `install.sh` or `pip install -e .`
pulls it in automatically; environments built before the 2026-05-26 submodule
bump may need a `pip install psutil>=5.9` to catch up.

### `settings.cfg.template`

Commit `a652ec4c` also ships `bluesky/settings.cfg.template` at the repo root —
a checked-in template that mirrors the runtime `settings.cfg` BlueSky reads at
import-time. It documents every supported setting (paths, plugin search dirs,
log dirs, networking) and is the canonical reference for the per-machine
`settings.cfg` that gets generated/edited locally.

**TODO**: `install.sh` does **not yet** auto-seed `settings.cfg` from
`settings.cfg.template`. The current install path expects either a manual copy
(`cp settings.cfg.template settings.cfg`) or a pre-existing `settings.cfg`. Add
an idempotent seeding step to `install.sh` in a follow-up.

**Installation:**

```bash
./scripts/setup-bluesky.sh               # Download + install BlueSky
./scripts/setup-bluesky.sh --gui         # With PyQt6 GUI
./scripts/setup-bluesky.sh --headless    # Server mode only (faster)
```

### Troubleshooting

**BlueSky won't start:**
```bash
# Check installation
bluesky --version

# Check scenario file validity
cat /tmp/blueplan_*.scn

# Review logs
tail -f data/logs/bluesky_*.log
```

**Recording failed:**
```bash
# Verify recorder binary
ls -l bluesky_screen_recorder/target/release/

# Check Xvfb
which Xvfb

# Review recorder logs
grep -i record data/logs/*.log
```

**ADSB feed connection failed:**
```bash
# Verify ADSB Analytics running
curl http://localhost:8400/health

# Check network
telnet localhost 10900

# Review relay logs
grep -i adsb data/logs/*.log
```

### Contributing Back

For BlueSky improvements that benefit the broader community:

1. Test changes thoroughly in BluePlan integration.
2. Create pull request against the canonical upstream
   [TUDelft-CNS-ATM/bluesky](https://github.com/TUDelft-CNS-ATM/bluesky) (the
   `upstream` remote on this fork).
3. Reference BluePlan use case in PR description.
4. Once merged upstream, drop the corresponding fork-local commit on the next
   rebase of `blueplan` onto `upstream/master`.

See [../docs/ecosystem/BLUESKY_FORK_POLICY.md](../docs/ecosystem/BLUESKY_FORK_POLICY.md)
for the rebase / cherry-pick policy.

## Contributing to BlueSky

BlueSky can be considered 'perpetual beta'. We would like to encourage anyone with a strong interest in
ATM and/or Python to join us. Please feel free to comment, criticise, and contribute to this project. Please send suggestions, proposed changes or contributions through GitHub pull requests, preferably after debugging it and optimising it for run-time performance.
