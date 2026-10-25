import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess

import pytest


# This builder validates macOS app bundles and their POSIX symlink layout.
# Windows hosts neither build these bundles nor need symlink privileges.
pytestmark = pytest.mark.skipif(platform.system() != "Darwin", reason="macOS-only package builder")


@pytest.fixture
def universal():
    path = Path(__file__).resolve().parents[1] / "scripts/package_macos_universal.py"
    spec = importlib.util.spec_from_file_location("rocket_universal_build", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payload_fixture(tmp_path, universal, architecture="arm64"):
    app = tmp_path / "Source app.app"
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    info = dict(CFBundleExecutable=universal.APP_NAME, CFBundleIdentifier=universal.BUNDLE_ID,
                CFBundleShortVersionString="0.1.1", CFBundleVersion="2", NSCameraUsageDescription="Camera")
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    main = contents / "MacOS" / universal.APP_NAME
    main.write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 12)
    main.chmod(0o755)
    native = contents / "Frameworks/native.dylib"
    native.parent.mkdir()
    native.write_bytes(b"\xcf\xfa\xed\xfe" + b"\0" * 12)
    vendor = contents / "Resources/vendor"
    vendor.mkdir(parents=True)
    runtime = "aarch64" if architecture == "arm64" else "x64"
    (vendor / "build-manifest.json").write_text(json.dumps(dict(
        architecture=runtime, runtime=dict(arch=runtime, os="mac"))))
    (vendor / "openrocket.jar").write_bytes(b"engine")
    (vendor / "rocket-bridge.jar").write_bytes(b"bridge")
    (vendor / "engine.json").write_text(json.dumps(dict(
        engine_sha256=universal.sha(vendor / "openrocket.jar"),
        bridge_sha256=universal.sha(vendor / "rocket-bridge.jar"))))
    return app


