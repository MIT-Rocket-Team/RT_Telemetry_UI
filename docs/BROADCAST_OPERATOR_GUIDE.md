# Base Video broadcast operator guide

The **Base Video** application composes the audience picture. OBS, on the same Mac, captures that clean window, mixes audio, records and sends to YouTube. OBS does not require an account. YouTube does require an enabled channel and destination/stream key. The app never asks for that key.

## Open the controls

1. Launch **RocketGNCMonitor-v0a** from Applications. Choose **Base station → Video** and the intended vehicle.
2. Join the intended mission with **Network…**. Joining a simulation remains an explicit choice; its output is marked SIMULATION.
3. Click **Broadcast…** in the Base Video toolbar. This creates one presentation owner and reuses the existing network and camera receivers.
4. In **Produce**, choose a scene and main camera, enter the mission title/caption, then **Prepare Preview**. Start with the inset off.
5. Wait for **Ready to take**, then **Take → Program**. The clean window is titled **Rocket Broadcast — Program**. Take commits at an output boundary, using delayed history for the newly selected camera.

Return to the operator panel with **View → Broadcast controls…** or **⌘⇧B** on macOS (**Ctrl+Shift+B** elsewhere), including while Program has focus. This leaves Program running and does not change OBS outputs.

Opening the app never starts Program or OBS. Opening Program initially presents a slate. Closing the control panel leaves Program running. **Stop Program** closes the visual output and releases its camera subscriptions; it does **not** stop OBS. Stop OBS separately before quitting the application.

## Main picture and telemetry

Camera selection is manual. Optional left/top/right/bottom main-camera crop margins are staged with Preview/Take and visibly labelled in Preview; intentionally choosing a different camera resets those margins. A temporary camera disappearance preserves the draft choice and crop, marks that choice unavailable, and keeps Take disabled until video recovers. The remaining image is letterboxed with its aspect ratio preserved. Browsing the normal video wall or changing the telemetry receiver never cuts the audience camera. One new camera warms at a time; take a main camera before adding a second-camera inset. Admission preserves existing wall/Program decoders and respects the local memory cap and ordinary publisher main-session limit. A publisher may also reject a request because other computers consume its capacity.

Telemetry can follow Away station 1–4 or **Best connection**. Best reevaluates once per second, ranks qualified observations for the selected vehicle by rocket sample age, then LTU-XR application RTT, and retains the current receiver on an exact tie. Ages up to 60 ms share the full-quality rocket score, matching the existing connection display. Missing history/calibration for the winning receiver stays unavailable; it does not silently select another station.

The two gauges use the application's existing **Settings → Display units** preference. Imperial is the default and uses OpenRocket's feet and feet/second definitions. All recordings, network packets and flight calculations retain their original units.

A valid packet does not automatically qualify every display field:

- Barometric altitude requires its declared datum, or a documented per-run override in **Timing and telemetry**.
- The native scalar velocity requires an explicit source contract establishing signed vertical m/s. Otherwise vertical speed is blank.
- Physical Iris observations currently have unknown vehicle attribution; picking Sustainer/Booster in the panel does not manufacture attribution.
- Attitude requires validated orientation or the existing packet-associated simulation extension. Integrated gyro values never rotate the rocket model.
- Motor/chute state is never inferred from an attractive animation or from arbitrary phase labels.

## Timing and calibration

All composition uses `T = output time − common delay`. For a calibrated path, an item's estimated event time is its local arrival minus the measured path delay. The newest item at or before T is selected. Data from the future is never substituted.

The 0.5 second default is **uncalibrated receive-time buffering**, visibly marked SYNC UNCALIBRATED. It does not assert camera/telemetry synchronization.

1. Select the desired camera and telemetry paths, then **Load current paths**.
2. Measure each camera/GS delay and jitter using a common-clock flash/telemetry marker. Enter delay, jitter, method, date and residual error.
3. Set the common delay to cover every path delay plus jitter and one 30 fps frame. Qualify all GS paths that Best may choose.
4. Measure each OBS audio offset. A commentator watching delayed Program and a direct range microphone generally need different offsets. Do not add the common delay twice.
5. Check the measurement and audio confirmations, then **Apply timing → slate**. This clears presentation histories and the presentation clock; re-establish the clock, warm the scene and Take again.

