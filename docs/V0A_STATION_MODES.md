# v0a station workspaces and ground-station connections

v0a organizes the monitor by station and operator role. The launch-site computer offers the original two-display **Launch station** layout and the single-display **Base station · Launch v2** layout. Four numbered Away stations each offer Telemetry or Video. Balius has two system video channels; Iris has three, distributed across the stations.

## Startup wizard

Every normal launch asks for:

1. **Station**: Launch station (two displays), Base station · Launch v2 (one display), or Away 1, 2, 3 or 4.
2. **Role**: Telemetry or Video.
3. **Vehicle**: Balius (Digital + Analog) or Iris (Sustainer Digital + Sustainer Analog + Booster Analog).
4. **Launch site**: URRG or a custom location.

**Remember these choices next time** is optional and off by default. Without it, the next launch starts from Launch station / Telemetry / Balius / custom site, unless command-line options preselect other choices. With it, the wizard starts from your saved selections. Uncheck it and finish setup to stop remembering choices. Previously automatic saved profiles do not opt you into remembering choices. Cancel preserves the remembering preference and exits before any device controller is constructed. Finish opens the chosen workspace in LIVE with devices disconnected.

During a session, **File → Forget setup on next launch** clears only the remembered startup choices. The current mission, instruments and other settings remain as they are.

A new mission is named **Balius Launch** or **Iris Launch**, according to the selected vehicle. URRG automatically sets the rocket launch-pad coordinates and, at Away stations, the selected station's antenna coordinates. Launch shows its own site reference but has no local antenna pointer. A custom launch site is entered in the mission editor. Elevations still need to be entered separately. Opening a saved mission or flight restores its own name and locations. The selected station identity appears at the top of the window and remains fixed for that session; relaunch to change it.

**Launch station / Telemetry** opens Flight and Systems windows sharing one mission and controller. Put one on each display. Both windows can be moved and resized on a single display for preparation. The second window creates no duplicate serial workers or recording sessions.

**Base station · Launch v2 / Telemetry** uses the same launch-site hardware configuration in one window. It keeps rocket state, key telemetry and the Away communication-route selector visible while tabs expose supporting instruments. Its Video role uses the existing launch-site video workspace.

**Away / Telemetry** opens one reduced workspace with main rocket telemetry, the compact 3D rocket pose and antenna pointing.

**Away / Video** opens one simplified window with its two assigned USB camera inputs. **Launch station / Video** shows the primary channels and all four Away stations below them; the Launch station has only a local Digital USB camera.

Video does not appear in either telemetry workspace. The video operator does not need to connect a telemetry PCB or pointer board.

`make run` opens the wizard; `make away` preselects Away 1 / Telemetry; `make video VEHICLE=iris` preselects Away 1 / Video / Iris. From the application source directory, for example:

```sh
.venv/bin/python -m rocket_gnc_monitor --site away3 --role video --vehicle iris --launch-site urrg --skip-setup

# Single-display Base station:
.venv/bin/python -m rocket_gnc_monitor --station launch-v2 --vehicle balius --launch-site urrg --skip-setup

# Packaged Mac app:
dist/RocketGNCMonitor-v0a.app/Contents/MacOS/RocketGNCMonitor-v0a --site away3 --role video --vehicle iris --launch-site urrg --skip-setup
```

`--site` accepts `launch`, `away1`, `away2`, `away3`, `away4`; `--role` accepts `telemetry`, `video`; `--vehicle` accepts `balius`, `iris`; `--launch-site` accepts `urrg`, `custom`. The previous `base` argument remains an alias for `launch`, including with the legacy `--station` flag. These flags override the corresponding defaults or explicitly remembered choices. Omit `--skip-setup` to review them in the wizard; include it to start directly with the resolved profile. Adjust the executable path if the Mac app has been installed elsewhere, keeping it inside its complete `.app` bundle.

`--remember-setup` checks the remembering option; `--no-remember-setup` clears it. With `--skip-setup`, these explicitly save the resolved profile or remove the remembered startup choices, respectively. Without either flag, skipping setup leaves the remembering preference and saved choices unchanged. `--station launch|launch-v2|away|video` preselects two-display Launch Telemetry, single-display Base Telemetry, Away 1 Telemetry or Away 1 Video; it does not bypass setup. Smoke-test flags bypass setup, except the dedicated controller-free `--setup-smoke`.