def test_rejects_wrong_architecture_native_dependency(universal, tmp_path, monkeypatch):
    app = payload_fixture(tmp_path, universal)
    monkeypatch.setattr(universal, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(universal, "binary_architectures",
                        lambda path: {"x86_64"} if path.name == "native.dylib" else {"arm64"})
    with pytest.raises(universal.PackageError, match="Native dependency does not support arm64"):
        universal.validate_payload(app, "arm64")


def test_valid_payload_is_read_only_and_rejects_external_symlink(universal, tmp_path, monkeypatch):
    app = payload_fixture(tmp_path, universal)
    before = {path.relative_to(app): path.read_bytes() for path in app.rglob("*") if path.is_file()}
    calls = []
    monkeypatch.setattr(universal, "run", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(universal, "binary_architectures", lambda path: {"arm64"})
    result = universal.validate_payload(app, "arm64")
    assert result["native_files"] == 2
    assert calls[0][1:4] == ("--verify", "--deep", "--strict")
    assert before == {path.relative_to(app): path.read_bytes() for path in app.rglob("*") if path.is_file()}
    outside = tmp_path / "external-library"
    outside.write_bytes(b"external runtime")
    (app / "Contents/Frameworks/external.dylib").symlink_to(outside)
    with pytest.raises(universal.PackageError, match="symlink leaves"):
        universal.validate_payload(app, "arm64")


def test_rejects_wrong_java_runtime_and_modified_engine(universal, tmp_path, monkeypatch):
    app = payload_fixture(tmp_path, universal)
    monkeypatch.setattr(universal, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(universal, "binary_architectures", lambda path: {"arm64"})
    vendor = app / "Contents/Resources/vendor"
    manifest = vendor / "build-manifest.json"
    original = manifest.read_text()
    manifest.write_text(json.dumps(dict(architecture="aarch64", runtime=dict(arch="x64", os="mac"))))
    with pytest.raises(universal.PackageError, match="Java architecture"):
        universal.validate_payload(app, "arm64")
    manifest.write_text(original)
    (vendor / "openrocket.jar").write_bytes(b"unexpected engine")
    with pytest.raises(universal.PackageError, match="recorded checksum"):
        universal.validate_payload(app, "arm64")


def test_output_guard_preserves_inputs_and_unknown_existing_app(universal, tmp_path):
    source = tmp_path / "Native.app"
    source.mkdir()
    with pytest.raises(universal.PackageError, match="inside an input"):
        universal.validate_output(source / "dist", [source])
    output = tmp_path / "dist"
    target = output / universal.OUTER_NAME
    target.mkdir(parents=True)
    sentinel = target / "keep-me"
    sentinel.write_text("untouched")
    with pytest.raises(universal.PackageError, match="unrecognized"):
        universal.validate_output(output, [source])
    assert sentinel.read_text() == "untouched"
    with pytest.raises(universal.PackageError, match="replace"):
        universal.validate_output(output, [target])


def test_output_guard_rejects_symlink_artifact_and_accepts_known_output(universal, tmp_path):
    output = tmp_path / "dist"
    target = output / universal.OUTER_NAME
    (target / "Contents/Resources").mkdir(parents=True)
    (target / "Contents/Info.plist").write_bytes(plistlib.dumps(dict(CFBundleIdentifier=universal.BUNDLE_ID)))
    (target / "Contents/Resources" / universal.MANIFEST_NAME).write_text(
        json.dumps(dict(package_kind="universal-native-payloads")))
    assert universal.validate_output(output, []) == output.resolve()
    protected = tmp_path / "important.zip"
    protected.write_text("important")
    (output / (universal.ARTIFACT_NAME + ".zip")).symlink_to(protected)
    with pytest.raises(universal.PackageError, match="invalid artifact"):
        universal.validate_output(output, [])
    assert protected.read_text() == "important"


def test_macho_detection_does_not_misidentify_java_class_files(universal, tmp_path):
    file = tmp_path / "binary"
    file.write_bytes(b"\xca\xfe\xba\xbe\x00\x00\x00\x34")
    assert not universal.is_macho(file)
    file.write_bytes(b"\xca\xfe\xba\xbe\x00\x00\x00\x02")
    assert universal.is_macho(file)


@pytest.mark.skipif(platform.system() != "Darwin", reason="Native macOS launcher integration")
def test_launcher_selects_native_payload_preserves_arguments_cwd_and_exit(universal, tmp_path):
    if platform.machine() != "arm64":
        pytest.skip("Both launch architectures need an Apple Silicon test host")
    if subprocess.run(["/usr/bin/arch", "-x86_64", "/usr/bin/true"]).returncode:
        pytest.skip("Rosetta is needed to test the Intel launcher slice")
    app = tmp_path / "Relocated folder with spaces" / universal.OUTER_NAME
    macos = app / "Contents/MacOS"
    macos.mkdir(parents=True)
    launcher = macos / universal.APP_NAME
    subprocess.run(["/usr/bin/xcrun", "clang", "-arch", "arm64", "-arch", "x86_64",
                    "-Wall", "-Wextra", "-Werror", str(universal.ROOT / "packaging/universal_launcher.c"),
                    "-o", str(launcher)], check=True)
    helper_source = tmp_path / "echo_arguments.c"
    helper_source.write_text(
        '#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include <unistd.h>\n'
        'int main(int argc, char **argv) {\n'
        ' char *cwd = getcwd(NULL, 0); puts(cwd); free(cwd);\n'
        ' for (int i=0; i<argc; ++i) fwrite(argv[i], 1, strlen(argv[i])+1, stdout);\n'
        ' return 37;\n}\n')
    for arch in sorted(universal.ARCHITECTURES):
        helper = app / f"Contents/Helpers/RocketGNCMonitor-{arch}.app/Contents/MacOS" / universal.APP_NAME
        helper.parent.mkdir(parents=True)
        subprocess.run(["/usr/bin/xcrun", "clang", "-arch", arch, str(helper_source), "-o", str(helper)], check=True)
        arguments = ["--station", "away", "flight with spaces.rocketflight", "Unicode-π", "$(touch never)", ""]
        result = subprocess.run(["/usr/bin/arch", "-" + arch, str(launcher), *arguments],
                                cwd=tmp_path, capture_output=True)
        assert result.returncode == 37, result.stderr
        cwd, raw_arguments = result.stdout.split(b"\n", 1)
        assert Path(os.fsdecode(cwd)) == tmp_path.resolve()
        observed = raw_arguments.split(b"\0")
        assert Path(os.fsdecode(observed[0])).resolve() == helper.resolve()
        assert observed[1:-1] == [argument.encode() for argument in arguments]
    assert not (tmp_path / "never").exists()
