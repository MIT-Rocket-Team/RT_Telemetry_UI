# Operator guide

The startup wizard selects Base or Away 1–4, Telemetry or Video, Balius or Iris, and launch site. Main Base Telemetry shows its instruments in one window without tabs; the optional secondary `launch-v2` layout retains tabs. Away Telemetry is a reduced single-window layout. Video has its own workspace. Remembering setup is optional and off by default. See [station modes](V0A_STATION_MODES.md) for receiver routing, URRG presets and Iris-only APRS.

## Test two station computers

1. Put both computers on the same station LAN. Start one as **Base Station / Telemetry** and the other as **Away Station 1 / Telemetry**, selecting the same vehicle.
2. Open **Network…** at Base. Choose **New mission**, then **Apply network configuration**. Copy its mission UUID.
3. At Away, open **Network…**, paste that UUID and apply. Alternatively select the discovered Base row and use **Join selected station's mission**. Mission names and Wi-Fi names do not join missions. A UUID is intentionally not restored on the next launch.
4. Discovery should show both applications online. If multicast is blocked, enter the other computer's displayed HTTP address under **Manual peer addresses** on **both** computers and apply. Use the actual LAN address, not `127.0.0.1`. Allow the application through the private-network firewall and macOS Local Network permission prompt.
5. At Away, connect the telemetry board and start polling. At Base, the GS1 tile shows network round-trip time and remote sample age. Select **GS1 · network · READ ONLY** as the telemetry display source to populate the plots, rocket state and instrument tables. Switch back to **Local telemetry board** for local commands. Remote samples never enable local hardware commands or pointer tracking.
6. For video testing, start the applications in their **Video** roles, join the same mission, and select USB cameras at Away. Base receives thumbnails automatically. Double-click a thumbnail to request main quality for its matching channel; the previous large view remains until the replacement decodes. Base's local digital camera remains independently selectable.

The same two computers can test telemetry and video sequentially. Running two roles on one computer requires separate `--data-dir` folders and `--network-port` values; each process owns a separate installation identity and must use distinct USB devices.

Command-line example for a shared mission (replace the sample UUID and LAN addresses):

```sh
RocketGNCMonitor-v0a --site base --role telemetry --vehicle balius --skip-setup \
  --mission-id 33333333-3333-4333-8333-333333333333 --peer http://192.168.1.12:8766
RocketGNCMonitor-v0a --site away1 --role telemetry --vehicle balius --skip-setup \
  --mission-id 33333333-3333-4333-8333-333333333333 --peer http://192.168.1.11:8766
```

On Mac the executable is inside `RocketGNCMonitor-v0a.app/Contents/MacOS/`; on Windows use `RocketGNCMonitor-v0a.exe`. `--no-discovery` uses manual peers only; `--no-network` starts without the service. Network discovery does not open serial ports or cameras.

## Assign devices and send station locations

Open **Settings → Station assignments & locations…** at Base. Each discovered computer is identified by its persistent node UUID, address and role. Select the exact computer, assign GS1–GS4, and enter latitude/longitude, MGRS or a Plus Code plus WGS84 ellipsoid altitude. Send the update and check the applied acknowledgement. Configure the telemetry and video computers for a station separately; they are separate devices even when their GS number matches.

Both devices must already share a mission, and the Away device must have discovered or manually connected to the Base application. A station-number change restarts the remote station view and disconnects its local devices; reconnect those inputs afterward. A location-only update pauses pointer tracking and updates the station's mission coordinates. Review the position before resuming tracking. The assignment is recorded against the device UUID; it never merges conflicting publishers silently. Saved station locations are restored only when explicitly joining their matching mission and GS identity.

## Reading network health

The two GS bars remain distinct: the LTU-XR path bar uses measured **application round-trip time**, not radio RSSI or a direct LTU management reading. Rocket age is a lower bound derived from source-local timing plus elapsed receive time; the computers' monotonic clocks are never subtracted. GS titles use green below 250 ms, orange below 1 s, red at 1 s or older/no sample, and gray when the station link is absent. Separate T/V labels show telemetry/video application status: stale after 3 s and offline after 10 s. The network dialog exposes mission mismatches, duplicate identities, rejected peers and archive errors.

Accepted observations are archived independently of the UI under the application data directory's `network-observations/`. Exact local wire bytes and complete remote events are retained with their source identity. This does not estimate a fused trajectory or average duplicate packets. Physical USB/radio commands remain local; network command forwarding is not enabled.

Lucida Grande Regular/Bold is included in the application package and loaded privately on every platform. No font installation is needed.

## Startup and connection control

