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
- A direct **View → Broadcast controls… / ⌘⇧B** shortcut (Ctrl+Shift+B elsewhere), with reliable operator-panel focus when returning from Program. Draft camera choices and crop survive temporary source outages; Take remains unavailable until fresh video recovers.
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

- Latest local desktop-qualification run: **145 passed, 1 skipped**.
- Earlier full regression run: **919 passed, 1 skipped**; latest focused broadcast/OBS run: **35 passed** (including the outage regression).
- The application has passed desktop checks across all four platforms. Latest test-only correction: Intel Mac, Windows and Linux CI passed; GitHub’s Apple Silicon rerun is still queued. The corrected suite passed locally on this Apple Silicon Mac. The report preserves earlier failures and their resolutions.
- Package verification: **17 checks passed**, followed by a successful broadcast/OBS-preset smoke check from the final installed app.
- One-hour local camera/telemetry soak: **passed**, 29.72 fps, no broadcast-history or archive drops. It exercised the earlier core milestone; the final-source load and focused tests cover subsequent changes.
- Core implementation ten-minute Iris load: **passed**, 29.87 fps with eight feeds, five main receivers and zero archive loss. Rendering did incur bounded presentation drops; the report records them and the preceding concurrent-load readiness failure.

On **2026-11-04**, with your approval and macOS authentication, OBS capture permission was enabled and the individual **Rocket Broadcast — Program** window was mapped. A real local recording from the installed app's **Start local recording / Stop recording** controls verified moving video, changing telemetry, Imperial/Metric units, camera loss/recovery, telemetry blanking/recovery and both app/independent OBS emergency slates. Authenticated connection, disconnect/reconnect and output-state reporting were also tested.

The main saved recording is **3:28.433**, **1920×1080 H.264 at 30 fps**, with stereo **AAC 48 kHz** isolated test audio; complete decode passed. OBS reported **17 skipped encoder frames (about 0.27%)** across the whole run, so this is functional qualification, not a claim of lossless performance. The preceding diagnostic run had much heavier drops and a recovered control disconnect; both results are retained in the report.

The latest camera-selection fix is pushed as **30a4299**, rebuilt and installed in Applications. Recording evidence above uses the preceding **f844fee** build; the final fix changes draft camera retention, not the renderer or OBS bridge. Exact hashes and final package/CI checks are in the verification report.

## Start here when back at the Mac

1. Launch the installed app and choose **Base station → Video**. Join the intended mission/source mode, then open **Broadcast…**.
2. Choose a main camera, **Prepare Preview**, wait for readiness and **Take → Program**. The clean window is **Rocket Broadcast — Program**.
3. In OBS, use **Rocket 1080p30** and the **Rocket Broadcast** scene collection. Capture permission is enabled on this Mac. Reselect the current **individual Program window** after reopening it; verify capture dimensions, cursor/chrome exclusion and your actual audio inputs. The measured capture used a 56-pixel top crop at 1:1 scale; recheck rather than assuming that crop on another display.
4. Enable authenticated OBS WebSocket control in OBS, then enter that local password in the app's **OBS / YouTube** tab. Verify capture/audio/recording destination and make a local recording.
5. After checking your physical audio and timing, configure the YouTube destination in OBS and perform the intended unlisted test with a separate viewing device.

OBS needs **no account**. The local OBS password is separate from the **YouTube stream key**, which stays in OBS. The app does not save the OBS password.

## What still needs the actual setup

The local software and live OBS capture workflow are implemented and tested. Actual range/microphone inputs, physical camera/telemetry timing, long-duration complete equipment load and internet upload capacity still need the event setup. The test tone proves an audio track was recorded; it does not qualify microphone levels or A/V alignment. YouTube channel/key setup and the real unlisted test remain intentionally deferred.

OBS was left on **Rocket Broadcast → Emergency slate**, with recording and streaming confirmed stopped. Program and the isolated rehearsal app are closed; the synthetic publisher is stopped. The test-tone source is disabled and muted, and display units were restored to Imperial. No YouTube credentials were entered.

Saved recording on this Mac: `~/Movies/Rocket Broadcast/Rocket-2026-11-04_09-25-39.mkv`. A same-name `.mp4` playback copy is also available (remuxed without re-encoding). [Recorded frame examples and machine-readable evidence](assets/broadcast/obs-live-capture-verification.json) are committed; the full video stays local.

Synthetic network, renderer, recovery, UI and packaged-app tests do not qualify real camera/telemetry latency, audio drift, launch-site radio load or internet upload capacity. Missing field semantics remain suppressed; in particular, current physical Iris samples do not establish a Sustainer/Booster identity or a validated attitude merely because the operator selects one.

Use [BROADCAST_OPERATOR_GUIDE.md](BROADCAST_OPERATOR_GUIDE.md) for the complete setup, calibration, recording and shutdown procedure. Stop OBS output explicitly before stopping Program or quitting Base Video.