Station layout and data mode are separate decisions:

| Data mode | Input and behavior |
| --- | --- |
| LIVE | Physical connections start closed. Launch connects one telemetry board for downlink reception/polling. Away stations connect their downlink board, pointer and cameras explicitly. Launch selects an Away communication route; the interstation transport and software uplink enable protocol remain pending. |
| REPLAY | A saved flight/session, with playback controls and physical command isolation. |

## Base station · Launch v2: one display

The Base station layout keeps the rocket-state badge, key flight metrics and the Away 1–4 communication-route selector visible above five tabs:

- **Flight**: rocket orientation, vertical profile, ground track, full telemetry and GPS.
- **Systems**: commands, power, battery/BMS, recovery, actuator readouts and GNC plots.
- **Mission & replay**: launch setup, wind, OpenRocket simulation, saved-flight controls and diagnostics.
- **Network**: USB telemetry connection, uplink and Iris target placeholders, Away links and the manual status link, with APRS available for Iris only.
- **3D simulation**: the full reference-trajectory renderer and playback controls.

The large antenna illustration is omitted to make room for the flight instruments; route selection remains available. Supporting panels are reached through tabs instead of a second display. This changes presentation only: Base station still has one local telemetry board, no local antenna pointer, the same URRG site reference and the same uplink and remote-control protocol limitations as Launch station. The original two-display Launch station mode remains available in the wizard.

## Launch station: Flight display

The flight display groups the information used to watch the rocket and select its remote communication route:

- Main telemetry, state, freshness and ground-station/rocket link status, received through one local telemetry board using the existing serial protocol and polling controls.
- Flight plots, reference trajectory, full 3D flight playback and the compact 3D rocket pose.
- Ethernet / PoE topology and a choice of Away 1–4 antenna pointers for communication. Launch has no local physical or virtual pointer connection; remote control and feedback await the interstation protocol.
- Three-axis GNC plots and all actuator channels in a side-by-side matrix.

At Away stations, the antenna controls show current/commanded angles, articulated visualization, manual pointing and trajectory following. The antenna and full trajectory renderers retain their orbit/zoom controls: drag to orbit, scroll to zoom and use their displayed view controls to restore the camera. They animate independently of telemetry arrival. The compact rocket pose uses the separate camera controls described below. Reference attitude, raw telemetry gyro integrals and path-aligned illustrations are labelled according to their source; missing telemetry orientation is explicitly unavailable.

## Compact 3D rocket pose

The Launch and Away telemetry workspaces, and the legacy instrument view, show a 3D rocket in place of the circular attitude horizon. The large centered graphic has its angular readouts, effect indicators and source/status text underneath. Framing fits the body and any visible flame or parachute; rotation alone does not change the scale. Launch places its two angular plots side by side below the taller rocket pane. Away splits the center column equally between the rocket pane and the vertical-profile plot below. Its **View azimuth** slider, from 0° to 360°, changes the viewing direction around the rocket. The camera remains upright; this view has no drag, pan or scroll interaction. Changing the viewing direction does not change the rocket's rotation or send hardware commands. The full trajectory viewer and antenna graphic retain their existing camera controls.

The source selector defaults to **Telemetry** and follows the selected LIVE or REPLAY telemetry stream. The current telemetry roll/pitch/yaw fields are cumulative integrals of the body-axis gyro rates, not three Euler attitude angles. The pane keeps their raw totals as integral readouts, but they do not rotate the rocket. Without a decoded body-to-world orientation, the graphic is neutral and muted with **ORIENTATION UNAVAILABLE**; its upright pose is a placeholder, not a measured vertical attitude. The display never assumes that the rocket points upward because its state is FLIGHT or its altitude is increasing. The GS1/GS2/GS3 test-launch recordings include gyro saturation, so they cannot support reliable reconstruction of absolute attitude from these integrals.

The current telemetry protocol also provides no confirmed motor-burning or parachute-deployment status: those indicators remain **unknown**. Flight phase and pyro-fired flags do not confirm ignition, current motor burn or parachute deployment, and the display does not infer those effects from them.

