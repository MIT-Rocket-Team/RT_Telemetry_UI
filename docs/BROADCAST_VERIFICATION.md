# Broadcast implementation and verification

Implementation date: 2026-11-03; local OBS follow-up: 2026-11-04, America/New_York. This report separates implemented behavior, locally measured evidence and equipment/account-dependent qualification. **No real YouTube stream was started.**

## Source milestones

- `5190c9f`: immutable broadcast models, branded renderer, bounded timing histories, shared frame subscriptions, canonical observation signal and per-vehicle receiver ranking. Focused core/media/network suite: 36 passed. The pre-existing settings shortcut test passed in isolation after its baseline timeout. A network-test helper was corrected to tolerate an unresolved peer identity while discovery is in flight.
- `23b158a`: Base Video production UI/controller, scene and Preview/Take workflow, mission-clock/event controls, source handling, authenticated local OBS control/status, native UI tests and real SRT rehearsal harness. 21 focused checks passed, including no duplicate/replayed OBS starts.
- `35325bc`: independent decoder ownership tests, renderer-error slate, bounded telemetry-node history, native-package smoke entry point, OBS presets and the operator guide.
- `4d2edc9`: production hardening milestone. Adds packaged OBS preset export with independent slate assets, replay-seek invalidation, pause/resume clock behavior, calibration identity checks, operator clock labels, recovery tests and cross-platform CI coverage. Broadcast implementation/scripts pass Ruff. The final `16e0fd3` application revision retains actual liftoff controls when custom timeline labels omit LIFTOFF; its broadcast/OBS focused suite passed **33 tests**. Subsequent focus/camera-retention UI fixes are recorded below with their own checks.

## Sending computer and dependencies

| Item | Observed state |
| --- | --- |
| Computer | MacBookPro18,4, Apple M1 Max, 64 GB RAM |
| OS | macOS 27.2, build 26B123 |
| Python | Bundled build environment Python 3.13.18 |
| UI | PySide6 Essentials 6.12.0; pinned project environment |
| OBS | 33.0.1 Apple Silicon, official OBS download; `codesign --verify --deep --strict` and Gatekeeper assessment passed, notarized Developer ID |
| Camera inventory | FaceTime HD Camera reported by system inventory; no rocket capture card qualified during this run |
| Branding | Web-sourced MIT Rocket Team logo, unchanged SHA-256 `f261c0124d57cdb9e9a172acb34182ff43d4756e54b216fdf34a9a5576811675`; provenance in resources/broadcast/LOGO_SOURCE.md |
| Program raster | 1920×1080, Imperial and Metric renderer checks |
| Native Program content | 1920×1080 device pixels in the native smoke report; this measures the app surface, not OBS capture |
| OBS presets | Named 1080p30/720p30 profiles and production/local-rehearsal collections installed without credentials; OBS log confirms the local fixture collection at 1920×1080, 30/1 fps |

The current transport decodes to 960×540 and the compositor scales that image into a 1920×1080 layout. This does not recover detail absent from the received rendition. No silent transport-resolution change was made.

## Field capability matrix

| Field/path | Current evidence | Broadcast behavior |
| --- | --- | --- |
| Physical Balius identity | Existing operator-assigned canonical vehicle ID | Eligible for the Balius scope; provenance unchanged |
| Physical Iris vehicle identity | Canonical publisher currently reports unknown | Unknown samples cannot populate Sustainer/Booster overlays |
| Barometric altitude | Publisher can declare a datum, defaults to unknown | Suppressed unless the datum is declared or a per-run documented override is supplied |
| Vertical speed | Native scalar m/s has no general vertical-vector contract | Suppressed unless explicitly qualified for the source |
| Physical attitude | No validated orientation in ordinary legacy packets | Orientation unavailable; gyro integrals are not substituted |
| Simulation attitude | Existing validated, packet-associated extension | Supported where the accepted packet supplies it |
| Native flight phases | Balius provisional / supported Zephyrus schema retained | State shown with existing schema semantics; missing milestones are not invented |
| Video | Real local H.264/MPEG-TS/SRT fixture exercised | Main-quality shared decoder tee feeds Program before wall coalescing |
| Rocket audio | Current pipeline is silent (`-an`) | Local OBS audio only; no implied camera audio |
| Local ground camera | Can reuse an active local capture worker | Scope association is operator-selected; real camera latency remains unqualified |
| Timing | Local receive times are known; field path delays unknown | Default SYNC UNCALIBRATED; explicit measured delays required for calibration |

