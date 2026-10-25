# RT Telemetry UI

The MIT Rocket Team's native **Rocket GNC Monitor v0a** for macOS and Windows. This repository contains the current application, tests, runtime resources, hardware references and build scripts, migrated from `MIT-RT-26_27-Work/ui_work/rocket_UI_v0a`. It has no dependency on that checkout. The application name, version (`0.1.1`), saved-file formats and preferences identity are preserved.

## Station workspaces

The startup wizard chooses **Launch station**, **Base station · Launch v2**, or **Away 1–4**, **Telemetry** or **Video**, **Balius** or **Iris**, and **URRG** or a custom site. Remembering choices is optional and off by default. Every normal session begins in **LIVE**, with devices disconnected.

| Station / role | Workspace |
| --- | --- |
| Launch / Telemetry | Two coordinated windows: Flight and Systems. One local telemetry board, rocket telemetry/state/GNC, simulation, recording/replay, power/recovery, diagnostics and remote antenna-route selection. |
| Base · Launch v2 / Telemetry | Same launch-site hardware in one window. Rocket state, key metrics and Away route selection stay visible; Flight, Systems, Mission & replay, Network and 3D simulation use tabs. The large antenna illustration is omitted. |
| Away / Telemetry | One window with main rocket telemetry and the local physical or virtual antenna pointer. |
| Away / Video | Two local USB inputs. Balius: Digital + Analog. Iris Away 1–3: Sustainer Digital + Sustainer Analog; Away 4: Sustainer Digital + Booster Analog. |
| Launch / Video | Two Balius or three Iris primary channels above the Away thumbnails. Local Digital input only; double-click a thumbnail to replace its matching large channel. Remote video transport remains pending. |

Serial ports and cameras cannot be assigned to two active local devices. Video capture/recording works independently of serial telemetry. Settings (**⌘,** / **Ctrl+,**) include the OpenRocket JAR, simulation time limit, recording folder and appearance. Original serial and pointer shortcuts remain available in their applicable roles.

Launch has one telemetry board and exclusive uplink authority, with an undefined firmware switch to enable uplink. Its command controls therefore remain locked until the firmware interface is defined. Iris Sustainer/Booster selection is also a labelled firmware placeholder. Launch has no local antenna pointer: it selects a future communication route through one of the four Away stations. Wi-Fi selection records the desired Away network and opens operating-system network settings; it does not claim to join or configure the LTU equipment.

## Telemetry, visualization and recorded flights

- Decode and record the existing telemetry/pointer protocols, including legacy good/rejected-packet CSVs, raw serial data, indexed replay, power/BMS/recovery values and command history.
- Configure launch coordinates using latitude/longitude, MGRS or Plus Codes, and Away antenna locations using latitude/longitude, MGRS or launch-relative heading/distance/height. URRG presets cover the launch pad and all stations; elevations remain manual.
- Run the team OpenRocket fork with manual/imported/online wind and view a georeferenced trajectory, rocket orientation and recorded simulation motor/recovery events. Use a virtual Away pointer for trajectory-following rehearsal.
- Save/open a portable **`.rktflight`** containing the mission, model/motors, reference and available recordings. Recorded flights reopen paused in REPLAY with hardware disconnected.

**Current telemetry roll/pitch/yaw fields are cumulative body-gyro integrals, not Euler attitude.** The compact rocket pane preserves these numeric readouts and shows a muted neutral model labelled **ORIENTATION UNAVAILABLE**. It rotates only when an adapter provides an explicitly validated orientation, or when the operator selects an OpenRocket reference. Current telemetry also lacks confirmed motor-burning/parachute-deployment fields; those remain unknown. The compact view has an upright azimuth slider only. Antenna and full-trajectory views retain orbit/zoom. Away telemetry gives the rocket pane and vertical-profile plot equal height.

The four-station telemetry/video/control transport, LTU metrics, analog receiver tuning and sensor fusion are not implemented. The existing LAN connection exchanges one peer's read-only snapshots and never feeds remote data into hardware control. APRS reception is available for Iris only and uses externally configured Dire Wolf KISS TCP. See the station guide for precise supported and unavailable behavior.

## Run from source

Use a native 64-bit Python 3.12 or 3.13 environment. From the repository root:

```sh
make setup PYTHON=python3.13
make run
make test
make lint
```

Windows can use `py -3.13 scripts/build.py setup`, then `.venv\Scripts\python.exe -m rocket_gnc_monitor`. Use x64 Python, including on Windows ARM. No Java installation is needed for telemetry-only source operation; simulation preparation requires the engine and Java runtime described in the build guide.

Preselect the wizard or bypass it explicitly:

```sh
.venv/bin/python -m rocket_gnc_monitor --site away3 --role telemetry --vehicle iris --launch-site urrg --skip-setup
# Single-display Base station:
.venv/bin/python -m rocket_gnc_monitor --station launch-v2 --vehicle balius --launch-site urrg --skip-setup
```

Use `--site launch|away1|away2|away3|away4`, `--role telemetry|video`, `--vehicle balius|iris` and `--launch-site urrg|custom`. Omit `--skip-setup` to review the selections. The old `base` argument remains a compatibility alias. **File → Forget setup on next launch** clears saved wizard choices without changing the current session.

## Build self-contained packages

Build each native package on its target OS/architecture. Build machines need Python, JDK 17+ and optionally GNU Make; recipients need only the complete package. The build embeds Python, Qt, Java, OpenRocket and FFmpeg.

```sh
# macOS, after make setup:
make -f Makefile.macos all OPENROCKET_DOWNLOAD=v6.2
# Windows with GNU Make, after setup:
make -f Makefile.windows all OPENROCKET_DOWNLOAD=v6.2
```

Use `OPENROCKET_JAR=/path/to/OpenRocket-MIT-v6.2.jar` for a local engine or `OPENROCKET_DOWNLOAD=latest` to resolve the latest stable team release. Pin a tested release for repeatable builds. macOS packages require macOS 14 or later; native ARM64 and Intel payloads can be combined into the universal distribution. Windows packaging targets x64. Keep the entire extracted Windows folder together.

Generated environments, downloaded vendor runtimes, package outputs and verification captures are ignored. They are rebuilt locally instead of being committed. Historical planning documents, old validation screenshots and the superseded v0 LaTeX report remain in the original repository.

[Migration validation](docs/MIGRATION_VALIDATION.md) · [Station setup and current capabilities](docs/V0A_STATION_MODES.md) · [Operator reference](docs/OPERATOR_GUIDE.md) · [Developer guide](docs/DEVELOPER.md) · [Build guide](docs/BUILD.md) · [macOS universal build](docs/MAC_UNIVERSAL.md) · [Wire protocol](docs/PROTOCOL.md) · [Legacy parity and shortcuts](docs/LEGACY_PARITY.md) · [Recording test fixtures](docs/RECORDING_FIXTURES.md) · [Third-party notices](docs/THIRD_PARTY_NOTICES.md)