Unsupported delay/memory combinations are rejected. Missing calibrated paths remain unavailable. Qualification requires connected source identities. A changed measured camera epoch or telemetry connection invalidates timing and returns Program to a slate. Mission/vehicle/source-mode changes also clear qualification. Recheck after changing devices, codec or network route even when its identity is unchanged. Saved measurements are reference preferences: calibration is unqualified again on the next app launch.

## Clock, timeline and replay

**Clock and events** offers a relative planned countdown or an ISO scheduled time with explicit UTC offset, for example `2026-11-04T14:30:00-05:00`. Hold freezes a planned countdown; resume is explicit. Reaching planned zero displays AWAITING LAUNCH until actual liftoff is marked.

Select LIFTOFF and use **Mark event observed now** when the event is happening in the real world. Use **Mark event on Program** when it is happening in the delayed picture. These have different time anchors. Repeating an event does not move its timestamp unless **Correct an existing event time** is checked. All decisions are logged.

Only explicitly received supported states can mark their own milestones; later states do not fill in earlier missing events. A received APOGEE/MAIN state supplies reception evidence, not an exact event timestamp. Custom timeline labels are supported, up to six labels of 12 characters. The actual LIFTOFF clock control remains available even if the custom timeline omits that label.

Replay uses the existing exclusive replay mode. Local replay has a recorded flight clock only when the existing recording has a time-aligned sample/zero mapping. Otherwise a clock remains unavailable until explicitly anchored. Operator clocks are labelled, including APPROXIMATE when timing is uncalibrated. Known pauses retain the last picture/time with PAUSED; a disconnected remote transport is still reported. Resume excludes the paused interval from an operator-anchored playback clock. Resume playback before editing its clock or marking new events. Seek/source changes invalidate incompatible history. Production annotations from a different run are not replayed as flight truth. Hardware-specific remote replay/simulation time qualification remains part of field rehearsal.

## Prepare OBS

OBS **33.0.1 Apple Silicon** is installed for this Mac. The packaged app includes **OBS / YouTube → Export OBS presets…** and a separate emergency slate. Export to a new folder, then import the profile/scene collection in OBS. The repository also includes the same preset generator. It creates named profiles without changing existing profiles or installing credentials:

```bash
.venv/bin/python scripts/prepare_broadcast_obs.py --output build/obs-setup
```

With OBS closed, `--install` installs new named presets on macOS and refuses to overwrite existing named files. The prepared local profiles are **Rocket 1080p30** and **Rocket 720p30**. The production scene collection is **Rocket Broadcast** and starts on **Emergency slate**. The optional **Rocket Local Rehearsal** collection contains only an explicitly supplied test movie. Keep an exported folder in place: it contains the independent slate artwork. When moving presets to another computer, remap that image and the recording folder; the export README explains this.

1. Open OBS with the team profile/collection. Keep the independent Emergency slate available.
2. Open Program in the telemetry app. In the OBS **Rocket Program** scene, map the macOS Screen Capture source to that **individual Program window**. Grant the necessary screen-capture permission when ready; never capture the entire desktop or application.
3. Turn off cursor capture; exclude title-bar chrome and verify exactly 1920×1080 content pixels. A Retina window may be 960×540 logical points. The app provides **Reset Program to 1920×1080 pixels** after display-scale changes. Revalidate OBS's actual source/crop after moving displays or restarting.
4. Add the intended local microphone/range inputs. Keep monitoring off unless routed to headphones. Current rocket video transport has no audio.
5. Use 1080p30, H.264 CBR 14 Mb/s, two-second keyframes, SDR Rec.709, AAC stereo 128 kb/s / 48 kHz, and MKV recording. The reduced preset is 720p30 at 5 Mb/s and must be qualified on the actual uplink.
6. Record locally and inspect motion, source labels, clock, both unit settings, audio alignment and a loss/recovery sequence. Then configure YouTube in OBS for the real unlisted test.