A normal session opens LIVE with no automatic serial connection or camera capture. Link indicators separate computer-to-telemetry-board USB from rocket-to-ground radio reception; Away additionally shows its local pointer USB connection. Ground board connected means its serial port opened. Rocket telemetry live means a checksum-valid LIVE packet arrived within the mission's freshness limit; no valid packet yet shows Waiting, and expired reception shows Lost / stale. The protocol has no separate radio handshake. Reconnecting the ground board requires a new packet before the radio can be shown live.

Connect each board independently at 115200 baud. Connecting the ground board opens its port; click **Start Polling** to receive telemetry. **Stop Polling** pauses reception while leaving the serial port open. **Start Logging** creates a session immediately when eligible telemetry or video is available. At Away stations, manual pointer controls require the pointer board independently. Launch alone has uplink authority on its one telemetry board, but LIVE rocket commands remain locked while the software-enable protocol is undefined. The control panel distinguishes connected/polling stopped, waiting, live reception, and stale reception.

## Away virtual antenna pointer and trajectory rehearsal

1. Open **Mission → Configure… → Antenna pointer**. Choose latitude/longitude, **MGRS**, or **Relative to launch**, then check **Antenna location established** and save. The position persists in mission JSON and portable flight files.
   - Latitude/longitude and MGRS use a separate WGS84 ellipsoid altitude. MGRS accepts spaces and lowercase and shows the resolved coordinates and grid precision.
   - **Relative to launch** requires an established launch origin. Enter the true heading **from the antenna toward the rocket on its launch pad**, horizontal distance (0–100,000 m), and **antenna altitude minus launch altitude** in metres. Positive means the antenna is above the pad; negative means below. For example, heading 90°, distance 500 m and height +5 m places the antenna west of the pad and 5 m higher.
   - Relative offsets remain attached to the mission launch site: changing the launch coordinates or ellipsoid altitude recalculates the antenna position. The preview shows the resolved WGS84 position. Existing saved missions default to latitude/longitude entry.
2. In **Mission & wind**, run OpenRocket or import a georeferenced, launch-relative ENU trajectory. The reference's stored origin is used for tracking; the antenna may be offset from the launch site.
3. Stay in **LIVE**. Choose **Virtual antenna pointer** in the pointer connection dropdown and click **Connect**, or use **Connect virtual pointer** on the Antenna pointer page. No ground station is needed.
4. Use Send, UP/DOWN/LEFT/RIGHT and ZERO to actuate the simulated mount. The original keyboard shortcuts work. The displayed pose slews toward the target at illustrative rates of 90°/s azimuth and 60°/s elevation.
5. Choose **Follow trajectory**. Pause/resume, Rewind, the time slider and 0.25×–4× speed control the rehearsal. Manual movement pauses playback. **Hold virtual pointer** freezes the simulated motion and clock. The Flight overview shows the reference with a gold virtual target and purple antenna marker, separately from telemetry.
6. Disconnect the virtual device when finished. Changing source mode closes it. Changing the antenna location or reference stops rehearsal and requires an explicit restart.

The connection, pose and banner are labeled **VIRTUAL / SIMULATED**. Rehearsal never opens a serial port, generates telemetry or unlocks ground-station/rocket controls. Its kinematics illustrate motion, not calibrated hardware dynamics. At a coincident antenna/target position the direction is undefined; at exact zenith it retains the previous azimuth. Physical pointer tracking keeps the legacy **Send to AntPtr** ground-GPS workflow. Simulation playback/connection state is temporary and is not restored when loading a flight.

## 3D flight playback

Open **View → 3D flight…**, or **3D…** beside the Flight overview trajectory selector. Run OpenRocket or import a reference first. The new window shows the simulation trajectory, an enlarged schematic rocket with fins/canards, an ignition plume during recorded motor burns, and a parachute at recorded deployment. Camera orbit is independent of the rocket's attitude.

- Drag to orbit, Shift-drag/right-drag to pan, scroll to zoom, and double-click or **Fit** to reset framing. Choose Perspective/Top/Side, follow the rocket, adjust its display size, or toggle velocity and ground projection.
- Play/pause, rewind, change speed, scrub the timeline, or jump to ignition, burnout, apogee, deployment and touchdown events. Playback uses elapsed time, independent of repaint frequency.
- **Follow monitor time** displays the virtual-pointer rehearsal time, or the aligned telemetry flight time. This shows the simulation at that time; it does not actuate the pointer. Turn it off for independent inspection.
- Gold is the reference, teal is the actual track when the mission and reference origins match, and purple is the configured antenna. Camera movement and independent playback send no hardware commands.
- Re-run OpenRocket for older references that lack quaternion/event columns. Old four-column references remain usable, with a path-aligned illustrative rocket and an explicit unavailable-attitude/events label. Motor ignition or recovery is never inferred from height or descending motion. The test-launch fixture model has no recovery event in its nominal output; the bundled small example does.
- The rocket and canopy are schematic, not a CAD model or a deployment-dynamics prediction. Engine attitude held after tumble/recovery and insufficient angular sampling are labeled explicitly. Flight files retain the added motion/event data.

