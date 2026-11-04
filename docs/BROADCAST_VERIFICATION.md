# Broadcast implementation and verification

Implementation date: 2026-11-03, America/New_York. This report separates implemented behavior, locally measured evidence and equipment/account-dependent qualification. **No real YouTube stream was started.**

## Source milestones

- `5190c9f`: immutable broadcast models, branded renderer, bounded timing histories, shared frame subscriptions, canonical observation signal and per-vehicle receiver ranking. Focused core/media/network suite: 36 passed. The pre-existing settings shortcut test passed in isolation after its baseline timeout. A network-test helper was corrected to tolerate an unresolved peer identity while discovery is in flight.
- `23b158a`: Base Video production UI/controller, scene and Preview/Take workflow, mission-clock/event controls, source handling, authenticated local OBS control/status, native UI tests and real SRT rehearsal harness. 21 focused checks passed, including no duplicate/replayed OBS starts.
- Subsequent hardening adds explicit independent decoder ownership tests, renderer-error slate, bounded telemetry-node history, native-package smoke entry point, OBS presets and the operator guide. See Git history for the final revision.

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

The baseline before changes was **886 passed, 1 skipped, 1 settings-shortcut focus timeout**. A later native full suite was **907 passed, 1 skipped, 2 keyboard-focus timeouts** while the Mac was locked. Both failing shortcut cases subsequently passed in an offscreen targeted run. The complete offscreen suite passed **910 tests, 1 skipped** in 224.65 seconds. Later focused hardening tests are recorded with the final revision.

Focused tests cover canonical field suppression/scope rejection, observation deduplication and actual arrival, no-future selection, buffer limits and epoch retirement, per-vehicle Best ranking/tie retention, timing admission, countdown/hold/actual liftoff, independent camera and telemetry choice, Take readiness, stale/slate/recovery, pause/transport loss, settings restart behavior, receiver lease ownership, renderer failure recovery, native panel lifecycle and OBS authentication/status/control behavior.

OBS tests use an authenticated local WebSocket test server. They verify start/stop recording requests, status updates, wrong-password behavior, stale/disconnected control rejection and no Start replay after reconnect. They **do not establish a real OBS-to-YouTube connection**.

The local transport rehearsal (`scripts/verify_broadcast.py`) publishes numbered synthetic camera frames through the actual HTTP/SRT/H.264 stack and canonical telemetry through the station runtime. A Base Video owner receives both, composes Program and writes separate production metrics. It creates a short 30 fps reference MKV from sampled rendered frames; that file is a reference artifact, not a claim of an OBS-captured recording.

- Initial 35 s transport smoke: 940 Program frames, 28.14 average fps including synchronous image-save overhead; no broadcast buffer or archive drops. Image saves were subsequently moved off the GUI thread.
- One-hour local transport/compositor soak: **running; final metrics to be recorded before handoff**. Started from milestone `23b158a`; later hardening has separate focused tests.
- Native source smoke: bundled logo loaded; Program raster and device-pixel content both 1920×1080; no hardware workers or OBS connection opened.
- Native packaged app: **17 existing package checks passed**, including startup/station/video wall, Sustainer/Booster playback and bundled OpenRocket; the separate broadcast smoke passed with 1920×1080 raster/content, logo loaded, no hardware workers and no OBS connection. Final relocated installation evidence follows below.

Generated detailed reports, captured Program frames and reference movies are under `build/broadcast-*`. Build products are under `dist/`. The final local artifact hash and installation/backup paths are recorded below after verification.

## Unfinished external qualification

The Mac locked during OBS setup. Native computer control reported that manual unlock is required. OBS also presented first-run screen/microphone permission setup, which was left unchanged. Consequently:

- OBS's actual Program-window capture, crop and Retina behavior have not been verified through an OBS recording.
- Actual OBS local start/stop via WebSocket remains unqualified until its local server is enabled/authenticated. The app's implementation is protocol-tested; no OBS authentication settings or credentials were silently changed.
- No physical microphone/range input, A/V offset or one-hour A/V drift was measured.
- The complete three-main Iris wall plus candidate, other external viewers and launch-site radio/uplink load still need the intended equipment.
- The long soak covers the local synthetic SRT/telemetry/compositor path, not OBS capture/audio or the real field network.
- Replay/simulation behavior has deterministic unit coverage and uses available explicit state; real archived-event timing and multi-speed hardware playback still require a representative production recording.
- YouTube activation/account, stream key, visibility, sustained upload, unlisted return and independent viewing-device checks remain intentionally untouched.

The software can prepare and preview locally without those services. The [operator guide](BROADCAST_OPERATOR_GUIDE.md) gives the remaining exact setup sequence. These gates are not equivalent to software installation failure, nor are they claimed complete.