## Test evidence

The baseline before changes was **886 passed, 1 skipped, 1 settings-shortcut focus timeout**. A later native full suite was **907 passed, 1 skipped, 2 keyboard-focus timeouts** while the Mac was locked. Both failing shortcut cases subsequently passed in an offscreen targeted run. A complete offscreen suite passed **910 tests, 1 skipped** in 224.65 seconds; the final expanded full run passed **919 tests, 1 skipped** in 221.54 seconds. The subsequently added calibration-identity case passed with the focused session/UI suite (**17 passed**); the complete broadcast/OBS focused suite passed **32 tests**.

[GitHub desktop qualification at 4d2edc9](https://github.com/MIT-Rocket-Team/RT_Telemetry_UI/actions/runs/41769683413) passed on macOS 15 Apple Silicon, macOS 15 Intel, Windows 2022 and Ubuntu 24.04. Its matrix now includes the broadcast model, session, UI, preset and OBS protocol tests alongside the existing real station lifecycle tests. This establishes portable software checks, not platform-specific OBS capture qualification.

[Final qualification at 16e0fd3](https://github.com/MIT-Rocket-Team/RT_Telemetry_UI/actions/runs/41770651554) also passed all four platforms. Its first Intel attempt had 142 passed, 1 skipped and a 20-second timeout in the existing `test_two_base_video_viewers_receive_both_mission_streams`; the broadcast tests passed. Rerunning only that failed job passed without changing code, timeouts or assertions. The transient timing failure is retained in the workflow history.

Focused tests cover canonical field suppression/scope rejection, observation deduplication and actual arrival, no-future selection, buffer limits and epoch retirement, per-vehicle Best ranking/tie retention, timing admission, countdown/hold/actual liftoff, independent camera and telemetry choice, Take readiness, stale/slate/recovery, pause/transport loss, settings restart behavior, receiver lease ownership, renderer failure recovery, native panel lifecycle and OBS authentication/status/control behavior.

OBS tests use an authenticated local WebSocket test server. They verify start/stop recording requests, status updates, wrong-password behavior, stale/disconnected control rejection and no Start replay after reconnect. They **do not establish a real OBS-to-YouTube connection**.

The local transport rehearsal (`scripts/verify_broadcast.py`) publishes numbered synthetic camera frames through the actual HTTP/SRT/H.264 stack and canonical telemetry through the station runtime. A Base Video owner receives both, composes Program and writes separate production metrics. It creates a short 30 fps reference MKV from sampled rendered frames; that file is a reference artifact, not a claim of an OBS-captured recording.

- Initial 35 s transport smoke: 940 Program frames, 28.14 average fps including synchronous image-save overhead; no broadcast buffer or archive drops. Image saves were subsequently moved off the GUI thread.
- Final 45 s fault rehearsal: **passed**, 1,299 Program frames, 29.93 fps, all seven fault checks passed: delayed notice, loss slate, recovery, explicit pause, frozen picture/clock during five seconds without new camera frames, resume, and exclusion of the paused interval from the clock. 73 broadcast-history frame drops during injected loss/pause recovery; zero archive drops. Bounded presentation drops do not block original reception/archives.
- One-hour local transport/compositor soak: **passed**, 3,600.02 seconds, **106,952 Program frames at 29.72 fps**, fresh telemetry at completion, zero broadcast-history drops and zero archive drops. Camera-history high water was 66.74 MiB; whole Python process RSS high water was 505.56 MiB. Median sampled render time was 4.90 ms. This run started from milestone `23b158a`; later hardening has separate focused tests and the final-source Iris load run.
- Native source smoke: bundled logo loaded; Program raster and device-pixel content both 1920×1080; no hardware workers or OBS connection opened.
- Native packaged app: **17 existing package checks passed**, including startup/station/video wall, Sustainer/Booster playback and bundled OpenRocket; the separate broadcast smoke passed with 1920×1080 raster/content, logo loaded, no hardware workers and no OBS connection. Final relocated installation evidence follows below.

### Full synthetic Iris load

`scripts/verify_broadcast_load.py` publishes all eight camera feeds from four local simulated away stations. The Base wall has three main views; Program shares one of those decoders and adds a camera inset; Preview warms a different main camera. The resulting load is **five main receivers plus five remaining preview receivers**, with three retained broadcast camera histories. The test deliberately leaves Iris telemetry unavailable instead of inventing a qualified physical adapter.

- First 600-second run, concurrent with package/native-app verification: **strict readiness check failed**. Program averaged 29.78 fps; all five main receivers and Program/inset were healthy at all 59 loaded samples, but next-shot Preview was not ready at two samples. The application correctly withheld Take. There were 179 broadcast-history drops, 104 missed output slots and zero archive drops. Concurrent activity coincided with the brief readiness loss; the exact cause was not isolated.
- A separate 600-second steady run of the final source: **passed**, 29.87 fps, all 59 loaded samples had five healthy main receivers, both Program images and a ready Preview. Median sampled render time was 4.78 ms. There were 92 bounded broadcast-history drops and 65 missed output slots; no archive drops. These counters are retained rather than described as perfectly lossless rendering.
- Steady-run camera-history high water: **173.53 MiB**; entire Python process RSS high water: **799.58 MiB**, including the wall, four publishers, receiver services and test generator. The 256 MiB broadcast budget is not a cap on that whole process or its separate encoder/decoder processes. This ten-minute fixture does not establish a one-hour complete OBS/audio field workload.

Both load runs were concurrent with the simpler one-hour transport soak. Their machine-readable reports are under `build/broadcast-iris-load/` and `build/broadcast-iris-steady/`.

The UI's missed-slot counter records rendering overruns; it is not a complete count of every scheduling gap. Average Program fps is measured over the actual active output interval and includes those gaps. A compact [verification summary](assets/broadcast/verification-summary.json) retains the measured results and detailed-report hashes. The [captured Program example](assets/broadcast/verified-program-fixture.png) is explicitly synthetic test video, not flight footage or an OBS capture.

Generated detailed reports, captured Program frames and reference movies are under `build/broadcast-*`. Build products are under `dist/`.

## Installed artifact

- Current app: `/Applications/RocketGNCMonitor-v0a.app`, source revision `30a4299` (2026-11-04 camera-retention fix).
- Current DMG SHA-256: `be49d884146b32859d0ac648f81233163ec62156dab58c3706f7dbec4ee128c1`.
- Current installed executable SHA-256: `00d06657abbe8b0290b5686ccb67e67355ceae8d51d249e012d9d3a31811138c`.
- The preceding `f844fee` app is retained at `build/application-backups/20261104-before-camera-retention/RocketGNCMonitor-v0a.app`. Its DMG hash was `e954fa208daef500488a10efaa9a91c3fbe113e77dfc19d8e8318478cc337088` and executable hash `ee2f0f9eea334bdeae0e82bfb2cb4ad29954d20290588fba55a6aebf2dd13fe3`.
- The prior `16e0fd3` app is retained at `build/application-backups/20261104-before-operator-focus/RocketGNCMonitor-v0a.app`.
- The current installed app's offscreen package smoke passed: **60 Program frames**, 1920×1080 raster/content, logo loaded and OBS presets exported, with no hardware or OBS connection. Strict deep signature verification passed. This checks bundled rendering/resources; actual OBS capture was qualified on the preceding `f844fee` binary below.

The following records the preceding 2026-11-03 package; its hashes are historical:

- App source revision: `16e0fd3`.
- Previous app retained at `build/application-backups/20261103-before-broadcast/RocketGNCMonitor-v0a.app`.
- Distribution: `dist/RocketGNCMonitor-v0a-Darwin-arm64.dmg` (Apple Silicon macOS).
- DMG SHA-256: `4f6fcc65cdd708ea5e093a2f6e33f04bf125acca87d3323faf0cf20e4a71355b`.
- Installed executable SHA-256: `150b392f30b097dd50016c52091d44f0505ac2c27b650542e2c2426cdf353328`.
- The 4d2edc9 app passed all **17 package checks**. After the final custom-timeline UI correction, the installed 16e0fd3 `/Applications` copy independently produced **52 frames**, loaded the logo, exported the bundled OBS profiles/slate, reported a **1920×1080** raster and content area, and opened no hardware or OBS connection.
- macOS Launch Services registration was refreshed for the installed path. The old Dock shortcut can continue pointing at that app.
- The preceding package encountered a transient disk-image “resource busy” error; retrying only disk-image creation succeeded. The final 16e0fd3 package completed the full build/DMG/checksum sequence successfully.

The separate Windows archive already in `dist/` predates this implementation and is not part of this delivery. Cross-platform CI success does not relabel an old binary as newly built.

## Initial real OBS follow-up — 2026-11-04 (before capture permission)

The unlocked Mac allowed native UI testing with OBS 33.0.1 and the installed app. The existing OBS WebSocket password was used, authentication remained enabled, and the password was neither changed nor saved in the repository or app settings. No YouTube credential was entered.

- A standalone, loopback-only simulated away publisher (`scripts/serve_broadcast_fixture.py`) supplied moving numbered camera frames and varying canonical telemetry. The installed `16e0fd3` app explicitly joined that simulation, selected AWAY1 digital, prepared/took Flight, and displayed the moving video, logo, source label and changing imperial altitude/qualified vertical speed in its clean Program window. This was observed directly, **not recorded through OBS window capture**. Synchronization remained visibly uncalibrated.
- Native testing exposed a focus issue when reopening an already-created Broadcast panel. `f844fee` activates it explicitly and adds **View → Broadcast controls… / ⌘⇧B** (Ctrl+Shift+B elsewhere). The installed replacement was verified returning from Program to the operator panel with output still running.
- The original 33 focused broadcast/OBS checks passed; the updated eight-test UI subset also passed, including the new focus regression, giving 34 distinct focused checks. [GitHub qualification for f844fee](https://github.com/MIT-Rocket-Team/RT_Telemetry_UI/actions/runs/41821289641) passed Windows, Apple Silicon Mac, Intel Mac and Linux. Packaging and strict deep signature verification passed.
- The rebuilt installed app connected to the actual authenticated OBS server. Its password field cleared, scene/status matched OBS, and recording state was correctly shown as ACTIVE/STOPPED.
- Recording started in OBS using **Rocket Local Rehearsal**, which plays the previously generated, explicitly labelled local reference movie, plus an isolated test-tone source. Microphones/system audio were not recorded; test-tone monitoring was off. Disconnecting app controls left recording active and changed app status to UNKNOWN. Reconnecting retained the existing recording. The app's **Stop recording** command finalized the file successfully, even while Program was stopped. A further reconnect while stopped did not start anything.
- A separate real-server run through the actual `OBSBridge` class successfully issued **StartRecord → active → StopRecord → stopped**, completing in 10.457 seconds, with streaming inactive throughout. This proves the bridge commands, not the panel's capture-readiness checklist.
- Main local recording: `~/Movies/Rocket Broadcast/Rocket-2026-11-04_06-31-36.mkv`, **34.566 s**, **1,035 decoded frames**, **1920×1080, 30 fps, H.264 High, limited-range Rec.709**, stereo **AAC 48 kHz**. Complete decode passed; the tone was non-silent (peak −24.2 dBFS, mean −37.4 dBFS). The container's combined bitrate was approximately 14.065 Mb/s. SHA-256: `3f3921eb9afdb0429b48c96ebe0aca70f2b3893d160a6d28f4081bd404fb901f`.
- OBS reported zero encoder-skipped frames for that recording; its process-lifetime render-skipped counter remained unchanged at 541,106 between the sampled initial/final states. The large pre-existing lifetime count is not attributed to this short test, and this does not claim a one-hour OBS load test.
- The [recorded fixture frame](assets/broadcast/obs-recording-fixture.png) comes from OBS's saved MKV. It shows playback of a synthetic reference movie, **not live Program capture**. Detailed local evidence is under `build/obs-live-rehearsal/`.
- Final OBS API verification confirmed **Rocket Broadcast → Emergency slate**, recording inactive and streaming inactive. The standalone publisher was stopped. The Mac locked again before final native UI cleanup; the app's clean Program standby window was left open in its isolated test session.

### Initial permission gate — subsequently resolved

macOS initially showed **Screen & System Audio Recording → OBS.app off**, leaving OBS's window list empty. Native capture testing paused at that gate. The user subsequently explicitly approved enabling the permission and completed the macOS authentication prompt. The next section supersedes the earlier pending-capture status. No privacy setting, lock or permission was bypassed.

## Live Program-window recording — 2026-11-04

OBS.app's capture permission was observed on after user authentication. OBS **33.0.1 / Rocket 1080p30 / Rocket Broadcast** captured the installed `f844fee` app's individual **Rocket Broadcast — Program** window. This test used the loopback synthetic publisher and an isolated app data directory, not live flight hardware or the user's normal mission session.

### Capture and operator controls

- Selected the specific Program window, disabled cursor capture, muted its audio and measured the source at **1920×1136**. A **56-pixel top crop**, scale **1:1**, and 1920×1080 canvas/output excluded the title bar. The saved audience frames contain no operator controls or desktop. Native rounded window-corner pixels remain at the extreme boundary; overlay content remains inside safe margins.
- Reused existing authenticated OBS control without changing or saving its password. The installed app's **Start local recording** and **Stop recording** buttons successfully started/finalized real live capture after the run-readiness confirmation. App status matched OBS. The **⌘⇧B** return-to-controls shortcut worked with Program open.
- Joined the labelled simulation, prepared/took AWAY1 digital and displayed the MIT Rocket Team logo, numbered moving video, current qualified fixture telemetry, source identity and an explicitly approximate, uncalibrated operator clock.
- Recorded an isolated stereo test tone with monitoring off. Screen-capture audio was muted; no microphone/range input was qualified. The tone is not proof of physical A/V alignment.

### Saved recording and fault checks

Main file: `~/Movies/Rocket Broadcast/Rocket-2026-11-04_09-25-39.mkv`.

- **208.433 s**, **6,253 decoded frames**, **1920×1080**, **30 fps**, H.264 High, limited-range Rec.709, stereo AAC LC **48 kHz**.
- Complete FFmpeg decode succeeded. Test-tone peak **−24.2 dBFS**, mean **−37.8 dBFS**.
- File size **367,918,847 bytes**; SHA-256 **`1ce4e1ed96ff389a803ddff527dbce72c723ff5962a0777714c45a7f57f9b553`**.
- Video disabled at recording 26.299 s, restored at 34.299 s; telemetry disabled at 46.299 s, restored at 60.299 s. A recorded 31 s frame shows the camera-unavailable placeholder with independent telemetry still populated; the 40 s frame shows recovered video. At 56 s the camera continues while stale telemetry is labelled and gauges are blank. At 85 s telemetry has recovered and the live display is in Metric. The earlier 8 s frame records Imperial units.
- The app's Emergency slate appears at the sampled 150 s frame. The independent OBS Emergency slate appears at 200 s after Program was stopped; recording continued until the app's Stop recording control finalized it. This exercises independence between Program and OBS output lifecycles.

The [machine-readable live capture record](assets/broadcast/obs-live-capture-verification.json) contains the timed events, artifact hashes and seven inspected saved-frame examples, including [Imperial](assets/broadcast/obs-live-imperial.png), [video loss](assets/broadcast/obs-live-video-lost.png), [recovery](assets/broadcast/obs-live-recovered.png), [telemetry loss](assets/broadcast/obs-live-telemetry-lost.png), [Metric](assets/broadcast/obs-live-metric.png), [app slate](assets/broadcast/obs-live-program-slate.png) and [independent OBS slate](assets/broadcast/obs-live-obs-slate.png). These are actual frames extracted from OBS's saved live-window recording, not compositor exports. The full video remains local; detailed logs are under `build/obs-program-capture/`.

### Performance limits retained

The first live diagnostic recording, `Rocket-2026-11-04_08-46-49.mkv`, was **not a clean performance pass**: OBS reported **1,619 encoder-skipped frames out of 9,624**, a control disconnect required reconnecting, and wall-clock discontinuity/audio-buffer warnings were observed. The cause was not isolated. Reconnection recovered the active state, and app Stop finalized that file. It is preserved locally.

The timed retake above was functionally successful, but OBS reported **17 encoder-skipped frames out of 6,290 (about 0.27%)** across the whole output interval. Its 60.002-second injected-fault segment had **zero encoder-skipped frames** and **eight added render-skipped frames**. Large pre-existing process-lifetime render counters are not attributed to this run. These observations do not establish a lossless one-hour complete OBS/audio workload.

### Outage UI correction and final state

The rehearsal exposed a draft-camera issue: removing a source from discovery reset Main/Inset to their defaults and cleared main crop, even though Program itself recovered correctly. **30a4299** retains missing selections with an `unavailable` label and preserves their crop; fresh continuous frames are still required before Take. Intentionally choosing another main camera still clears crop. The regression failed on the old code, then the complete focused broadcast/OBS suite passed **35 tests**, and Ruff passed. This final change affects the operator's draft selection, not composition or OBS commands.

The final build is installed in Applications, with the package smoke/signature checks listed above. [Desktop qualification for 30a4299](https://github.com/MIT-Rocket-Team/RT_Telemetry_UI/actions/runs/41849370216) tracks the four-platform checks.

OBS was left on **Rocket Broadcast → Emergency slate**, with recording and streaming confirmed inactive. Program and the rehearsal app were closed, the synthetic publisher was stopped, the test tone was disabled/muted, and Imperial display units were restored. On next launch, reselect the new Program window in OBS and verify its crop. No YouTube stream was started or key entered.

## Unfinished external qualification

The 2026-11-04 follow-ups resolved real OBS authentication/status, individual Program capture/crop on this Retina display, app-controlled recording Start/Stop and saved loss/recovery output. Remaining checks are:

- No physical microphone/range input, A/V offset or one-hour A/V drift was measured.
- The complete three-main Iris wall plus candidate, other external viewers and launch-site radio/uplink load still need the intended equipment.
- The long soak covers the local synthetic SRT/telemetry/compositor path, not OBS capture/audio or the real field network.
- Replay/simulation behavior has deterministic unit coverage and uses available explicit state; real archived-event timing and multi-speed hardware playback still require a representative production recording.
- YouTube activation/account, stream key, visibility, sustained upload, unlisted return and independent viewing-device checks remain intentionally untouched.

The software can prepare and preview locally without those services. The [operator guide](BROADCAST_OPERATOR_GUIDE.md) gives the remaining exact setup sequence. These gates are not equivalent to software installation failure, nor are they claimed complete.
