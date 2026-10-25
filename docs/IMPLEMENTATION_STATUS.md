# Current implementation status

This is the maintained v0a application in the standalone `RT_Telemetry_UI` repository. [Station modes](V0A_STATION_MODES.md) describes the current interface; [BUILD.md](BUILD.md) defines native package preparation and verification.

## Implemented

- Startup wizard and command-line profile selection for Launch/Away 1–4, Telemetry/Video, Balius/Iris and URRG/custom site; optional remembered setup and explicit forget action.
- Two-window Launch telemetry, reduced Away telemetry and separate station-specific video workspaces.
- Legacy Zephyrus decoding, original command encoders/shortcuts, separate connection/polling, port exclusivity, GPS/servo/power/BMS/recovery readouts, recordings and replay.
- Exclusive local USB camera assignment, independent capture, per-channel FFmpeg recording and replay; Launch video source selection/promotion UI.
- Bundled GS1/GS2/GS3 recorded demonstrations, portable flights, mission coordinates/presets, settings, nominal OpenRocket/wind simulation, 3D playback and virtual Away pointer rehearsal.
- Smooth illustrative antenna graphics; compact rocket pose with a fixed-upright azimuth camera and explicit telemetry/reference provenance.
- Receive-only uncompressed APRS via external Dire Wolf KISS TCP, one-peer read-only LAN snapshots and truthful Wi-Fi selection/current-connection status.
- Native Mac and Windows build tooling, universal Mac composition, automated unit/integration tests and packaged runtime verification.

## Protocols or evidence still needed

| Item | Current behavior |
| --- | --- |
| Launch uplink enable/disable | One Launch telemetry board; labelled DEMO-only switch. LIVE rocket commands are locked pending the wire protocol. |
| Iris Sustainer/Booster selection | Labelled DEMO-only board target; no fabricated switch command. Launch's single board shares one target for reception/uplink. |
| Four-station transport | Launch selects an Away pointer route and video sources. Remote telemetry/video/control transport is not implemented. The one-peer LAN snapshot link does not supply it. |
| Sensor fusion | No multi-station fusion or gyro-integral attitude reconstruction. Local, remote and simulation sources remain distinct. |
| Telemetry attitude | Current Zephyrus fields are gyro integrals, not Euler orientation. Raw readouts remain; the model is neutral and marked ORIENTATION UNAVAILABLE. |
| Telemetry ignition/deployment | No confirmed motor-burning/parachute fields in the current decoder. Simulation events are available only in explicitly selected reference playback. |
| New canards/fin tabs | Legacy four-servo values remain available; new actuator feedback needs a verified mapping/protocol. Controlled simulation dynamics are not qualified. |
| Pointer feedback/stop | No measured pose, acknowledgement, mechanical home or stop opcode. Stopping tracking stops new targets; the last move may continue. |
| LTU/analog VRX/calls | LTU metrics/management, receiver tuning and interstation calling await interfaces/implementation. |
| Hardware acceptance | Real USB boards, cameras, pointer mechanics, APRS radio and network equipment still require field checks. Automated tests do not replace those. |

The original v0 plans, superseded LaTeX report and historical validation archives remain in the former repository. They are not current support claims and are not required to run/build this checkout.