On this Mac, OBS capture permission was approved and enabled on 2026-11-04. The tested Retina source was 1920×1136 including a 56-pixel title bar; a top crop of 56 at scale 1 produced the recorded 1920×1080 audience picture. These are measured values for that window/display, not universal settings. Select the current Program window again after relaunch; macOS window IDs change. The native window's rounded corner pixels can remain at the extreme output edge; all content stays inside the safe margins.

The local rehearsal's **LOCAL TEST TONE — NOT RANGE AUDIO** source is left disabled and muted. Keep it off for a real broadcast. Screen-capture audio was also muted during qualification; configure and measure your intended physical inputs separately.

The software encoder preset is portable. Switch to Apple VideoToolbox only after checking the desired settings and repeating the recording test. OBS updates, encoder changes and audio-device changes require requalification.

## OBS controls in the application

In OBS, enable **Tools → WebSocket Server Settings** with authentication. In the app's **OBS / YouTube** tab, enter the local port (4455 by default) and the **OBS WebSocket password**, then connect. This password is distinct from the YouTube stream key; the app does not save it to its settings or logs.

The app connects only to `127.0.0.1`. Check OBS's own server exposure/firewall configuration; the app does not alter OS permissions, firewalls or OBS authentication settings.

The panel reports OBS scene, stream/recording state, reconnecting state, bitrate, frame rate, dropped frames and render/encoder lag. A failed/stale connection reports **unknown**, not stopped. Reconnecting never resends a Start command. When connection is unknown, use OBS directly.

After verifying capture, output resolution, audio and destination, check the run confirmation. **Start local recording** and **Start YouTube broadcast…** become available while Program is open. Starting a broadcast requires another explicit confirmation. **Stop broadcast…** and **Stop recording** operate independently, including after Program has been stopped. Stopping either output never commands rocket hardware.

OBS SENDING establishes that OBS reports an active output; it does not prove the intended YouTube event is visible. Always inspect the YouTube Studio destination and a separate device's return.

## Loss and shutdown procedure

- Delayed video gets a notice after 0.5 s; after 2 s the selected source becomes a slate. Recovery needs a continuous valid buffer. Choose another camera with Preview/Take if needed.
- Stale telemetry is explicitly labelled and then blanked. Missing orientation is labelled. Video can continue with unavailable telemetry.
- **Emergency slate** cuts the app's Program to a clean standby at the next output boundary. OBS also has an independent slate for an application crash or quit.
- For shutdown: select the OBS independent end/slate scene; stop broadcast; confirm OBS stopped; stop/finalize recording; then stop Program and close Base Video.

Production decisions and ten-second metrics are under the app data directory's `broadcast-events/`. Settings are in `broadcast.json`. Neither contains stream keys or OBS passwords. Existing raw/decoded recordings remain separately owned and unchanged.

## Verification and remaining event-day checks

For an installed-app rehearsal without hardware, run:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/serve_broadcast_fixture.py --output build/obs-live-rehearsal
```

Read that directory's `endpoints.json`, add its video and telemetry addresses as manual peers in an isolated Base Video session, then explicitly join the SIMULATION mission. Select AWAY1 digital in Broadcast. The publisher is loopback-only and its camera picture is labelled as a local test. `control.json` can disable `video` or `telemetry` for loss/recovery checks; restore both to `true` to recover. Stop the publisher with Ctrl-C. Its scalar velocity is the signed derivative of its sinusoidal altitude in m/s; only qualify that field for this fixture's scope. This does not qualify actual flight telemetry or physical timing.

See [BROADCAST_VERIFICATION.md](BROADCAST_VERIFICATION.md) for measured results and limitations. Local synthetic SRT tests do not qualify real camera latency, the three-feed Iris field configuration, local audio devices, internet routing, or YouTube delivery. Those checks use the actual event equipment and credentials.
