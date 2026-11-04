# Broadcast implementation and verification

Implementation date: 2026-11-03, America/New_York. This report separates implemented behavior, locally measured evidence and equipment/account-dependent qualification. **No real YouTube stream was started.**

## Source milestones

- `5190c9f`: immutable broadcast models, branded renderer, bounded timing histories, shared frame subscriptions, canonical observation signal and per-vehicle receiver ranking. Focused core/media/network suite: 36 passed. The pre-existing settings shortcut test passed in isolation after its baseline timeout. A network-test helper was corrected to tolerate an unresolved peer identity while discovery is in flight.
- `23b158a`: Base Video production UI/controller, scene and Preview/Take workflow, mission-clock/event controls, source handling, authenticated local OBS control/status, native UI tests and real SRT rehearsal harness. 21 focused checks passed, including no duplicate/replayed OBS starts.
- `35325bc`: independent decoder ownership tests, renderer-error slate, bounded telemetry-node history, native-package smoke entry point, OBS presets and the operator guide.
- `4d2edc9`: production hardening milestone. Adds packaged OBS preset export with independent slate assets, replay-seek invalidation, pause/resume clock behavior, calibration identity checks, operator clock labels, recovery tests and cross-platform CI coverage. Broadcast implementation/scripts pass Ruff. The final `16e0fd3` application revision retains actual liftoff controls when custom timeline labels omit LIFTOFF; its broadcast/OBS focused suite passed **33 tests**. Later commits contain verification tooling and evidence only.

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

- App: `/Applications/RocketGNCMonitor-v0a.app`, source revision `16e0fd3`.
- Previous app retained at `build/application-backups/20261103-before-broadcast/RocketGNCMonitor-v0a.app`.
- Distribution: `dist/RocketGNCMonitor-v0a-Darwin-arm64.dmg` (Apple Silicon macOS).
- DMG SHA-256: `4f6fcc65cdd708ea5e093a2f6e33f04bf125acca87d3323faf0cf20e4a71355b`.
- Installed executable SHA-256: `150b392f30b097dd50016c52091d44f0505ac2c27b650542e2c2426cdf353328`.
- The 4d2edc9 app passed all **17 package checks**. After the final custom-timeline UI correction, the installed 16e0fd3 `/Applications` copy independently produced **52 frames**, loaded the logo, exported the bundled OBS profiles/slate, reported a **1920×1080** raster and content area, and opened no hardware or OBS connection.
- macOS Launch Services registration was refreshed for the installed path. The old Dock shortcut can continue pointing at that app.
- The preceding package encountered a transient disk-image “resource busy” error; retrying only disk-image creation succeeded. The final 16e0fd3 package completed the full build/DMG/checksum sequence successfully.

The separate Windows archive already in `dist/` predates this implementation and is not part of this delivery. Cross-platform CI success does not relabel an old binary as newly built.

## Unfinished external qualification

The Mac locked during OBS setup. Native computer control reported that manual unlock is required, and a final access check confirmed it was still locked. OBS also presented first-run screen/microphone permission setup, which was left unchanged. Consequently:

- OBS's actual Program-window capture, crop and Retina behavior have not been verified through an OBS recording.
- Actual OBS local start/stop via WebSocket remains unqualified until its local server is enabled/authenticated. The app's implementation is protocol-tested; no OBS authentication settings or credentials were silently changed.
- No physical microphone/range input, A/V offset or one-hour A/V drift was measured.
- The complete three-main Iris wall plus candidate, other external viewers and launch-site radio/uplink load still need the intended equipment.
- The long soak covers the local synthetic SRT/telemetry/compositor path, not OBS capture/audio or the real field network.
- Replay/simulation behavior has deterministic unit coverage and uses available explicit state; real archived-event timing and multi-speed hardware playback still require a representative production recording.
- YouTube activation/account, stream key, visibility, sustained upload, unlisted return and independent viewing-device checks remain intentionally untouched.

The software can prepare and preview locally without those services. The [operator guide](BROADCAST_OPERATOR_GUIDE.md) gives the remaining exact setup sequence. These gates are not equivalent to software installation failure, nor are they claimed complete.
