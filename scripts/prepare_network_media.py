#!/usr/bin/env python3
"""Stage the native SRT runtime; builds never depend on a system library at run time.

Mac: copies libsrt and its non-system dylibs, rewrites paths and ad-hoc signs.
Windows: supply a directory containing srt.dll and its redistributable DLLs.
Linux MPEG-TS reception uses a separately pinned decoder; imageio's Linux
7.0.2 executable crashes reading MPEG-TS, including streams it just encoded.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "vendor" / "network-media"
LINUX_FFMPEG_NAME = "ffmpeg-n8.1.3-9-g29e619e767-linux64-gpl-8.1.tar.xz"
LINUX_FFMPEG_SHA = "97ce978979194b5cf7e06a5e68020dbdaa7a4f3294c5452b6a1bc347100dbb79"
LINUX_FFMPEG_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/"
                    "autobuild-2026-09-30-13-08/" + LINUX_FFMPEG_NAME)


def prepare_linux_decoder():
    if platform.system() != "Linux":
        return
    if platform.machine() not in ("x86_64", "AMD64"):
        raise SystemExit("The pinned Linux network decoder requires x86_64")
    decoder, marker = DEST / "ffmpeg", DEST / "ffmpeg-runtime.json"
    if decoder.is_file() and marker.is_file():
        installed = json.loads(marker.read_text())
        if (installed.get("archiveSha256") == LINUX_FFMPEG_SHA
                and installed.get("sha256") == hashlib.sha256(decoder.read_bytes()).hexdigest()):
            return
    cache = ROOT / "build/network-native"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / LINUX_FFMPEG_NAME
    if not archive.is_file():
        partial = archive.with_suffix(".part")
        with urllib.request.urlopen(LINUX_FFMPEG_URL, timeout=120) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output)
        partial.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != LINUX_FFMPEG_SHA:
        raise SystemExit("Pinned Linux FFmpeg archive digest mismatch")
    DEST.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as bundle:
        prefix = LINUX_FFMPEG_NAME.removesuffix(".tar.xz")
        for name, target in (("bin/ffmpeg", decoder), ("LICENSE.txt", DEST / "FFmpeg-LICENSE.txt")):
            entry = bundle.getmember(prefix + "/" + name)
            if not entry.isfile() or entry.size > 512 * 1024 * 1024:
                raise SystemExit("Invalid pinned FFmpeg archive entry")
            with bundle.extractfile(entry) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    decoder.chmod(0o755)
    marker.write_text(json.dumps(dict(source=LINUX_FFMPEG_URL, archiveSha256=LINUX_FFMPEG_SHA,
        sha256=hashlib.sha256(decoder.read_bytes()).hexdigest(),
        purpose="Qualified Linux MPEG-TS network decoder"), indent=2) + "\n")


def run(*args):
    return subprocess.run([str(arg) for arg in args], check=True, capture_output=True, text=True).stdout


def prepare(source):
    DEST.mkdir(parents=True, exist_ok=True)
    prepare_linux_decoder()
    copied = {}
    if platform.system() == "Darwin":
        pending = [source.resolve()]
        while pending:
            current = pending.pop()
            if current in copied:
                continue
            output = DEST / ("libsrt.1.5.dylib" if current == source.resolve() else current.name)
            shutil.copy2(current, output)
            output.chmod(0o755)
            dependencies = []
            for line in run("otool", "-L", current).splitlines()[2:]:
                dep = line.strip().split(" (", 1)[0]
                if dep.startswith(("/usr/lib/", "/System/")):
                    continue
                if not dep.startswith("/"):
                    raise SystemExit(f"Resolve non-absolute dependency before packaging: {dep}")
                resolved = Path(dep).resolve()
                dependencies.append((dep, resolved))
                pending.append(resolved)
            copied[current] = (output, dependencies)
        for output, dependencies in copied.values():
            run("install_name_tool", "-id", "@loader_path/" + output.name, output)
            for old, resolved in dependencies:
                run("install_name_tool", "-change", old,
                    "@loader_path/" + copied[resolved][0].name, output)
            run("codesign", "--force", "--sign", "-", output)
    elif platform.system() == "Windows":
        folder = source if source.is_dir() else source.parent
        for file in folder.glob("*.dll"):
            if file.resolve() != (DEST / file.name).resolve():
                shutil.copy2(file, DEST / file.name)
        if not any((DEST / name).is_file() for name in ("srt.dll", "libsrt.dll")):
            raise SystemExit("The native runtime directory must contain srt.dll or libsrt.dll")
    else:
        shutil.copy2(source, DEST / "libsrt.so.1.5")
    # Licenses supplied with a prebuilt runtime are included unchanged.
    roots = [source.parent, source.resolve().parent.parent, source.parent.parent / "share", source.parent.parent / "licenses"]
    for folder in roots:
        if folder.exists():
            for pattern in ("LICENSE*", "COPYING*", "NOTICE*", "srt/LICENSE*"):
                for file in folder.glob(pattern):
                    if file.is_file():
                        target = DEST / ("SRT-" + file.name)
                        if file.resolve() != target.resolve():
                            shutil.copy2(file, target)
    sys.path.insert(0, str(ROOT / "src"))
    import imageio_ffmpeg
    from rocket_gnc_monitor.network.media_srt import library
    library()
    encoder_output = run(imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-encoders")
    muxer_output = run(imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-muxers")
    decoder = DEST / "ffmpeg" if platform.system() == "Linux" else imageio_ffmpeg.get_ffmpeg_exe()
    decoder_output = run(decoder, "-hide_banner", "-decoders")
    if not all(("libx264" in encoder_output, "mpegts" in muxer_output, " h264 " in decoder_output)):
        raise SystemExit("Runtime requires software libx264 encoding, H.264 decoding and MPEG-TS")
    # Capability lists do not detect the Linux vendor demuxer crash. Read an
    # actual encoded MPEG-TS frame before allowing this runtime into a package.
    sample = subprocess.check_output([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-f", "lavfi",
        "-i", "color=red:size=32x32:rate=1", "-frames:v", "1", "-c:v", "libx264", "-f", "mpegts", "pipe:1"])
    decoded = subprocess.run([str(decoder), "-v", "error", "-f", "mpegts", "-i", "pipe:0",
        "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
        input=sample, capture_output=True, check=True, timeout=30).stdout
    if len(decoded) != 32 * 32 * 3:
        raise SystemExit("The packaged MPEG-TS decoder did not return a complete frame")
    manifest = {"srt": "native libsrt; runtime load verified", "ffmpeg": imageio_ffmpeg.__version__,
                "software_encoder": "libx264", "container": "mpegts", "decoder": "h264",
                "platform": platform.system(), "architecture": platform.machine(),
                "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in DEST.iterdir() if path.is_file() and path.name != "manifest.json"
                          and (path.suffix.lower() != ".dll" or platform.system() == "Windows")
                          and (path.suffix != ".dylib" or platform.system() == "Darwin")}}
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Network media runtime prepared: {DEST}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--srt-library", type=Path,
                        default=Path("/opt/homebrew/lib/libsrt.dylib") if sys.platform == "darwin" else None)
    args = parser.parse_args()
    if args.srt_library is None or not args.srt_library.exists():
        parser.error("Supply --srt-library with the native library or Windows DLL directory")
    prepare(args.srt_library)