## Global settings

Open **Rocket GNC Monitor → Settings…** or press **⌘,** on Mac. On Windows, use **File → Settings…** or **Ctrl+,**. Settings are available in every mode, including disconnected LIVE.

- **OpenRocket JAR:** Browse to a complete compatible OpenRocket-MIT JAR, or choose **Use bundled**. The packaged app includes the engine and Java; no separate OpenRocket installation is needed. The bundled release version and effective path are displayed. Changes apply to the next simulation; a running job keeps its selected engine and time limit. If a custom file moves or disappears, select it again or return to the bundled engine.
- **Simulation time limit:** 10–1800 seconds; default 120 seconds.
- **Recording folder:** Where new logging sessions are created. **Use default** restores the app data directory's sessions folder. Existing sessions and active recordings stay in their current locations.
- **Appearance:** Dark or Daylight. The View menu's Daylight choice also persists.

**Save** validates and stores preferences; **Cancel** discards edits. **Restore Defaults** resets the form and takes effect when saved. Preferences live in the per-user application data directory as `settings.json` (the exact path appears in the window); `--data-dir` selects a separate preferences file. They are local to this computer and are not included in mission JSON or portable flight files. Mission-specific site, wind, rail, alert and video-offset choices remain in Mission configuration. The app always starts in LIVE with hardware closed.

## Original keyboard shortcuts

The original shortcuts are retained wherever their station/role permits the action. Video roles do not expose serial control, and Launch has no local pointer. On Mac, Qt maps Ctrl to **Command** and Alt to **Option**, as in the previous UI.

| Action | Windows / portable Qt | Mac |
|---|---|---|
| Refresh serial ports | Ctrl+R | ⌘R |
| Ground station connect/disconnect | Ctrl+Alt+C | ⌘⌥C |
| Antenna connect/disconnect | Shift+Ctrl+Alt+C | ⇧⌘⌥C |
| Start/stop polling | Ctrl+Return | ⌘Return |
| Start/stop logging | Ctrl+L | ⌘L |
| Pointer UP / DOWN / RIGHT / LEFT | Ctrl+arrow | ⌘arrow |
| Pointer ZERO | Ctrl+0 | ⌘0 |

The Serial controls menu shows the shortcuts and current toggle state. Each action follows its board's connection state.

On macOS, **⌘W** closes the active window or dialog and **⌘Q** quits the application. Closing the primary Flight window ends the session and closes its owned windows. Closing the Systems window leaves Flight running; **View → Arrange station displays** brings Systems back.

## Rocket controls

The Launch **Systems** window shows the complete legacy telemetry/GPS/servo/cell tables, state and zero commands, roll servo angle, PD Activate, VTX 1/3/5/8 W, power rail requests and readbacks, BMS status, six pyro channels, and emergency recovery commands. The 3.3 V request stays on, matching the previous UI; Total is an aggregate indicator. State advance, pyro ARM/FIRE, and emergency recovery retain their confirmation dialogs. Pyro actions require selecting channels 0–5. Radio freshness is displayed independently from the ground serial connection.

## Recorded-file replay

