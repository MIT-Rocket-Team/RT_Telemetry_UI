# Broadcast implementation handoff

The Base Video application now composes the planned MIT Rocket Team broadcast and provides local OBS recording/streaming controls. The macOS application is installed in Applications. No YouTube stream was started and no stream key was entered.

## What was added

- Camera-first 1920×1080 output with the web-sourced MIT Rocket Team logo, Imperial/Metric gauges, mission clock and curved event timeline.
- Separate operator Preview and clean Program windows. Prepare a shot, wait for readiness, then Take; browsing the normal video wall does not change Program.
- Standby, prelaunch, flight, recovery, replay and end-card scenes; staged camera crop, caption, camera inset and validated orientation inset.
- Independent manual GS telemetry choice or Best connection reevaluated every second. Vehicle/source identities and unavailable measurements remain explicit.
- Bounded delayed frame/telemetry histories, timing calibration, source-restart invalidation, stale/lost notices, automatic loss slate, pause/seek handling and emergency slate.
- Operator countdown/hold, observed-now versus on-Program event marks, corrections and a separate production log. Custom timeline labels retain the actual liftoff clock control.
- An OBS / YouTube tab for authenticated local connection, recording Start/Stop, confirmed streaming Start/Stop and current output health. Reconnecting cannot automatically start a stream.
- Exportable OBS 1080p30 and 720p30 presets, an independent emergency slate and an operator guide. Credentials are excluded from exports and production logs.

Broadcast reuses the existing camera receivers and canonical telemetry. It does not send rocket commands or change raw recording units. Opening the app does not automatically start Program or streaming.

## Installed files

- Current app: `/Applications/RocketGNCMonitor-v0a.app`.
- OBS: `/Applications/OBS.app`, version 33.0.1 Apple Silicon.
- macOS distribution: `dist/RocketGNCMonitor-v0a-Darwin-arm64.dmg` in this repository.
- Original application backup: `build/application-backups/20261103-before-broadcast/RocketGNCMonitor-v0a.app`.
- Detailed test evidence, exact source revision and artifact hashes: [BROADCAST_VERIFICATION.md](BROADCAST_VERIFICATION.md).

The existing Windows archive in `dist/` is older and was not rebuilt for this delivery.

## Verification results

- Full regression run: **919 passed, 1 skipped**; final focused broadcast/OBS run: **33 passed**.
- GitHub qualification passed on Apple Silicon Mac, Intel Mac, Windows and Linux. One existing Intel video timeout passed on an unchanged retry; the report preserves that history.
- Package verification: **17 checks passed**, followed by a successful broadcast/OBS-preset smoke check from the final installed app.
- One-hour local camera/telemetry soak: **passed**, 29.72 fps, no broadcast-history or archive drops. It exercised the earlier core milestone; the final-source load and focused tests cover subsequent changes.
- Final-source ten-minute Iris load: **passed**, 29.87 fps with eight feeds, five main receivers and zero archive loss. Rendering did incur bounded presentation drops; the report records them and the preceding concurrent-load readiness failure.

These are local software/transport results. They do not establish an OBS-captured or YouTube-delivered broadcast.

## Start here when back at the Mac

1. Launch the installed app and choose **Base station → Video**. Join the intended mission/source mode, then open **Broadcast…**.
2. Choose a main camera, **Prepare Preview**, wait for readiness and **Take → Program**. The clean window is **Rocket Broadcast — Program**.
3. In OBS, use **Rocket 1080p30** and the **Rocket Broadcast** scene collection. Grant the needed screen/audio permissions and map the **individual Program window**. Verify actual capture dimensions, cursor/chrome exclusion and local audio.
4. Enable authenticated OBS WebSocket control in OBS, then enter that local password in the app's **OBS / YouTube** tab. Verify capture/audio/recording destination and make a local recording.
5. After local capture and audio pass, configure the YouTube destination in OBS and perform the intended unlisted test with a separate viewing device.

OBS needs **no account**. The local OBS password is separate from the **YouTube stream key**, which stays in OBS. The app does not save the OBS password.

## What still needs the actual setup

The Mac locked during native OBS setup, so its actual Program-window capture, WebSocket connection and local audio/recording rehearsal could not be completed unattended. OS permissions were not bypassed. These checks remain before the real YouTube test, in addition to the intentionally deferred channel/key setup.

Synthetic network, renderer, recovery, UI and packaged-app tests do not qualify real camera/telemetry latency, audio drift, launch-site radio load or internet upload capacity. Missing field semantics remain suppressed; in particular, current physical Iris samples do not establish a Sustainer/Booster identity or a validated attitude merely because the operator selects one.

Use [BROADCAST_OPERATOR_GUIDE.md](BROADCAST_OPERATOR_GUIDE.md) for the complete setup, calibration, recording and shutdown procedure. Stop OBS output explicitly before stopping Program or quitting Base Video.