Choose **OpenRocket reference** to display the loaded simulation's orientation and flight events instead. The simulation already provides valid body-to-ENU quaternions with the nose along body +Z; their interpretation is unchanged by the telemetry correction. This opens or focuses the existing 3D playback controls: embedded in Launch's Flight window, in the optional 3D flight window at Away stations, or in the legacy 3D flight dialog. Use **Play**, the time slider or **Jump to event…** to examine ignition, burnout and recovery. The compact pane shows its reference time and whether playback is paused, playing or following monitor time. A flame follows simulated motor-burning intervals; a parachute follows recorded simulation deployment events. Missing attitude or event information remains labelled unavailable or illustrative. A partial event list without motor or deployment history leaves the corresponding effect unknown. Selecting a reference never fills gaps in the Telemetry source with simulation values.

For simulation playback without hardware, load or generate an OpenRocket reference containing flight events, select **OpenRocket reference**, leave **Follow monitor time** unchecked (**Monitor time** in the embedded Launch view), and use its playback controls. This operates the visualization only. Switching back to **Telemetry** restores its raw integral readouts and own status information; current telemetry input returns to **ORIENTATION UNAVAILABLE**.

## Launch station: Systems display

The companion display exposes the supporting information and controls:

- Full rocket telemetry/GPS, servo and cell readouts, rocket commands, power rails, BMS protections and recovery controls supplied by the telemetry wire contract. Launch alone has uplink authority on its telemetry board. The software uplink enable/disable command is a firmware placeholder; live rocket commands remain unavailable until that protocol is defined.
- Mission launch location, OpenRocket configuration/reference and wind information. Away station missions additionally configure their local antenna positions.
- Station network connections and status, with APRS available for Iris only.
- Session recording/replay and diagnostic events.

The large **Rocket state** badge in the Systems window's upper-right corner follows the telemetry state machine: green **GROUND_TESTING**, blue **PREFLIGHT**, red **FLIGHT**, and orange **POST_APOGEE**, **MAIN** or **END**. Unknown states and missing telemetry are black. The smaller caption identifies LIVE or REPLAY; stale live telemetry retains its last state with an explicit **STALE** label. The badge does not infer state from the simulation and does not indicate that commands are enabled.

Monitoring information stays visible across the two windows. Configuration editors, confirmation dialogs and file selectors remain deliberate actions. Legacy functions retain their meaning even when their controls move to a different section.

The target layout is two 1920 × 1080 displays (about 1920 × 1020 usable window space). Fixed instrument tables show every normal channel/field; long event history, raw JSON and arbitrary-length wind profiles scroll inside their visible panels. **View → Arrange station displays** places each window on an available screen. With one screen, both launch windows remain available for preparation; Away and Video modes each use one window. Closing the primary Flight window shuts down the entire workspace. Closing Systems hides that window; **View → Arrange station displays** reopens it. On macOS, **⌘W** closes the active window or dialog and **⌘Q** ends the entire workspace. Shortcuts work from either display.

## Video role

### Away station

Both assigned channels remain visible together. Balius uses **Digital + Analog** at every Away station. Iris uses **Sustainer Digital + Sustainer Analog** at Away 1–3, and **Sustainer Digital + Booster Analog** at Away 4. Each has its own camera selector, **Find**, **Start**, **Stop**, local video **File…** input and capture status. A camera selected or in use by one channel is excluded from every other channel, including while a previous capture is still closing. No telemetry or pointer board connection is required.

### Launch station

Two or three large primary channels sit above all four Away stations. There are eight thumbnail slots for either vehicle: each station contributes its assigned Digital/Analog pair. Iris’s large panes are Sustainer Digital, Sustainer Analog and Booster Analog; the Booster thumbnail exists only under Away 4. The Launch station exposes one local USB camera selector, for **Digital** only. Digital initially shows that local source; Balius Analog and Iris Sustainer Analog initially select Away 1; Iris Booster Analog initially selects Away 4.

**Double-click any Away thumbnail** to replace its matching large channel. For example, Away 3 Sustainer Analog replaces only the large Sustainer Analog view. Away 4 Booster Analog replaces only Booster Analog. Digital promotion replaces only Digital. Every other thumbnail stays visible, including lower-quality alternatives. **Use local Digital** returns the large Digital view to Launch station's USB receiver; promotion never closes that camera.