Open a saved flight or session to inspect telemetry and any recorded video in **REPLAY**. Physical transports are disconnected and replay cannot send commands. Play, pause, seek and speed controls are described under [Sessions](#sessions). For simulation playback without hardware, use the OpenRocket reference and virtual-pointer workflow above.

## Configure and connect

Mission → Configure: choose **Latitude / longitude**, **MGRS**, or **Plus Code** for the launch location. All conversions work offline and display the resolved WGS84 coordinates before Save. MGRS accepts compact or spaced references, UTM and polar grids, and reports the supplied grid resolution; it uses the grid reference point. Full Plus Codes resolve directly to their area center. For a short Plus Code, enter nearby reference latitude/longitude; a named locality alone is not resolved. Saving retains the chosen format and code alongside canonical latitude/longitude. Switching formats alone preserves the existing coordinates.

**Launch site preset → URRG** fills `18TUN2061530290`, selects MGRS and marks the launch origin established. The preset name and exact code survive mission/flight saves. Editing the coordinates changes the preset to Custom; switching coordinate display formats alone preserves the site. Presets do not supply elevation.

Enter WGS84 **ellipsoid altitude**, separate **mean sea level elevation**, and mark the origin established. Geographic transforms use ellipsoid height; atmosphere/weather use MSL. Choose the verified legacy altitude interpretation. Firmware may zero GPS height preflight; select GPS relative to launch if that is the board configuration. This interpretation controls the trajectory comparison. Legacy pointer tracking continues to use its original GPS/barometric convention.

Stop recording before changing the mission. Legacy pointer operation does not require a mission-calibration checkbox.

In LIVE at an Away telemetry station, assign the downlink and antenna boards to different serial selectors and connect each explicitly. Launch connects one local telemetry board and chooses an Away antenna route separately; it has no local pointer. A connected or connecting board's port disappears from the other board's dropdown, including its Mac tty/cu alias, and returns after disconnect. Both are 115200 8N1, no flow control, with the same default DTR/RTS settings as the old UI. Rescan after plugging in devices. Reconnect manually; tracking never resumes automatically after a stale target/disconnect.

In the **Video** role, Away has its assigned Digital and Analog USB inputs, while Launch has only local Digital and pending remote station sources. Balius has two system channels; Iris has Sustainer Digital, Sustainer Analog and Booster Analog. These labels describe the rocket-to-ground links, not the USB transport. In each panel choose **Find → USB receiver → Start**. A camera selected for one panel is removed from every other local dropdown, and an active or closing camera remains reserved until its capture has closed. Select the empty choice to release an unused selection. Each panel has its own **Stop**, **File…**, frame-age display and fault status. Stopping or changing one stream leaves the other running. Grant OS camera permission when requested. Each input supplies both display and recording.

Flight t=0 is set only by an observed Preflight → Flight transition or an explicit operator alignment in Sessions. Joining mid-flight leaves it unaligned. Onboard time is uptime; after a detected reboot, realign it. Diagnostics shows packet counters, full samples and alerts. Low battery requires one second below threshold and clears 0.3 V above it. Acknowledgment logs an event without hiding the active condition.

## Antenna

The drawing uses cached geometry and its own 16 ms animation timer, independent of the slower dashboard refresh. Hidden views stop animating. Rotation takes the short path through north, and framing stays fixed during movement. This visual smoothing changes no serial commands or control timing.

The drawing follows the supplied stand and antenna references with the September 20 attachment clarification: the grid dish, Yagi and black Avenger XR18 mount directly to a shared beam concentric with the support pivots. Their mounting centers share the beam elevation rather than using raised/lowered stalks. Dimensions and hidden mechanisms are illustrative. Drag the model to orbit from any angle, including above and below; scroll to zoom; double-click or use a preset to reset the view. Orbit changes only the viewing camera, even when Live controls are locked. Enter azimuth/elevation and click Send. Editing the fields does not move the graphic or transmit. UP/DOWN/LEFT/RIGHT send the original firmware direction opcodes; ZERO sends the original zero opcode.

“Sent” means serial dispatch, not board acknowledgment or arrival. Measured angles are unavailable. “ZERO” resets software step counters at the physical current position; it does not home to a switch. The graphic smoothly approaches the last sent target; a direction command before a known target leaves the angle readout unknown.

With both boards connected and polling, use **Send to AntPtr** to freeze the ground-station GPS coordinates. **Start tracking** then sends pointing angles at 5 Hz using the rocket GPS latitude/longitude and filtered barometric altitude, exactly as the old UI calculated them. Ground coordinates retain the old five-decimal rounding and the old pointer altitude of zero. The displayed receiver altitude retains its historical division by 1000. These conventions are preserved separately from trajectory display settings. Unfreeze to update the ground location; Stop tracking ends periodic commands. Stale/invalid telemetry or disconnection stops host tracking; restart explicitly.

**Stop tracking stops new targets and removes unsent queued work. The last commanded movement may continue.** An independent physical stop/power control is needed to interrupt motion. The legacy protocol supplies no stop/watchdog. No automatic retries. REPLAY cannot transmit.

## Wind and nominal reference

Set the site and a timezone-bearing weather time, e.g. `2026-09-18T16:00:00Z`. Retrieve weather or enter/import a manual profile, then Apply wind after table edits. Heights are metres above launch, directions meteorological **from** true north, speed m/s. Internally wind uses east/north vectors. Retrieved geopotential heights are converted to geometric MSL height then height above launch. The nearest available hour within one hour is used; outside layer coverage the nearest endpoint wind is held. Review coverage. Raw requests/responses are retained with missions/sessions for offline reuse. [Open-Meteo](https://open-meteo.com/en/docs) weather data is attributed under CC BY 4.0.

Select a `.ork` with a saved simulation/motor configuration; index 0 means the first saved simulation. The test-launch motor **MITRT N8406** is bundled and loaded automatically, so `test_launch.ork` works without selecting an extra motor file. Other custom `.eng`/`.rse` curves can be added with **Select custom motor files…**; a missing curve is reported by name before simulation. All motor inputs are copied into the job folder and hashed. Run nominal simulation; Cancel terminates its process without stopping telemetry.

The bridge uses the saved motor/flight configuration, specified rail/site/wind, ISA atmosphere, zero turbulence standard deviation, fixed seed and first output branch. Saved simulation extensions and the fork's global inertia override are disabled. Inspect `trajectory.json` warnings/branch count and `worker.log` in the job folder. Controlled canard/tab dynamics remain unqualified.

An imported normalized reference needs a CSV with `time_s,east_m,north_m,up_m` and same-basename JSON declaring `schema_version:1`, `frame:"ENU"`, `units:"m,s"`, `origin:[latitude,longitude,ellipsoid_altitude]`. Up is launch-relative. Live origins must match. No reference-time extrapolation is performed.

## Flight files

Use **File → Save flight…** (⌘S on Mac, Ctrl+S on Windows) to write one portable `.rktflight` file. **File → Open flight…** (⌘O / Ctrl+O) restores it. The Launch Systems window also provides flight-file controls. These add to the original shortcuts and mission JSON/session-folder options.

A flight file contains mission settings (including launch-site choice, wind and weather response), the selected rocket model/custom motor files, the current simulation reference, and available session telemetry, raw bytes, events, original CSVs and recorded video. Embedded model/motor files reopen from the application's flight cache, so their original paths are not required. Saving is atomic; a failed save leaves an existing file intact. Loading checks archive contents and checksums before replacing the current flight.

- During logging: **Save flight** snapshots committed telemetry while logging continues. Video includes only finalized segments; the segment currently recording is omitted. **Stop Logging and save again** to include the final segment and telemetry. Active snapshots remain labeled as snapshots, with dropped-record/error information retained.
- After stopping logging: Save includes the last session in the current source mode and waits for its video to finish closing.
- In REPLAY: Save includes the opened session and current playback position.
- In LIVE without a recording: Save includes only the displayed telemetry buffer (up to 6,000 samples), explicitly labeled as such. Use **Start Logging** for a complete capture.
- Before telemetry arrives: Save creates a planning flight containing mission/model/reference only.

Stop logging before opening another flight. A flight containing a session opens in paused **REPLAY**, with both serial transports disconnected. A planning flight opens in disconnected **LIVE**. Pointer calibration and hardware connections are never restored. Existing mission JSON files and recorded-session folders remain supported by their original Open commands. Extracted flight assets remain under the application data directory's `flights` folder.

## Sessions

Start Logging (or ⌘L on Mac) immediately creates a unique folder under the application data directory’s `sessions` folder. Its path appears in Diagnostics. Each folder contains `telemetry.csv` and `telemetry_badpackets.csv` with all 43 original columns, plus the manifest, indexed SQLite telemetry/events, CRC-protected incoming/outgoing serial bytes, optional reference, and video segments. Stop Logging finalizes the session. The recorder operates independently of UI redraws. Closing finalizes recording; inspect completion/error status.

Open the folder in Sessions. Play/pause, seek, 0.1-second step and 0.25×–4× speed are available. Seek removes future state and restores earlier commands/time alignment. CSV export retains full sample JSON. Incomplete sessions may be read if committed SQLite data is intact and remain marked incomplete. Do not remove a crashed session's SQLite `-wal` file before recovery.

Video uses host timing, not exposure timestamps. A positive mission video offset delays video; a negative offset advances it. Paused seek decodes one frame for each recorded stream. Each active channel retains separate segment directories and replay timing; all recorded channels are included in saved flight files. Older single-video recordings open in Digital. A panel's Stop also pauses that replay feed until the next seek or playback restart. Reconnect creates a new segment directory for that stream. A crash may lose the active video segment and uncommitted telemetry while finalized data remain recoverable.

Import telemetry CSV converts the audited legacy monitor layout into a replay session, retaining the original file, extra columns and quality limitations. Other CSV layouts are rejected; raw packet checksums are unavailable for CSV imports. Serial reconnect is manual. New vehicle actuator feedback requires a new verified wire contract.
