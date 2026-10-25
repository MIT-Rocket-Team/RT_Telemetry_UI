"""One FFmpeg input serves display and optional recording; no GUI-thread I/O."""

from __future__ import annotations
from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import queue
import re
import subprocess
import threading
import time
import uuid
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


class LocalVideoRecorder:
    """Independent recording sink; disk errors cannot stop USB acquisition."""

    def __init__(self, owner):
        self.owner = owner
        self.frames = queue.Queue(maxsize=12)
        self.stopped = threading.Event()
        self.process = None
        self.logs = deque(maxlen=8)
        self.thread = threading.Thread(target=self.run, name="local-video-archive", daemon=True)
        self.thread.start()

    def feed(self, frame):
        if self.stopped.is_set():
            return
        try:
            self.frames.put_nowait(frame)
        except queue.Full:
            self.owner.recording_error = "Video archive cannot keep up; recording stopped with a gap"
            self.owner._record_event(dict(kind="recording.gap", reason=self.owner.recording_error))
            self.stopped.set()

    def run(self):
        try:
            folder = self.owner.record_dir
            folder.mkdir(parents=True, exist_ok=True)
            args = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
                    "-pixel_format", "rgb24", "-video_size", f"{WIDTH}x{HEIGHT}", "-framerate", "30",
                    "-use_wallclock_as_timestamps", "1", "-i", "pipe:0", "-an", "-c:v", "mpeg4",
                    "-q:v", "4", "-g", "30", "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
                    "-f", "segment", "-segment_time", "30", "-reset_timestamps", "1", "-segment_list",
                    str(folder / "segments.csv"), "-segment_list_type", "csv", str(folder / "segment_%05d.mkv")]
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.PIPE, bufsize=0, **popen_options())
            threading.Thread(target=self._logs, daemon=True).start()
            while not self.stopped.is_set() or not self.frames.empty():
                try:
                    data = memoryview(self.frames.get(timeout=.2))
                except queue.Empty:
                    if self.process.poll() is not None:
                        raise OSError("Recorder exited: " + " · ".join(self.logs))
                    continue
                while data:
                    count = self.process.stdin.write(data)
                    if not count:
                        raise OSError("Recorder input closed")
                    data = data[count:]
            self.process.stdin.close()
            if self.process.wait(timeout=3):
                raise OSError("Video recording failed: " + " · ".join(self.logs))
        except Exception as exc:
            self.owner.recording_error = "Video recording failed: " + str(exc)
            self.owner.logs.append(self.owner.recording_error)
        finally:
            self.stopped.set()
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()

    def _logs(self):
        for line in iter(self.process.stderr.readline, b""):
            self.logs.append(line.decode(errors="replace").strip()[-500:])

    def stop(self):
        self.stopped.set()
        self.thread.join(timeout=4)
        if self.thread.is_alive() and self.process and self.process.poll() is None:
            self.process.terminate()


class VideoWorker:
    def __init__(self, kind, source="", record_dir=None, seek=0.0, speed=1.0, single_frame=False):
        if kind not in {"camera", "file"}:
            raise ValueError("Unsupported video input")
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
        self._capture_finished = threading.Event()
        self.process = None
        self._frame_listeners = set()
        self._frame_listener_lock = threading.Lock()
        self.capture_epoch = str(uuid.uuid4())
        self.recording_error = ""
        self.recording_context = {}
        self._metadata = queue.Queue(maxsize=4096)
        self._metadata_thread = None
        if self.record_dir:
            self._metadata_thread = threading.Thread(target=self._record_metadata, name="video-provenance", daemon=True)
            self._metadata_thread.start()
        self._recorder = LocalVideoRecorder(self) if self.record_dir else None
        self.thread = threading.Thread(target=self.run, name="video-decoder", daemon=True)
        self.thread.start()

    def arguments(self):
        args = [ffmpeg(), "-hide_banner", "-loglevel", "warning", "-y"]
        if self.kind == "camera":
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
            "-fps_mode",
            "passthrough",
            "-f",
            "rawvideo",
            "pipe:1",
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
                if self.record_dir:
                    self._record_event(dict(kind="capture.frame", frame_id=str(self.received_frames),
                                            capture_epoch=self.capture_epoch,
                                            received_host_mono_ns=str(time.monotonic_ns()),
                                            received_time_utc=datetime.now(timezone.utc).isoformat(),
                                            timing_basis="host_frame_delivery_after_decode",
                                            capture_delay_uncertainty_ms=None, clock_uncertainty_ms=None))
                frame = bytes(data)
                if self._recorder:
                    self._recorder.feed(frame)
                with self._frame_listener_lock:
                    listeners = tuple(self._frame_listeners)
                for listener in listeners:
                    # Subscribers must enqueue only: never encode or perform I/O
                    # here, so remote media cannot backpressure local capture.
                    try:
                        listener(frame, self.last_frame)
                    except Exception as exc:
                        self.logs.append("Frame subscriber failed: " + str(exc)[:300])
                if self.frames.full():
                    try:
                        self.frames.get_nowait()
                    except queue.Empty:
                        pass
                try:
                    self.frames.put_nowait(frame)
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
            self._capture_finished.set()
            if self._recorder:
                self._recorder.stopped.set()

    def add_frame_listener(self, listener):
        with self._frame_listener_lock:
            self._frame_listeners.add(listener)

    def remove_frame_listener(self, listener):
        with self._frame_listener_lock:
            self._frame_listeners.discard(listener)

    def set_recording_context(self, context):
        """Network identity/mission metadata, kept independently of viewer quality."""
        self.recording_context = dict(context)
        if self.record_dir:
            self._record_event(dict(kind="capture.context", capture_epoch=self.capture_epoch,
                                    context=self.recording_context))

    def record_network_timing(self, rendition, timing):
        if self.record_dir and timing:
            self._record_event(dict(kind="network.rendition_timing", rendition=rendition, **timing))

    def _record_event(self, value):
        if self.recording_error:
            return
        try:
            self._metadata.put_nowait(value)
        except queue.Full:
            self.recording_error = "Video provenance archive queue full; metadata gap"
            self.logs.append(self.recording_error)

    def _record_metadata(self):
        try:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            with (self.record_dir / "capture-timing.jsonl").open("a", encoding="utf-8") as output:
                header = dict(kind="capture.open", capture_epoch=self.capture_epoch,
                              source_mode="live" if self.kind == "camera" else "replay",
                              source_kind=self.kind, source=self.source,
                              decoded_resolution=[WIDTH, HEIGHT], decoder="FFmpeg",
                              local_recording_codec="mpeg4", recording_resolution=[WIDTH, HEIGHT],
                              context=self.recording_context)
                output.write(json.dumps(header, allow_nan=False) + "\n")
                while not self._capture_finished.is_set() or not self._metadata.empty():
                    try:
                        entry = self._metadata.get(timeout=.2)
                    except queue.Empty:
                        output.flush()
                        continue
                    output.write(json.dumps(entry, allow_nan=False) + "\n")
                if self.recording_error:
                    output.write(json.dumps(dict(kind="archive.gap", reason=self.recording_error)) + "\n")
                output.flush()
        except (OSError, ValueError) as exc:
            self.recording_error = f"Video provenance recording failed: {exc}"
            self.logs.append(self.recording_error)

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
        if self._metadata_thread:
            self._metadata_thread.join(timeout=1)
        if self._recorder:
            self._recorder.stop()