The inter-station video connection has not yet been specified or implemented. Remote views therefore start with **Awaiting station link**, not simulated live pictures. Their presentation API already supports quality text, receive age, stale frames and loss of connection. Choosing a thumbnail selects its source even when it has no frame; it does not establish a network connection. The existing telemetry LAN feature does not carry video. Remote pictures injected through the future transport are display-only today; remote recording/replay is not implemented.

The supplied routing diagrams establish the video and vehicle assignments. The revised station architecture uses four LTU-XR links for Away-to-Launch transfer alongside one local telemetry board at Launch. That board is connected from the Telemetry workspace; the Video workspace retains its dedicated camera interface. The [Iris diagram](../references/IRIS_STATION_ROUTING.png) adds stage-specific routing, summarized below. The stage names label receiver assignments; the application does not infer a vehicle identity from legacy telemetry packets.

### Recording and playback

The simplified screen hides serial connections, rocket readouts, antenna controls and mission panels. The data source remains visible so LIVE and recorded REPLAY are distinguishable. Local USB channels retain independent capture, segmented recording and replay. Once frames arrive, **Start recording** can create a video-only session. In REPLAY, a compact footer exposes flight/session loading, play, pause, seek and speed. The Launch station replays the local Digital channel from an archive; inactive analog recordings remain preserved in the archive.

Settings remain available with **⌘,** / **Ctrl+,**. Quitting closes active workers. Normal station profiles are fixed for the session; the legacy programmatic layout-switching API retains its connection-preserving behavior for existing integrations.

## Iris receiver assignments

| Station | Local video receivers | Telemetry assignment | APRS assignment |
| --- | --- | --- | --- |
| Away 1–3 | Sustainer Digital; Sustainer Analog | Sustainer | Sustainer |
| Away 4 | Sustainer Digital; Booster Analog | Booster | Booster |
| Launch station | Sustainer Digital | Sustainer or Booster through one board | Relayed from Away stations |

The wizard and station header identify the telemetry assignment. Each Away station has one downlink board; that board selects one Iris stage at a time. Launch also has one local telemetry board and alone has uplink authority. Its Sustainer/Booster selection is shared by downlink and uplink, so there are no independent targets or second serial board. The existing connection/polling receives downlink telemetry. Target-switch commands, acknowledgements and software uplink enable/disable are not defined; the application does not claim to know the physical target or uplink state. APRS remains one external Dire Wolf KISS input with an optional callsign filter; set the appropriate vehicle callsign. Automatic stage identification and Away-to-Launch APRS relay are not yet implemented. The existing read-only one-peer telemetry LAN is separate from the planned multi-station data/comms transport.

## Iris board target and Launch uplink (placeholders)

The Iris Telemetry workspace displays one Sustainer/Booster selector for its telemetry board. At Launch, this is the common board target for both downlink and uplink. Away stations have no uplink authority or transmit controls. Initial desired targets follow the station assignment (Booster at Away 4, Sustainer elsewhere). Choosing a remote Away antenna pointer remains a separate communication-route choice and does not actuate the board's stage switch.

These controls are explicitly marked **PLACEHOLDER** because the firmware commands and acknowledgements are not defined. A dropdown is only a desired target, never a confirmed hardware target. In LIVE and REPLAY, the hardware target remains unknown and target-switch actions are disabled. Launch software uplink enable/disable likewise remains unavailable until its protocol is defined. No target-switch or uplink-enable bytes are invented or transmitted. Live rocket commands are locked for both vehicles until the Launch uplink enable contract is implemented; Iris also needs its common board-target contract.

## Ground-station circuit-board mapping

The current station architecture uses the paths below. Earlier supplied block diagrams remain reference material. USB device selection does not identify devices automatically by their cable position; use the port/camera names and verify each physical unit during setup.

