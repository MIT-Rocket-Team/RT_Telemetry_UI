# Build and distribution

Run commands from the repository root. The application remains `RocketGNCMonitor-v0a`, version `0.1.1`, with macOS bundle identifier `edu.mit.rocketteam.gncmonitor.v0a`. This migration changes repository layout, not application identity or saved-file formats.

## Build prerequisites

- Native 64-bit Python 3.12–3.13. Windows requires x64 Python even on an ARM host; use `sysconfig.get_platform()` to confirm `win-amd64`.
- JDK 17 or newer, with `javac` and `jar` on PATH, for compiling the OpenRocket bridge. A private Java 17 runtime is separately embedded for production use.
- GNU Make for the Makefile entry points, or the equivalent Python commands below.
- macOS packaging requires Xcode command-line tools. The distributed app requires macOS 14 or newer.

PyInstaller builds on the target operating system. Build Apple Silicon and Intel Mac payloads in separate architecture-specific environments; Intel builds can run through Rosetta on Apple Silicon. Windows x64 can build/run through x64 emulation in Windows ARM. Never copy `.venv` or `vendor/java` between operating systems or architectures.

Each checkout keeps its PyInstaller cache under `build/pyinstaller-cache`, so simultaneous native builds in separate working folders cannot erase each other's cached binaries. An explicit `PYINSTALLER_CONFIG_DIR` overrides this default; use a different directory for each concurrent build.

## Prepare and run

```sh
# macOS
make setup PYTHON=python3.13
make run
make test
make lint
```

```powershell
# Windows PowerShell, without Make
py -3.13 scripts/build.py setup
.venv\Scripts\python.exe -m rocket_gnc_monitor
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check src tests --select F
```

`setup` creates a local `.venv`, installs exact versions from `requirements-build.txt`, and installs this checkout in editable mode. `uv sync --locked` is an alternative for developers using `uv`; the lockfile is retained. Initial dependency/runtime downloads need internet access. For offline builds, stage matching wheels and the architecture-specific vendor files beforehand.

## Prepare OpenRocket and Java

The bridge requires the team's complete `OpenRocket-MIT-vX.Y.jar` or compatible development `swing-*-all.jar`, including motor/resources databases and fork-specific control fields. An arbitrary upstream OpenRocket JAR is not interchangeable.

```sh
# Pin the engine used for this application baseline:
make runtime
make bridge OPENROCKET_DOWNLOAD=v6.2
# Or stage a local team build:
make bridge OPENROCKET_JAR="/absolute/path/to/OpenRocket-MIT-v6.2.jar"
# Optional: resolve the newest stable team release at build time:
make bridge OPENROCKET_DOWNLOAD=latest
```

Choose either `OPENROCKET_JAR` or `OPENROCKET_DOWNLOAD`. The downloader queries `MDNich/ActiveControl_MIT_RktTeam`, selects the exact `OpenRocket-MIT-vX.Y.jar` asset, checks its size and published SHA-256 when available, and records provenance in `vendor/engine.json`. A missing published digest is reported; the local hash is still recorded. Failed preparation preserves the previously staged engine/bridge. Pin a tested version for production: future engine changes may require bridge changes.

`make runtime` checks the private runtime's OS/architecture or downloads a checksum-verified Temurin Java 17 runtime. Run it before reusing staged files. The build records the runtime manifest and keeps its legal notices. Engine and runtime preparation happen at build time; changing the running app's OpenRocket setting does not download anything.

## Native packaging

```sh
# macOS
make -f Makefile.macos all OPENROCKET_DOWNLOAD=v6.2
# Windows with GNU Make (use PYTHON="py -3.13" for setup)
make -f Makefile.windows all OPENROCKET_DOWNLOAD=v6.2
```

An engine option stages the engine, recompiles the bridge and prepares Java. With neither option, packaging reuses `vendor`; for that path run `make runtime` and `make bridge` first. Use `make package-macos` or `make package-windows` to package without the surrounding test/verification targets.

Equivalent Windows preparation and packaging:

