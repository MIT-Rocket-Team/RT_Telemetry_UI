# Developer guide

The repository root is the application root. There are no imports from the former monorepo, the original PyQt5 UI or a local OpenRocket checkout. Runtime resources are retained under `resources/`; downloaded engine/runtime files are prepared under ignored `vendor/`.

## Module map

| Area | Main files under `src/rocket_gnc_monitor/` |
| --- | --- |
| Entry point, CLI and smoke checks | `__main__.py` |
| Wizard, station identity, role routing | `startup_wizard.py`, `station_profile.py`, `station_workspace.py` |
| Shared state and lifecycle | `controller.py`, `domain.py` |
| Legacy serial framing and commands | `protocol.py`, `rocket_protocol.py`, `legacy.py`, `devices.py` |
| Legacy CSV decoding | `legacy_sample.py` |
| Recording, replay and portable flight files | `recording.py`, `flight.py` |
| USB video/FFmpeg and Launch video wall | `media.py`, `video_wall.py` |
| Mission coordinates and presets | `location.py`, `location_ui.py`, `site_presets.py` |
| Settings and per-user persistence | `settings.py`, `settings_ui.py` |
| Reference simulation and wind | `trajectory.py`, plus `simulation_bridge/src/RocketBridge.java` |
| Antenna mesh, rendering and virtual movement | `mount_geometry.py`, `widgets.py`, `scene_mesh.py`, `virtual_pointer.py` |
| Rocket geometry, source model and rendering | `rocket_geometry.py`, `rocket_pose.py`, `rocket_pose_view.py`, `rocket_pose_panel.py` |
| Full 3D trajectory scene/playback | `flight_scene.py`, `flight_view.py` |
| Existing instruments and role controls | `ui.py`, `rocket_panel.py`, `rocket_state_badge.py`, `iris_link_panel.py`, `launch_uplink_panel.py`, `launch_link_panel.py` |
| APRS, peer snapshots and Wi-Fi selection | `station_io.py`, `station_panel.py`, `wifi.py`, `wifi_panel.py` |

## Data and thread boundaries

`Mission` stores planning configuration; `Sample` stores decoded telemetry, source/timestamps and optional details. Keep units and provenance explicit. Serial workers frame/decode on their own threads and the controller owns the current accepted state. Video/FFmpeg and the recorder have independent lifecycles. Qt widgets update on the GUI thread. Both Launch windows share one controller and one recording session; constructing another view must not create duplicate transports.

The startup wizard finishes before the controller is constructed. `StationProfile` is immutable for the session. Its historical internal key `base` remains in persistence for compatibility; normal public UI says **Launch station** and maps to the single-board `launch` controller layout. `base`/`legacy` controller layouts remain compatibility APIs, not the normal wizard architecture. Do not remove an old saved-file key merely to rename a label.

LIVE and REPLAY are source modes, separate from station/role. REPLAY cannot construct physical serial transports. Disconnection and invalid/stale telemetry stop tracking; resumption is explicit. A serial write means dispatched, not acknowledged. Never retry uncertain partial command writes automatically. Original command vectors, shortcuts and CSV mapping are regression fixtures in `tests/fixtures/legacy_reference.json`.

Launch owns one local telemetry board and no local physical or virtual pointer. Away owns downlink and pointer workers. Launch-only uplink enable and Iris target switching are disabled firmware placeholders; no invented switch commands are emitted. A selected Away route and received LAN data cannot unlock controls.

## Geometry and coordinate frames

The physical antenna description is defined in **`mount_geometry.py`**, including dimensions, material palette and component attachment groups. `mount_mesh()` caches fixed stand, turntable and elevation-carrier geometry; `widgets.py` applies pose and camera transforms. The dish, Yagi and Avenger housing share the beam through the support pivots. Renderer coordinates are +Y up, +Z north and −X east. Geographic azimuth is clockwise from north. The dish feed arm and boresight share its carrier +Z axis. Geometry is illustrative, not surveyed CAD.

`scene_mesh.py` batches transforms/projection of cached polygons. The antenna animation timer is independent of telemetry arrival; smoothing affects graphics only. Commanded pose is never presented as measured feedback. The legacy pointer protocol has no measured pose, acknowledgement, hardware home or stop opcode. Holding tracking only stops new targets; the last physical move can continue.