| Hardware/data path | Monitor integration |
| --- | --- |
| Telemetry board → USB serial | One board at Launch and one at each Away station. Existing receive/polling. A USB connection and recent valid rocket telemetry are separate status conditions. |
| Launch telemetry board → uplink | Launch only, on the same board and serial connection used for downlink. Software enable/disable is a disabled firmware placeholder; live command transmission stays blocked. |
| Antenna pointer board → USB serial | Away stations only. Independent pointer commands. Once a serial port is connected to one board, it disappears from the other board's available ports. |
| Digital video receiver → USB camera capture | Digital video channel. |
| Analog video receiver → USB camera capture | Away-only Analog channel (Sustainer at Iris Away 1–3, Booster at Iris Away 4). A camera selected for one channel is excluded from all other active selectors. |
| APRS radio audio → computer audio/Dire Wolf → KISS TCP | Iris-only receive-only uncompressed APRS positions from an external Dire Wolf decoder, default `127.0.0.1:8001`. |
| Launch Ethernet / PoE → four LTU-XR links → Away LTU-XR and local WLAN | Away Wi-Fi selection and Launch communication-route selection are available. Selecting a network or route does not establish the future application transport. |
| Existing direct station LAN | Explicit read-only station status/telemetry exchange with one peer, default TCP `8765`. This is not the planned four-Away-station transport. LTU management readings require its equipment protocol. |

The Digital/Analog names refer to the rocket-to-ground radio video paths, not to the computer input type: the local computer inputs are USB cameras. The video computer can capture either feed without connecting the telemetry board. Once frames arrive, the recording control can create a video-only recording; the absence of a telemetry board does not prevent this. Live rocket commands remain locked at Launch while the software uplink enable protocol is undefined. Downlink reception and polling at Launch and Away stations remain independent of this uplink placeholder.

Unknown analog VRX control protocols and equipment-management interfaces are not represented as working controls or fabricated telemetry. An unavailable/unsupported status means the monitor has no verified value or command contract for that function. Existing Away pointer commands and downlink polling remain available through their established serial connections.

The [source diagram and implementation notes](../references/GROUND_STATION_BOARD_NOTES.md) record what is supported and which suggestions still require device protocols or additional work. Online wind retrieval needs internet access; manual and imported wind remain available offline.

## APRS and station LAN

At Away stations, the Wi-Fi panel lets you choose a saved network or type an SSID. **Select Wi-Fi** saves the desired network for this station; it does not join the network. **Refresh** reads current/saved network names from the operating system, and **Open Wi-Fi settings** opens the system controls for securely joining it. The current OS connection is shown separately from the selected SSID, so a saved selection is never presented as a successful connection. Join the Away station's local WLAN before using any data connection. No Wi-Fi password is stored by the monitor.

At Launch, the **Communication / Away antenna selection** panel shows the Ethernet / PoE topology and all four LTU-XR paths. Choose **Away station 1–4 antenna pointer** from **Choose an away-station pointer…** to set the desired communication route. Selection does not join a link, send rocket commands, move a pointer or imply live pointer feedback. Link status and signal strength remain unavailable until the bridge management and interstation data protocols are provided. Launch has no local physical or virtual pointer controls; its serial connection is for the telemetry board.

The **Station network** panel keeps network connections separate from the serial boards and USB video. APRS controls and receiver activity are available only for Iris; Balius retains the station LAN link without APRS.

For Iris APRS, run Dire Wolf externally with the radio audio input configured and KISS TCP enabled. Set its host and port in the APRS row (defaults `127.0.0.1`, `8001`), optionally enter a complete callsign including SSID, and choose **Connect**. A blank filter shows the latest supported position from any station. The panel shows connection state, callsign, latitude/longitude, optional altitude and age. Unsupported packets do not become positions.

The decoder accepts AX.25 UI uncompressed APRS positions (`!`, `=`, `/`, `@`); compressed positions, Mic-E, objects, NMEA and ambiguous positions are not supported. APRS `/A=` altitude is converted from feet to metres, but its datum is not inferred. APRS fixes are read-only recovery information; they do not replace flight telemetry or actuate the antenna. Dire Wolf itself is not bundled or configured by the monitor, and the monitor never transmits APRS.

For a local station link:

1. Join both computers to the ground-station AP/LTU network.
2. On one computer, set the LAN port (default `8765`) and choose **Listen**. The listener binds all local IPv4 interfaces and accepts one peer.
3. On the other computer, enter the listening computer's LAN IP and matching port, then choose **Connect**.
4. Check the peer's displayed station name, source mode, rocket-link state, altitude, phase and sample age. Choose **Stop** to close the connection.

