"""Build and verify a native release from one immutable CI checkout."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "build/release-verification"
EVIDENCE.mkdir(parents=True, exist_ok=True)
STATUS = {"status": "running", "steps": []}


def write(name, value):
    (EVIDENCE / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def run(name, *arguments):
    print(f"Starting {name}", flush=True)
    started = time.monotonic()
    with (EVIDENCE / f"{name}.log").open("wb") as log:
        child = subprocess.Popen(list(map(str, arguments)), cwd=ROOT,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT)
        for line in child.stdout:
            log.write(line)
            log.flush()
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        code = child.wait()
    STATUS["steps"].append(dict(name=name, returncode=code,
                                 seconds=round(time.monotonic() - started, 2)))
    write("status.json", STATUS)
    if code:
        raise RuntimeError(f"{name} failed with exit code {code}")


def main():
    system = platform.system()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if commit != os.environ.get("GITHUB_SHA", commit):
        raise RuntimeError("Checkout differs from the requested revision")
    source = dict(commit=commit, system=system, machine=platform.machine(), python=sys.version,
                  run_id=os.environ.get("GITHUB_RUN_ID"),
                  runner_image=os.environ.get("ImageOS"),
                  runner_image_version=os.environ.get("ImageVersion"))
    write("source.json", source)
    run("setup", sys.executable, "scripts/build.py", "setup")
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run("network-media", python, "scripts/build_mission_srt.py")
    run("runtime", python, "scripts/build.py", "runtime")
    run("bridge", python, "scripts/build.py", "bridge", "--download", "v6.2")
    (ROOT / "vendor/source-build.json").write_text(json.dumps(source, indent=2) + "\n")
    run("tests", python, "-m", "pytest", "-q", f"--junitxml={EVIDENCE / 'tests.xml'}")
    run("lint", python, "-m", "ruff", "check", "src", "tests", "scripts/release_ci.py", "--select", "F")
    run("package", python, "scripts/build.py", "package", "--platform", system)
    archives = list((ROOT / "dist").glob("*.dmg" if system == "Darwin" else
                    "*.tar.gz" if system == "Linux" else "*.zip"))
    if len(archives) != 1:
        raise RuntimeError(f"Expected one native package, found {archives}")
    archive = archives[0]
    checksum = digest(archive)
    if archive.with_name(archive.name + ".sha256").read_text().split()[0] != checksum:
        raise RuntimeError("Package checksum mismatch")
    relocated = Path(os.environ.get("RUNNER_TEMP", ROOT / "build")) / "Telemetry UI Package Verification"
    relocated.mkdir(parents=True, exist_ok=True)
    if system == "Darwin":
        mount = relocated / "disk"
        run("mount-image", "hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mount, archive)
        try:
            package = relocated / "RocketGNCMonitor-v0a.app"
            run("relocate", "ditto", mount / "RocketGNCMonitor-v0a.app", package)
        finally:
            run("unmount-image", "hdiutil", "detach", mount)
        run("signature", "codesign", "--verify", "--deep", "--strict", package)
    else:
        if system == "Linux":
            with tarfile.open(archive) as bundle:
                bundle.extractall(relocated, filter="data")
        else:
            with zipfile.ZipFile(archive) as bundle:
                for entry in bundle.infolist():
                    if not (relocated / entry.filename).resolve().is_relative_to(relocated.resolve()):
                        raise RuntimeError("Archive path escapes destination")
                bundle.extractall(relocated)
        package = relocated / "RocketGNCMonitor-v0a"
    verification = ROOT / "build/package-verification"
    run("verify-package", python, "scripts/verify_package.py", "--package", package,
        "--model", ROOT / "tests/fixtures/test_launch.ork", "--output", verification)
    report = json.loads((verification / "report.json").read_text())
    if report.get("check_count") != 18 or report.get("minimal_path") is not True:
        raise RuntimeError("Expected all 18 checks with a minimal system PATH")
    write("verified-package.json", dict(**source, file=archive.name, bytes=archive.stat().st_size,
          sha256=checksum, package_checks=report["check_count"], minimal_path=True,
          relocated_to_path_with_spaces=True, hardware_tested=False))
    STATUS["status"] = "passed"


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        STATUS.update(status="failed", error=str(error))
        raise
    finally:
        for name in ("engine.json", "java-runtime.json", "build-manifest.json"):
            manifest = ROOT / "vendor" / name
            if manifest.is_file():
                shutil.copy2(manifest, EVIDENCE / name)
        write("status.json", STATUS)
