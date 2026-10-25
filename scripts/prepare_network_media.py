#!/usr/bin/env python3
"""Stage the native SRT runtime; builds never depend on a system library at run time.

Mac: copies libsrt and its non-system dylibs, rewrites paths and ad-hoc signs.
Windows: supply a directory containing srt.dll and its redistributable DLLs.
FFmpeg encoding/decoding continues to use the pinned imageio-ffmpeg package.
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

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "vendor" / "network-media"


def run(*args):
    return subprocess.run([str(arg) for arg in args], check=True, capture_output=True, text=True).stdout


def prepare(source):
    DEST.mkdir(parents=True, exist_ok=True)
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
    decoder_output = run(imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-decoders")
    if not all(("libx264" in encoder_output, "mpegts" in muxer_output, " h264 " in decoder_output)):
        raise SystemExit("Runtime requires software libx264 encoding, H.264 decoding and MPEG-TS")
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