Both sides exchange bounded, versioned, read-only snapshots about twice per second. Snapshot data includes selected telemetry, pointer state, link states and, for Iris only, an available APRS fix; the panel shows a compact peer summary. Remote data stays separate from local telemetry and cannot enable or issue rocket/pointer commands. The link carries no video, audio or calling service. It is a direct local TCP connection and does not require an internet service.

Host/port/filter choices are stored in `station_network.json` in the application data directory. Connections always start off on a new launch and are closed when the workspace exits. A disconnected APRS fix is labelled as the last fix, and disconnected peer data is removed rather than being presented as live.

## Mission and antenna positions

The mission stores launch and antenna locations for simulation and virtual rehearsal. Physical pointer tracking retains the legacy frozen ground-receiver GPS convention and Send to AntPtr action. Launch entry accepts decimal latitude/longitude, MGRS or Plus Codes, with URRG as a preset. Antenna entry accepts decimal latitude/longitude, MGRS or a position relative to launch.

Choosing **URRG** in the startup wizard sets the launch pad to `18TUN2061530290` and, at Away stations, the antenna to the preset for the selected station. Launch shows its coordinate as a site reference and does not create a local pointer location:

| Station | MGRS location |
| --- | --- |
| Launch station reference | `18TUN2063730181` |
| Away 1 | `18TUN2177730106` |
| Away 2 | `18TUN2291333237` |
| Away 3 | `18TUN2015031101` |
| Away 4 | `18TUN2259229678` |

At Away stations, the mission editor offers the Away presets under **Antenna pointer** when the launch site is URRG, identifying the current station. Selecting a preset changes the horizontal antenna position and preserves its entered altitude. Launch uses its communication-route selector instead of a local pointer editor. The references contain no elevation: a new mission starts with zero elevations, so enter the launch and relevant Away antenna elevations for simulation or trajectory following. The presets do not calibrate a pointer or send commands to hardware. Saved missions and `.rktflight` files keep their own locations and names; startup defaults do not replace them when opened.

For relative entry, specify the heading **from the antenna to the rocket at launch**, horizontal distance and altitude difference using the signs shown in the editor. Check the resolved coordinates before following a trajectory. Save the mission in a `.rktflight` file to preserve the model, motors, reference and available recording with it.

To rehearse without hardware at an Away station: load or simulate a reference, select the virtual pointer, connect it and choose Follow trajectory. Manual pointer controls and the original keyboard shortcuts also operate the virtual mount. No physical pointer feedback is implied by the model: legacy firmware does not acknowledge moves or report measured pose.

## Developer integration

`station_profile.py` owns the immutable `StationProfile` and its validated, atomic JSON persistence, including the launch-site choice. The internal station key `base` is retained for saved-profile compatibility; public `launch` values normalize to that key and display as Launch station. The optional `display_mode="launch_v2"` keeps that hardware identity but labels it Base station and selects the single-window telemetry layout. The default `standard` display mode is omitted from saved JSON for compatibility, and Away profiles reset it to `standard`. Explicitly remembered startup choices use `startup-choices.json` in the application data directory; legacy `station-profile.json` and `station-layout.json` do not seed startup defaults. `startup_wizard.py` is a controller-free four-page Qt wizard; `__main__.py` resolves opted-in/CLI defaults and waits for acceptance before constructing `StationWindow`. Startup configuration supplies the new mission's vehicle-based name and optional URRG launch/Away-pointer positions; loading an existing mission or flight remains authoritative. Smoke flags bypass interactive setup, while `--setup-smoke` only renders the wizard.

`station_workspace.py` assembles the role-specific cards over one inherited instrument owner. An explicit profile fixes identity and role for the window lifetime. Video role gates hidden serial menu actions and connection callbacks; it keeps recording shortcuts and file/replay controls. Telemetry retains the original shortcuts and device gating. The legacy constructor without an explicit profile supports prior layout-switching integrations.

