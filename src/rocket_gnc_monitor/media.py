"""One FFmpeg input serves display and optional recording; no GUI-thread I/O."""

from __future__ import annotations
from collections import deque
import os
from pathlib import Path
import platform
import queue
import re
import subprocess
import threading
import time
import imageio_ffmpeg

DEFAULT_VIDEO_CHANNELS = ("digital", "analog")
VIDEO_STREAMS = {"digital": "Digital", "analog": "Analog", "analog2": "Analog 2"}
WIDTH, HEIGHT = 960, 540


def ffmpeg():
    # imageio-ffmpeg's wheel includes a native executable on each release platform.
    return imageio_ffmpeg.get_ffmpeg_exe()


def popen_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _directshow_cameras(lines):
    """Keep receiver identities distinct when USB adapters share a display name."""
    devices, pending = [], None
    for line in lines:
        device = re.search(r'"(.+)" \((video|audio)\)\s*$', line)
        alternative = re.search(r'Alternative name "(.+)"\s*$', line)
        if device:
            pending = [device[1], device[1]] if device[2] == "video" else None
            if pending is not None:
                devices.append(pending)
        elif alternative and pending is not None:
            # FFmpeg prints the alternative identifier immediately after its
            # device. Never attach an audio or orphan identifier to a camera.
            pending[1] = alternative[1]
            pending = None
        else:
            pending = None
    unique = {}
    for title, source in devices:
        unique.setdefault(source, (title, source))
    return list(unique.values())


def camera_devices():
    system = platform.system()
    if system == "Darwin":
        command = [ffmpeg(), "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""]
    elif system == "Windows":
        command = [ffmpeg(), "-hide_banner", "-f", "dshow", "-list_devices", "true", "-i", "dummy"]
    else:
        return [(str(p), str(p)) for p in sorted(Path("/dev").glob("video*"))]
    run = subprocess.run(command, capture_output=True, timeout=12, **popen_options())
    lines = run.stderr.decode(errors="replace").splitlines()
    if system == "Windows":
        return _directshow_cameras(lines)
    devices, video = [], False
    for line in lines:
        if system == "Darwin":
            if "AVFoundation video devices" in line:
                video = True
            elif "AVFoundation audio devices" in line:
                video = False
            elif video and (match := re.search(r"\[(\d+)\] (.+)$", line)):
                if "Capture screen" not in match[2]:
                    devices.append((match[2], match[1]))
    return devices


class VideoWorker:
    def __init__(self, kind, source="", record_dir=None, seek=0.0, speed=1.0, single_frame=False):
        self.kind, self.source = kind, source
        self.record_dir = Path(record_dir) if record_dir else None
        self.seek = seek
        self.speed = speed
        self.single_frame = single_frame
        self.frames = queue.Queue(maxsize=2)
        self.logs = deque(maxlen=25)
        self.error = ""
        self.last_frame = 0.0
        self.received_frames = 0
        self.stopped = threading.Event()
        self.process = None
        self.thread = threading.Thread(target=self.run, name="video-decoder", daemon=True)
        self.thread.start()

    def arguments(self):
        args = [ffmpeg(), "-hide_banner", "-loglevel", "warning", "-y"]
        if self.kind == "demo":
            pattern = {"analog": "smptebars", "analog2": "testsrc"}.get(self.source, "testsrc2")
            args += ["-re", "-f", "lavfi", "-i", f"{pattern}=size=960x540:rate=30"]
        elif self.kind == "camera":
            if platform.system() == "Darwin":
                args += ["-f", "avfoundation", "-framerate", "30", "-i", f"{self.source}:none"]
            elif platform.system() == "Windows":
                args += ["-f", "dshow", "-i", f"video={self.source}"]
            else:
                args += ["-f", "v4l2", "-i", self.source]
        elif self.kind == "file":
            args += ["-readrate", str(self.speed), "-ss", str(max(self.seek, 0)), "-i", self.source]
        else:
            raise ValueError("Unsupported video input")
        args += ["-map", "0:v:0", "-an"]
        if self.single_frame:
            args += ["-frames:v", "1"]
        args += [
            "-vf",
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            # Raw USB inputs need compression. MPEG-4 encoding is bundled on all target wheels.
            args += ["-map", "0:v:0", "-an"]
            if self.kind == "file":
                args += ["-c:v", "copy"]
            else:
                args += ["-c:v", "mpeg4", "-q:v", "4", "-g", "30", "-pix_fmt", "yuv420p"]
            args += [
                "-f",
                "segment",
                "-segment_time",
                "30",
                "-reset_timestamps",
                "1",
                "-segment_list",
                str(self.record_dir / "segments.csv"),
                "-segment_list_type",
                "csv",
                str(self.record_dir / "segment_%05d.mkv"),
            ]
        return args

    def drain_logs(self):
        for line in iter(self.process.stderr.readline, b""):
            self.logs.append(line.decode(errors="replace").strip()[-1000:])

    def run(self):
        try:
            self.process = subprocess.Popen(
                self.arguments(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=WIDTH * HEIGHT * 3,
                **popen_options(),
            )
            log_thread = threading.Thread(target=self.drain_logs, daemon=True)
            log_thread.start()
            frame_size = WIDTH * HEIGHT * 3
            if self.stopped.is_set():
                self.process.stdin.write(b"q\n")
                self.process.stdin.flush()
            while True:
                data = bytearray()
                while len(data) < frame_size:
                    chunk = self.process.stdout.read(frame_size - len(data))
                    if not chunk:
                        break
                    data.extend(chunk)
                if len(data) != frame_size:
                    break
                self.last_frame = time.monotonic()
                self.received_frames += 1
                if self.frames.full():
                    try:
                        self.frames.get_nowait()
                    except queue.Empty:
                        pass
                try:
                    self.frames.put_nowait(bytes(data))
                except queue.Full:
                    pass
            if not self.stopped.is_set():
                code = self.process.wait(timeout=3)
                log_thread.join(timeout=1)
                self.error = "Video ended" if code == 0 else ("Video failed: " + " · ".join(self.logs)[-700:])
        except Exception as exc:
            self.error = str(exc)
        finally:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            if self.process:
                self.process.stdin.close()
                self.process.stdout.close()
                self.process.stderr.close()

    def stop(self):
        self.stopped.set()
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.write(b"q\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        # Continue draining stdout until FFmpeg has finalized the recording trailer/index.
        self.thread.join(timeout=2)
        if self.thread.is_alive() and self.process and self.process.poll() is None:
            self.process.terminate()
            self.thread.join(timeout=2)
        if self.thread.is_alive() and self.process and self.process.poll() is None:
            self.process.kill()
            self.thread.join(timeout=1)
