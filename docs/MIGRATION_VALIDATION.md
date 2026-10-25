# Standalone migration validation

Validation date: **25 October 2026**. This record covers migration into `MIT-Rocket-Team/RT_Telemetry_UI`; it does not claim new hardware acceptance. These results describe the original migration builds, before subsequent mode and interface simplification; they are not validation results for later changes.

## Scope and provenance

- Original application: `MDNich/MIT-RT-26_27-Work`, `ui_work/rocket_UI_v0a`, application commit `b4494d43fffeb2de61d7273285ce950957898480`.
- Initial standalone migration: `202e97c`. Final source used for the Mac packages: **`8edfdb04b503cba7a73b585d012cb63402dc7d2e`**.
- Retained application modules, tests/fixtures, Java bridge source, essential runtime resources, hardware references, dependency locks, Makefiles/packaging scripts and current documentation/licenses. Application version `0.1.1`, executable names, preferences identity and saved-file formats remain unchanged.
- Documentation was updated for the current Launch/Away roles and protocol limitations. The build-only follow-up isolates PyInstaller caches so concurrent native builds cannot remove one another's cached files. Commit `b61a153` also makes an existing virtual-pointer UI test wait for exposed, visible controls before real mouse clicks; all behavior assertions remain intact. Application modules are unchanged.
- Omitted old planning/audit documents, historical validation captures, the superseded v0 LaTeX report, copied environments, vendor binaries, build outputs and unrelated monorepo content. The original repository is preserved. Generated environments, runtimes and packages remain ignored.
- The Mac build input inventory covers 164 tracked files. Its generated `source-manifest.json` SHA-256 is `3290ccc673392a260c870498f7af94291692c8693bf15a003d21a8a193044592`.

## Completed macOS checks

| Check | Result |
| --- | --- |
| Apple Silicon regression suite | **578 passed**, no skips; offscreen Qt for unit/integration tests. |
| Intel regression suite under Rosetta | **573 passed, 5 skipped** in the full run; a subsequent prepared-runtime simulation run passed all **5** simulation tests. |
| Intel skip reconciliation | Four runtime-dependent skipped cases were covered by the subsequent simulation run. The remaining ARM-host universal-launcher case passed in the Apple Silicon suite; **577 Intel-applicable cases** covered. |
| Packaged Apple Silicon execution | **20 checks passed** through the universal launcher with `arm64` selection. |
| Packaged Intel execution | **20 checks passed** through the same universal launcher with `x86_64` selection under Rosetta. |
| Relocation/runtime independence | Both packaged runs used a fresh path containing spaces and a minimal system PATH, with bundled Python/Qt/FFmpeg/Java/OpenRocket. |
| Final DMG audit | Mounted contents match the relocated verified app: **1,743 files/symlinks** checked. Deep/strict code-signature verification passed. |

Packaged checks include startup/wizard, station and video-role inventories, locked LIVE mode, all three recorded test-flight sources, portable flights, URRG, virtual pointing, 3D ignition/recovery events and both example/OpenRocket models. They exercise the private OpenRocket-MIT **v6.2** engine. Detailed local evidence was produced under ignored `build/migration/`; this compact record retains the acceptance results without committing captures or generated sessions.

Final Mac artifact:

- Filename: `RocketGNCMonitor-v0a-macOS-universal.dmg`
- Size: **410,713,509 bytes**
- SHA-256: `acef899cf3b5eb2b43c402ce79c328df341f01db1d6c589c2eae45ad1eb862bc`
- Signing: **ad-hoc**, not notarized.

## Completed Windows checks

The delivered x64 ZIP was built from **`b61a1533f17541070b68eea6a8d2a97beffe2c09`** on Windows Server 2025 with x64 Python **3.13.16**, native desktop Qt and bundled Temurin **17.0.20.1+1**. The Mac/Windows production application code is identical; later commits contain workflow, documentation and test changes.

| Check | Result |
| --- | --- |
| Clean Windows regression suite | **567 passed, 11 platform-specific skips**; lint passed. |
| Relocated packaged execution | **20 checks passed**, using a fresh path containing spaces and minimal system PATH. |
| Archive/runtime audit | ZIP checksum and CRC valid; all **243** EXE/DLL/PYD binaries are AMD64; embedded engine, bridge and FFmpeg hashes match their manifests. |
| Additional Windows 11 VM source run | **567 passed, 11 skipped** with x64 Python 3.13.14 under Windows ARM emulation and the v6.2 engine. The revised virtual-pointer test module also passed all **7** tests. |
| Supplemental VM package | A separate local build of `8edfdb0` with OpenRocket **v6.4** passed all **20** packaged checks under Windows ARM x64 emulation. The delivered ZIP remains the clean-runner v6.2 build below. |

[Successful clean Windows build and verification](https://github.com/MIT-Rocket-Team/RT_Telemetry_UI/actions/runs/40213280209). The initial CI attempt exposed the native-layout timing issue in the test described above; the full rerun passed. No application behavior was changed to make that test pass.

The supplemental VM package initially returned exit 1 on its video startup check without a diagnostic trace. The same executable subsequently passed that check with both local and shared-folder output. An intermediate full run was interrupted when the guest execution transport disconnected; the final detached run completed all 20 checks in 164 seconds. The cause of the initial transient remains unconfirmed, and no production code was changed for it.

Final Windows artifact:

- Filename: `RocketGNCMonitor-v0a-Windows-x64.zip`
- Size: **218,134,349 bytes**
- SHA-256: `5977838d86aaa9e32f57d717728c593f0e04713905d2b6dfc5b5ed912dba79f7`
- Engine: **OpenRocket-MIT v6.2**, matching the tested universal Mac baseline.
- Signing: unsigned. Keep the complete extracted folder together.

The local `dist/` directory retains the two final platform archives and checksum sidecars. Detailed evidence is kept in compact ignored verification archives; generated recordings and duplicate build trees are excluded from Git. A separately generated native Mac build using OpenRocket v6.4 is preserved and is not the universal DMG listed here.

## Limits

Intel execution was tested **under Rosetta on Apple Silicon**, not on a physical Intel Mac. Packaged checks used native desktop Qt, while the Apple Silicon regression suite used offscreen Qt. Real USB cameras, serial boards, antenna mechanics, APRS radio and station/LTU network hardware were not connected or validated. Undefined switch/transport protocols remain placeholders. No Linux build or Linux support claim is made. These checks do not establish correct layout at every window size or display scale; the reported small-window layout issues remain tracked separately in GitHub issues #2 and #4.