Trajectory positions use east/north/up relative to a WGS84 launch origin. Geographic transforms use ellipsoid height; weather uses separately supplied MSL elevation. Keep the legacy physical pointing convention (frozen receiver latitude/longitude, receiver altitude zero, barometric rocket height) separate from mission/reference transforms. Its historical height discrepancy is documented in [PROTOCOL.md](PROTOCOL.md).

The rocket mesh's nose is body **+Z**. Current telemetry `Sample.attitude` values are independent cumulative body-gyro integrals in degrees; the field name is historical. Do not compose them into an Euler rotation or infer orientation from flight phase, climb/descent or a simulation. The compact pane uses a neutral model with **ORIENTATION UNAVAILABLE** unless a decoder provides:

```python
sample.details["orientation"] = {
    "frame": "body_to_ENU",
    "body_axis": "+Z",
    "quaternion_wxyz": [w, x, y, z],
}
```

The validator requires finite scalar-first quaternion components with norm within 0.001 of unity and normalizes accepted rounding error. This is an internal adapter contract, not a new wire protocol. Optional `details["flight_status"]` accepts exact Boolean `motor_ignited`, `motor_burning`, `parachute_deployed`; missing/malformed values remain unknown. Ignition history alone does not establish current burn. State/pyro bits do not prove deployment. OpenRocket reference orientation/events have their own source path and never fill missing telemetry values. There is no onboard-attitude reconstruction or station sensor fusion in the current app.

## Recording and simulation

`recording.py` stores indexed SQLite samples/events, CRC-protected raw chunks, legacy CSVs and finalized per-channel video segments. `flight.py` packs the mission/model/motors/reference and available session assets into checksummed `.rktflight` archives. Saves are atomic. A live snapshot includes committed telemetry and finalized video only; incomplete captures stay marked incomplete. Loading a recorded flight disconnects transports and starts paused REPLAY. Preserve archive/path validation when extending the format.

`trajectory.py` launches the Java bridge as a separate cancellable process with explicit model, motor, wind and site inputs. The job folder retains the request, hashes, log and results. OpenRocket is a nominal reference; controlled canard/tab dynamics are not qualified by the monitor. The private engine preferences do not read the desktop OpenRocket installation. Bridge changes require rebuilding `vendor/rocket-bridge.jar` and package verification against the bundled engine.

## Network and future interfaces

`station_io.py` currently implements Iris-only receive-only uncompressed APRS through KISS TCP and an explicit one-peer LAN snapshot connection for both vehicles. Balius has no APRS controls or active receiver, and its snapshots omit APRS fixes. Snapshots are bounded/versioned/read-only. They remain separate from local telemetry/control; no video, audio or hardware commands traverse this protocol. Wi-Fi selection stores the desired SSID and displays the current OS connection separately; the monitor stores no Wi-Fi password.

`video_wall.py` validates station/channel routing and presents remote slots but starts no network transport. A future transport supplies decoded frames on the GUI thread through `update_remote_frame(station, channel, QPixmap, quality="", received=None)`; `received` is local monotonic receive time. Station IDs are `away1`–`away4`; channel IDs are `digital`, `analog`, `analog2`. Reject unsupported station/channel pairs and call `mark_remote_unavailable` on link loss. Frames age stale after five seconds. Double-click promotion replaces only the matching large channel. Define transport provenance, frame timing and recording semantics before enabling remote feeds.

LTU signal/management, remote pointer actuation, multi-station fusion, analog VRX control and launch-board switch packets need verified interfaces. Add adapters and tests before replacing placeholder status. Source selection does not imply a connected device.

## Tests and build ownership

`tests/` covers protocol vectors and corrupt streams, legacy parity, recordings/flight archives, coordinate conversion, source isolation, station roles, camera/port reservations, video walls, settings/wizard persistence, pose validation and package tooling. POSIX pseudo-terminal tests and Mac-specific packaging tests skip where unsupported. Use native Windows Qt for its full suite. `scripts/verify_package.py` exercises the actual packaged executable with a minimal system PATH.

`requirements-build.txt` and `uv.lock` define dependency preparation; `scripts/build.py` handles engine/runtime preparation, bridge compilation, licensing and native packaging. `packaging/` contains the PyInstaller specification, entitlements and universal launcher source. `scripts/package_macos_universal.py` combines separately verified native Mac apps. [BUILD.md](BUILD.md) lists commands and outputs.

Keep source, tests, essential small fixtures, authored docs and resource provenance in Git. Keep `.venv`, vendor downloads, builds, package outputs, recordings and visual test captures out. A concise current verification record can state source identity, package hashes, actual checks and environment limits without importing years of test artifacts.