The compact rocket-pose model and renderer keep telemetry and OpenRocket frames separate. In the current telemetry decoder, `sample.attitude` holds cumulative body-gyro integrals; the historical field name does not make them Euler attitude. Preserve these values for readouts and plots without composing them into a rotation. A future decoder can supply genuine orientation through `sample.details["orientation"] = {"frame": "body_to_ENU", "body_axis": "+Z", "quaternion_wxyz": [w, x, y, z]}`. This explicitly maps the rocket's +Z nose into east/north/up using a scalar-first quaternion. Missing or invalid orientation leaves the neutral model labelled unavailable. The current decoder has no such orientation field, and this correction adds no sensor fusion or reconstruction from gyro integrals.

A future decoder may also provide an optional `details.flight_status` object with exact Boolean fields `motor_burning`, `motor_ignited` and `parachute_deployed`; missing or non-Boolean values remain unknown. `motor_ignited` records historical ignition and does not by itself indicate a currently burning motor. Only explicit burning/deployment evidence controls telemetry flame/parachute effects. Both objects are internal decoded-data contracts: they introduce no firmware opcode or serial wire support, and the present telemetry decoder supplies neither. Simulation poses instead use the selected OpenRocket frame and its event metadata, preserving unavailable-attitude and path-aligned-illustration labels.

`controller.py` accepts a `board_layout` and vehicle profile. Launch creates one local telemetry worker; Away creates downlink/pointer workers. The legacy default preserves the previous combined ground-board API. The Launch telemetry worker uses the established downlink connection and polling path. Launch live uplink stays unavailable until its software enable protocol is specified; remote pointer actuation separately awaits the station transport. Incoming read-only LAN data cannot enable either. `iris_link_panel.py` presents one shared board target at Launch and a downlink target at Away stations, with disabled target-switch controls until the firmware interface is defined.

`controller.py` also accepts an immutable `video_channels` tuple. Its per-channel states drive worker lifecycle, reservations, recording and replay; `ui.py` constructs matching local controls from `video_labels`. Unknown/inactive channels are rejected. Existing two-channel defaults and missing-stream-as-Digital recordings remain compatible. `recording.py` and `flight.py` preserve arbitrary channel directories without an archive schema change. Digital-only replay filters inactive channels without deleting their archived assets.

`video_wall.py` uses `StationProfile.away_channels` to validate every remote station/channel combination; unsupported combinations are rejected. It owns presentation and source selection only. A future transport should deliver decoded frames on the Qt GUI thread through `update_remote_frame(station, channel, QPixmap, quality="", received=None)`. Station IDs are `away1`–`away4`; channels are `digital`, `analog`, and Iris-only `analog2`. `received`, when supplied, must be local monotonic receive time. Use `mark_remote_unavailable(...)` on link loss. Five seconds without a fresh frame is labelled stale; quality strings are transport-provided and not inferred from image appearance. `set_local_source(label, live=False)` supplies truthful local file/replay context and avoids falsely ageing paused recordings as a stale live camera. `select_remote(...)` promotes the matching channel, and `select_local_digital()` restores Launch USB. `set_local_frame(...)` updates local Digital without overwriting a promoted remote view.

The wall starts no network workers. It does not subscribe to the existing telemetry LAN protocol, relay frames, record remote frames or claim a live receiver connection. Add the transport and its recording semantics after the station interface is specified. Tests exercise source promotion, stale state, image aspect ratio, channel exclusivity, all three local recordings, profile validation, wizard cancellation and role isolation.

## Deployment alongside v0

The Mac application is `RocketGNCMonitor-v0a.app`, bundle identifier `edu.mit.rocketteam.gncmonitor.v0a`, version `0.1.1`. It can coexist with the original v0 application and keeps separate preferences. The Windows executable/package has the same `-v0a` suffix. Keep the full app/package together; do not extract and move only its executable.

Packages target macOS 14 or newer (Apple Silicon and Intel in the universal distribution) and Windows x64. Intel execution is checked through Rosetta on Apple Silicon; the Windows build is checked in the supplied Windows 11 ARM VM using x64 emulation. These checks do not establish physical Intel/Windows hardware or receiver compatibility. The operator controls and original keyboard shortcuts should be checked with the connected equipment before field use. See the [build guide](BUILD.md) and [universal build guide](MAC_UNIVERSAL.md) for platform checks and limitations.
