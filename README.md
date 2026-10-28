# RT Telemetry UI

The MIT Rocket Team's native **Rocket GNC Monitor v0a** for macOS and Windows. This repository contains the current application, tests, runtime resources, hardware references and build scripts, migrated from `MIT-RT-26_27-Work/ui_work/rocket_UI_v0a`. It has no dependency on that checkout. The application name, version (`0.1.1`), saved-file formats and preferences identity are preserved.

## Station workspaces

The startup wizard chooses **Base station**, **Base station · Launch v2**, or **Away 1–4**, **Telemetry** or **Video**, **Balius** or **Iris**, and **URRG** or a custom site. Remembering choices is optional and off by default. Every normal session begins in **LIVE**, with devices disconnected.

| Station / role | Workspace |
| --- | --- |
| Base / Telemetry | One live workspace without tabs: rocket telemetry/state, GPS, canard/tab readouts, commands, power/recovery and Away communication-route selection. One local telemetry board. |
| Base · Launch v2 / Telemetry | Same launch-site hardware in one window. Rocket state, key metrics and Away route selection stay visible; Flight, Systems, Mission & replay, Network and 3D simulation use tabs. The large antenna illustration is omitted. |
| Away / Telemetry | One window with main rocket telemetry and the local physical or virtual antenna pointer. |
| Away / Video | Two local USB inputs. Balius: Digital + Analog. Iris Away 1–3: Sustainer Digital + Sustainer Analog; Away 4: Sustainer Digital + Booster Analog. |
| Base / Video | Two Balius or three Iris primary channels above the Away thumbnails. Local Digital input only; double-click a thumbnail to replace its matching large channel. Remote video transport remains pending. |

The main **Base station** is LIVE-only: it omits the antenna illustration, simulation/reference comparison, recording, flight-file and replay controls, including their menu actions. **Launch v2** retains the existing simulation, recording and replay tools. The main view places recovery at the upper-right and the rocket-state badge beside Settings; **Canards / Tabs** provides eight channel rows, with unavailable feedback or command protocols explicitly indicated.

Main Base station Balius state displays and Advance State use provisional codes `0–4`: **GROUND_TESTING → PREFLIGHT → FLIGHT → APOGEE → LOW_BATTERY**. Timer annotations are not states, and the codes remain subject to firmware changes. Launch v2 retains its existing state interpretation. Individual Canard/Tab Send controls remain unavailable until the firmware defines them.

Serial ports and cameras cannot be assigned to two active local devices. Video capture/recording works independently of serial telemetry. Settings (**⌘,** / **Ctrl+,**) include the OpenRocket JAR, simulation time limit, recording folder and appearance. Original serial and pointer shortcuts remain available in their applicable roles.

Base has one telemetry board and exclusive uplink authority, with an undefined firmware switch to enable uplink. Its command controls therefore remain locked until the firmware interface is defined. Iris Sustainer/Booster selection is also a labelled firmware placeholder. Base has no local antenna pointer: it selects a future communication route through one of the four Away stations. Wi-Fi selection records the desired Away network and opens operating-system network settings; it does not claim to join or configure the LTU equipment.

## Telemetry, visualization and recorded flights

Simulation and recorded-flight workflows remain available in **Launch v2** and other workspaces that expose those tools; they are absent from the main Base station's live workspace.

- Decode and record the existing telemetry/pointer protocols, including legacy good/rejected-packet CSVs, raw serial data, indexed replay, power/BMS/recovery values and command history.
- Configure launch coordinates using latitude/longitude, MGRS or Plus Codes, and Away antenna locations using latitude/longitude, MGRS or launch-relative heading/distance/height. URRG presets cover the launch pad and all stations; elevations remain manual.
- Run the team OpenRocket fork with manual/imported/online wind and view a georeferenced trajectory, rocket orientation and recorded simulation motor/recovery events. Use a virtual Away pointer for trajectory-following rehearsal.
- Save/open a portable **`.rktflight`** containing the mission, model/motors, reference and available recordings. Recorded flights reopen paused in REPLAY with hardware disconnected.

**Current telemetry roll/pitch/yaw fields are cumulative body-gyro integrals, not Euler attitude.** The compact rocket pane preserves these numeric readouts and shows a muted neutral model labelled **ORIENTATION UNAVAILABLE**. It rotates only when an adapter provides an explicitly validated orientation, or when the operator selects an OpenRocket reference. Current telemetry also lacks confirmed motor-burning/parachute-deployment fields; those remain unknown. The compact view has an upright azimuth slider only. Antenna and full-trajectory views retain orbit/zoom. Away telemetry gives the rocket pane and vertical-profile plot equal height.

The four-station telemetry/video/control transport, LTU metrics, analog receiver tuning and sensor fusion are not implemented. The existing LAN connection exchanges one peer's read-only snapshots and never feeds remote data into hardware control. APRS reception is available for Iris only and uses externally configured Dire Wolf KISS TCP. See the station guide for precise supported and unavailable behavior.

## Run from source

### OpenRocket Mission Simulation

OpenRocket MIT6.5 can launch this application as a simulated Base or Away station.
Select this project and its Python interpreter in OpenRocket's **Mission Simulation
→ Connections**, or select a packaged executable. To attach an already-open app,
choose **Mission Simulation → Prepare OpenRocket connection** here and select the
displayed connection file in OpenRocket. Each station role uses its own connection.
The app opens a separate SIMULATION workspace with software receiver/pointer ports,
native session recording, and optional labeled video slates sent through the normal
encoder/SRT/Base wall. Physical devices remain disconnected in that workspace.

Desktop Mission connections require Qt/PySide **6.12 or newer**; this checkout pins
**6.12.0** in both dependency lockfiles. Update an existing environment with
`make setup` or `uv sync --locked`. Older bindings can retain freed layout-item
wrappers when workspaces are rebuilt; the connection check rejects them. The
OpenRocket bundled headless worker needs no Qt or separate Telemetry UI installation.
See the [Mission Simulation guide](https://github.com/MDNich/ActiveControl_MIT_RktTeam/blob/main/doc/improvements/19-mission-simulation-user-guide.md)
for configuration, ownership, reconnect, recordings and saved-flight experiments.

### Developer environment

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
# Main Base station, without tabs:
.venv/bin/python -m rocket_gnc_monitor --station base --vehicle balius --launch-site urrg --skip-setup
# Secondary tabbed Base station:
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
