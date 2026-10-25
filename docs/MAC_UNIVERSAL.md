# macOS universal package

The universal distribution contains one application that runs natively on Apple Silicon and Intel Macs. A small universal executable selects one of two complete native application payloads. Each includes Python, Qt, FFmpeg, Java, and OpenRocket; production computers need no separately installed runtime. This universal distribution requires **macOS 14 or newer**, reflecting the bundled NumPy runtime's deployment target.

This is deliberately a universal launcher with architecture-specific payloads, rather than a claim that every bundled library is universal. Joining finished PyInstaller executables using `lipo` is unsupported because their embedded Python archives conflict. See the [PyInstaller architecture documentation](https://pyinstaller.org/en/stable/feature-notes.html#macos-multi-arch-support).

## Build

Build and verify the Apple Silicon and Intel packages separately first. Use an ARM Python environment with an ARM Java runtime for the first, and an Intel Python environment with an Intel Java runtime for the second. Intel builds can run under Rosetta on an Apple Silicon development Mac. Keep their staging directories separate so runtime files cannot be mixed.

On macOS with Xcode command-line tools installed:

```sh
make package-macos-universal \
  MAC_ARM64_APP="/path/to/arm64/RocketGNCMonitor-v0a.app" \
  MAC_X86_64_APP="/path/to/x86_64/RocketGNCMonitor-v0a.app"
```

Alternatively:

```sh
.venv/bin/python scripts/package_macos_universal.py \
  --arm64-app "/path/to/arm64/RocketGNCMonitor-v0a.app" \
  --x86_64-app "/path/to/x86_64/RocketGNCMonitor-v0a.app" \
  --output-dir dist
```

The builder verifies signatures, native dependency architectures, matching application versions, matching OpenRocket engines, and private Java runtime provenance. It preserves input apps, copies their symlinks with `ditto`, compiles the universal launcher, signs the nested apps before the outer app, checks both launcher slices, and validates the resulting disk image and ZIP. Testing both slices requires an Apple Silicon build Mac with Rosetta installed.

Outputs:

- `dist/RocketGNCMonitor-v0a-universal.app`
- `dist/RocketGNCMonitor-v0a-macOS-universal.dmg`
- `dist/RocketGNCMonitor-v0a-macOS-universal.zip`, containing the DMG
- SHA-256 checksum files for both archives

An existing universal app is replaced only if its bundle ID and universal build manifest identify it as a previous output of this builder. Native input bundles and other files in the output directory are preserved. `UNIVERSAL_OUTPUT=/path` overrides the Makefile destination.

## Signing and verification

By default the package uses ad-hoc signatures, consistent with the development packages. `MAC_SIGN_IDENTITY` selects an available signing identity; prepare both native inputs with the same identity when using a Developer ID certificate. This builder does not notarize the application and does not claim Gatekeeper approval. See [Apple's nested-code signing guidance](https://developer.apple.com/documentation/xcode/using-the-latest-code-signature-format).

The outer app and both payloads retain the same application identity and camera usage description. The launcher replaces itself with the native app using `execv`, preserving arguments, the process, exit status, and current directory. It resolves its own location, so installation in a path containing spaces works normally.

Before publishing, run the packaged station, recording, replay, virtual pointer, 3D, and OpenRocket checks on each native payload. Also test the outer launcher with both architecture selections and launch it from Finder after relocating it. Camera access should be checked with real USB cameras; synthetic test-pattern video does not verify macOS camera permissions. Intel execution through Rosetta validates the Intel payload on Apple Silicon but is not a substitute for an Intel hardware smoke test.

```sh
arch -arm64 dist/RocketGNCMonitor-v0a-universal.app/Contents/MacOS/RocketGNCMonitor-v0a --help
arch -x86_64 dist/RocketGNCMonitor-v0a-universal.app/Contents/MacOS/RocketGNCMonitor-v0a --help
codesign --verify --deep --strict dist/RocketGNCMonitor-v0a-universal.app
```