```powershell
.venv\Scripts\python.exe scripts/build.py runtime
.venv\Scripts\python.exe scripts/build.py bridge --download v6.2
.venv\Scripts\python.exe scripts/build.py package --platform Windows
.venv\Scripts\python.exe scripts/verify_package.py --model tests\fixtures\zephy_testlaunch.ork
```

Outputs are ignored under `dist/`:

| Platform | App and archive |
| --- | --- |
| Mac ARM64 | `RocketGNCMonitor-v0a.app`, `RocketGNCMonitor-v0a-Darwin-arm64.dmg` |
| Mac Intel | `RocketGNCMonitor-v0a.app`, `RocketGNCMonitor-v0a-Darwin-x86_64.dmg` |
| Windows x64 | `RocketGNCMonitor-v0a/`, `RocketGNCMonitor-v0a-Windows-x64.zip` |

Archive outputs have `.sha256` sidecars. Copy the complete Windows directory, including `_internal`, rather than its executable alone. The packages embed Qt/Python, the native MGRS converter, Lucida Grande, FFmpeg, Java and the team engine. Device drivers and OS camera permission remain machine-specific. See [macOS universal packaging](MAC_UNIVERSAL.md) for combining separately verified native Mac payloads.

## Verification

Run `make test` and `make lint`, then verify the actual distributable after relocating/extracting it to a fresh path (include spaces in the path). The package verifier removes developer runtime environment variables and restricts PATH to system directories:

```sh
.venv/bin/python scripts/verify_package.py \
  --package "/path with spaces/RocketGNCMonitor-v0a.app" \
  --model tests/fixtures/zephy_testlaunch.ork \
  --output build/package-verification
```

On Windows replace the interpreter with `.venv\Scripts\python.exe` and `--package` with the extracted `RocketGNCMonitor-v0a` directory. Use native desktop Qt for Windows verification; do not set `QT_QPA_PLATFORM=offscreen`. On Mac, offscreen Qt is useful for isolated unit tests but does not replace native visual/package checks.

The verifier checks the startup wizard, station roles, camera inventories, locked LIVE startup, all three demo recordings, portable flights, URRG, virtual pointing, simulation and 3D events. Its reports/logs/screenshots go to the selected output directory. No serial hardware opens. Synthetic video does not establish real USB receiver compatibility; physical board/camera/network acceptance is separate.

The package keeps `vendor/build-manifest.json`, engine/bridge hashes, Java provenance, FFmpeg configuration, dependency metadata and notices. Simulation jobs also retain their exact model/motor inputs, request, output and worker log. Rebuild the bridge after editing `simulation_bridge/src/RocketBridge.java`.

## Manual Windows CI build

The manual [Windows workflow](../.github/workflows/build-windows.yml) is a fallback when no local Windows build machine is available. In GitHub **Actions → Build and verify Windows package → Run workflow**, choose the source branch and run it. It uses a fresh `windows-2025` runner, x64 Python 3.13, Temurin JDK 17 and the pinned dependency file, prepares OpenRocket-MIT v6.2, runs the full tests/lint, then builds and verifies the extracted package with native Windows Qt and a minimal system PATH.

On success, download **Windows-x64-package** for the ZIP and checksum. **Windows-x64-verification** contains source hashes, runner/runtime provenance and test/package logs, including available failure logs. Both artifacts expire after **one day**; download deliverables promptly. The workflow creates no release and runs only when manually requested. Its hosted runner validates software behavior, not physical USB cameras/boards or the launch network. Actual native Python/Java versions and the checked-out commit are recorded with each run.

## Signing and cleanup

Mac development packages use ad-hoc signing. `MAC_SIGN_IDENTITY` selects a configured Developer ID identity; notarization is a separate distribution step. Windows packages are unsigned unless a separate signing process is applied. No signing credentials belong in the repository.

`make clean` removes this checkout's `build/` and `dist/`, including finished packages. Remove only `build/` to retain the final deliverables. `.venv/` and `vendor/` are reusable local caches, not source files; they remain ignored. Avoid committing package ZIPs/DMGs, runtime binaries, test captures or copied virtual environments.
