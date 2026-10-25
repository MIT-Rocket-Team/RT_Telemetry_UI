"""Package two verified native apps behind a universal macOS launcher.

Finished PyInstaller executables cannot safely be joined with lipo: each carries
its own Python archive. This package keeps both native payloads intact instead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import struct
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "RocketGNCMonitor-v0a"
BUNDLE_ID = "edu.mit.rocketteam.gncmonitor.v0a"
OUTER_NAME = APP_NAME + "-universal.app"
ARTIFACT_NAME = APP_NAME + "-macOS-universal"
MANIFEST_NAME = "universal-build-manifest.json"
ARCHITECTURES = {"arm64", "x86_64"}


class PackageError(ValueError):
    """A package cannot safely be constructed from the supplied inputs."""


def run(*arguments, capture=False):
    arguments = list(map(str, arguments))
    print("Running:", " ".join(arguments), flush=True)
    return subprocess.run(arguments, check=True, capture_output=capture, text=True)


def sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def binary_architectures(path):
    return set(run("/usr/bin/lipo", "-archs", path, capture=True).stdout.split())


def is_macho(path):
    with path.open("rb") as handle:
        header = handle.read(8)
    if len(header) < 8:
        return False
    magic = header[:4]
    if magic in (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"):
        return True
    endian = {
        b"\xca\xfe\xba\xbe": ">",
        b"\xbe\xba\xfe\xca": "<",
        b"\xca\xfe\xba\xbf": ">",
        b"\xbf\xba\xfe\xca": "<",
    }.get(magic)
    # Java class files share CAFEBABE but have a version where fat Mach-O has
    # its small architecture count. Do not mistake .class resources for code.
    return bool(endian and 0 < struct.unpack(endian + "I", header[4:])[0] <= 16)


def validate_payload(app, architecture):
    if architecture not in ARCHITECTURES:
        raise PackageError(f"Unsupported architecture: {architecture}")
    app = Path(app).expanduser().resolve()
    if not app.is_dir() or app.suffix != ".app":
        raise PackageError(f"Expected an existing .app bundle: {app}")
    try:
        with (app / "Contents/Info.plist").open("rb") as handle:
            info = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as error:
        raise PackageError(f"Cannot read {app}/Contents/Info.plist: {error}") from error
    if info.get("CFBundleExecutable") != APP_NAME or info.get("CFBundleIdentifier") != BUNDLE_ID:
        raise PackageError(f"Not a {APP_NAME} application bundle: {app}")
    if not info.get("NSCameraUsageDescription"):
        raise PackageError(f"Missing camera permission description: {app}")
    executable = app / "Contents/MacOS" / APP_NAME
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise PackageError(f"Missing executable application launcher: {executable}")
    if architecture not in binary_architectures(executable):
        raise PackageError(f"Application executable does not support {architecture}: {executable}")
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", app)
    native_files = 0
    for directory, names, files in os.walk(app, followlinks=False):
        for name in names + files:
            candidate = Path(directory) / name
            if candidate.is_symlink() and not candidate.resolve().is_relative_to(app):
                raise PackageError(f"Payload symlink leaves the app bundle: {candidate}")
        for name in files:
            candidate = Path(directory) / name
            if candidate.is_symlink() or not candidate.is_file() or not is_macho(candidate):
                continue
            native_files += 1
            if architecture not in binary_architectures(candidate):
                raise PackageError(f"Native dependency does not support {architecture}: {candidate}")
    vendor = app / "Contents/Resources/vendor"
    try:
        build = json.loads((vendor / "build-manifest.json").read_text())
        engine = json.loads((vendor / "engine.json").read_text())
    except (OSError, ValueError) as error:
        raise PackageError(f"Missing or invalid package provenance in {vendor}: {error}") from error
    expected_runtime = "aarch64" if architecture == "arm64" else "x64"
    if build.get("architecture") != expected_runtime:
        raise PackageError(f"Build manifest architecture does not match {architecture}: {app}")
    if build.get("runtime", {}).get("arch") != expected_runtime:
        raise PackageError(f"Bundled Java architecture does not match {architecture}: {app}")
    if build.get("runtime", {}).get("os") != "mac":
        raise PackageError(f"Bundled Java is not a macOS runtime: {app}")
    for filename, key in (("openrocket.jar", "engine_sha256"), ("rocket-bridge.jar", "bridge_sha256")):
        if not (vendor / filename).is_file() or sha(vendor / filename) != engine.get(key):
            raise PackageError(f"Bundled {filename} does not match its recorded checksum: {app}")
    return dict(path=app, info=info, build=build, engine=engine, native_files=native_files,
                executable_sha256=sha(executable))


def validate_output(output, sources):
    output = Path(output).expanduser().absolute()
    # Refuse a symlink at any existing output ancestor; resolve the final location
    # before creating a temporary stage or replacing our known artifact names.
    for ancestor in (output, *output.parents):
        if ancestor.is_symlink():
            raise PackageError(f"Output directory must not traverse a symlink: {ancestor}")
    output = output.resolve()
    target = output / OUTER_NAME
    for source in sources:
        source = Path(source).resolve()
        if output.is_relative_to(source) or source.is_relative_to(target):
            raise PackageError("Output must not contain, replace, or be inside an input application")
    if target.is_symlink():
        raise PackageError(f"Refusing to replace a symlink: {target}")
    if target.exists():
        try:
            with (target / "Contents/Info.plist").open("rb") as handle:
                info = plistlib.load(handle)
            manifest = json.loads((target / "Contents/Resources" / MANIFEST_NAME).read_text())
        except (OSError, ValueError, plistlib.InvalidFileException) as error:
            raise PackageError(f"Refusing to replace an unrecognized existing application: {target}") from error
        if info.get("CFBundleIdentifier") != BUNDLE_ID or manifest.get("package_kind") != "universal-native-payloads":
            raise PackageError(f"Refusing to replace an unrecognized existing application: {target}")
    for suffix in (".dmg", ".zip", ".dmg.sha256", ".zip.sha256"):
        artifact = output / (ARTIFACT_NAME + suffix)
        if artifact.is_symlink() or (artifact.exists() and not artifact.is_file()):
            raise PackageError(f"Refusing to replace an invalid artifact path: {artifact}")
    return output


def sign_bundle(app, identity):
    arguments = ["/usr/bin/codesign", "--force", "--sign", identity]
    if identity != "-":
        arguments += ["--options", "runtime", "--timestamp"]
    arguments += ["--entitlements", ROOT / "packaging/entitlements.plist", app]
    run(*arguments)


def package(arm64_app, x86_64_app, output_dir):
    if platform.system() != "Darwin":
        raise PackageError("Build the universal macOS package on macOS with Xcode command-line tools")
    sources = {"arm64": Path(arm64_app).expanduser().resolve(), "x86_64": Path(x86_64_app).expanduser().resolve()}
    if sources["arm64"] == sources["x86_64"]:
        raise PackageError("Supply two separate native application bundles")
    output = validate_output(output_dir, sources.values())
    payloads = {arch: validate_payload(source, arch) for arch, source in sources.items()}
    first, second = payloads.values()
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        if not first["info"].get(key) or first["info"].get(key) != second["info"].get(key):
            raise PackageError(f"Input applications must have the same {key}")
    if first["engine"]["engine_sha256"] != second["engine"]["engine_sha256"]:
        raise PackageError("Input applications must bundle the same OpenRocket engine")
    output.mkdir(parents=True, exist_ok=True)
    identity = os.environ.get("MAC_SIGN_IDENTITY") or "-"
    with tempfile.TemporaryDirectory(prefix=".universal-stage-", dir=output) as temporary:
        stage = Path(temporary)
        app = stage / OUTER_NAME
        contents = app / "Contents"
        for directory in ("MacOS", "Resources", "Helpers"):
            (contents / directory).mkdir(parents=True, exist_ok=True)
        for arch, source in sources.items():
            helper = contents / "Helpers" / f"RocketGNCMonitor-{arch}.app"
            run("/usr/bin/ditto", "--noqtn", source, helper)
            # Preserve the entire signed native payload; sign only its bundle
            # envelope before enclosing it in the outer application signature.
            sign_bundle(helper, identity)
            run("/usr/bin/codesign", "--verify", "--deep", "--strict", helper)
        info = dict(first["info"])
        info["CFBundleExecutable"] = APP_NAME
        info["CFBundleName"] = APP_NAME
        info["CFBundleDisplayName"] = "Rocket GNC Monitor v0a"
        info["LSArchitecturePriority"] = ["arm64", "x86_64"]
        info["LSMinimumSystemVersion"] = max(
            ["14.0", *(payload["info"].get("LSMinimumSystemVersion", "14.0")
                       for payload in payloads.values())],
            key=lambda value: tuple(map(int, value.split("."))),
        )
        with (contents / "Info.plist").open("wb") as handle:
            plistlib.dump(info, handle)
        icon = first["info"].get("CFBundleIconFile", "icon.icns")
        if Path(icon).name != icon:
            raise PackageError("Application icon must be a filename within Contents/Resources")
        shutil.copy2(sources["arm64"] / "Contents/Resources" / icon, contents / "Resources" / icon)
        launcher = contents / "MacOS" / APP_NAME
        run("/usr/bin/xcrun", "clang", "-arch", "arm64", "-arch", "x86_64",
            "-mmacosx-version-min=13.0", "-O2", "-Wall", "-Wextra", "-Werror",
            ROOT / "packaging/universal_launcher.c", "-o", launcher)
        if binary_architectures(launcher) != ARCHITECTURES:
            raise PackageError("Universal launcher does not contain both required architectures")
        manifest = dict(
            schema_version=1, package_kind="universal-native-payloads", application=APP_NAME,
            version=info["CFBundleShortVersionString"], architectures=sorted(ARCHITECTURES),
            signing="ad-hoc" if identity == "-" else identity, notarization="not performed by this builder",
            launcher_pre_sign_sha256=sha(launcher),
            payloads={arch: dict(source=str(item["path"]), source_executable_sha256=item["executable_sha256"],
                                 native_files_checked=item["native_files"], build=item["build"])
                      for arch, item in payloads.items()},
        )
        (contents / "Resources" / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")
        sign_bundle(app, identity)
        run("/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", app)
        # Both slices must launch and return their argparse help successfully.
        # The full station/video/simulation suite is a separate release check.
        for arch in sorted(ARCHITECTURES):
            run("/usr/bin/arch", "-" + arch, launcher, "--help")
        dmg = stage / (ARTIFACT_NAME + ".dmg")
        run("/usr/bin/hdiutil", "create", "-volname", "Rocket GNC Monitor v0a",
            "-srcfolder", app, "-ov", "-format", "UDZO", dmg)
        run("/usr/bin/hdiutil", "verify", dmg)
        archive = stage / (ARTIFACT_NAME + ".zip")
        # The DMG already preserves app metadata and is compressed. Store it at
        # the ZIP root, without exposing the hidden temporary staging folder.
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zipped:
            zipped.write(dmg, arcname=dmg.name)
        with zipfile.ZipFile(archive) as zipped:
            if zipped.namelist() != [dmg.name] or zipped.testzip() is not None:
                raise PackageError("Universal ZIP did not preserve the root-level installer")
        for artifact in (dmg, archive):
            (stage / (artifact.name + ".sha256")).write_text(sha(artifact) + "  " + artifact.name + "\n")
        # Leave any previous known-good result in place until the full new build
        # and its archives pass. Never remove arbitrary paths supplied by users.
        target = output / OUTER_NAME
        previous = stage / "previous.app"
        if target.exists():
            target.rename(previous)
        try:
            app.rename(target)
        except OSError:
            if previous.exists():
                previous.rename(target)
            raise
        for artifact in (dmg, archive, stage / (dmg.name + ".sha256"), stage / (archive.name + ".sha256")):
            os.replace(artifact, output / artifact.name)
    print("Created", output / OUTER_NAME)
    print("Created", output / (ARTIFACT_NAME + ".zip"))
    return output / OUTER_NAME


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm64-app", type=Path, required=True)
    parser.add_argument("--x86_64-app", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        package(args.arm64_app, args.x86_64_app, args.output_dir)
    except (PackageError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Universal package failed: {error}\n")


if __name__ == "__main__":
    main()
