"""Mission stream registry and static capture inputs for the real leased SRT path."""
from __future__ import annotations

import hashlib
import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QImage

from .media import ffmpeg, popen_options
from .station_profile import StationProfile


class MissionRecording:
    """Optional bounded host-clock recording; logical timing remains in the sidecar."""
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True)
        self.frames = queue.Queue(maxsize=12)
        self.stopped = threading.Event()
        self.error = ''
        self.process = None
        self.thread = threading.Thread(target=self.run, name='mission-video-recording', daemon=True)
        self.thread.start()

    def feed(self, pixels):
        if self.error:
            raise ValueError(self.error)
        try:
            self.frames.put_nowait(pixels)
        except queue.Full:
            self.error = 'Encoded mission recording cannot keep up; committed frames are preserved'
            self.stopped.set()
            raise ValueError(self.error) from None

    def run(self):
        try:
            with (self.folder / 'encoder.log').open('wb') as log:
                self.process = subprocess.Popen([
                    ffmpeg(), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo',
                    '-pixel_format', 'rgb24', '-video_size', '960x540', '-framerate', '30',
                    '-use_wallclock_as_timestamps', '1', '-i', 'pipe:0', '-an', '-c:v', 'mpeg4',
                    '-q:v', '4', '-g', '30', '-pix_fmt', 'yuv420p', '-fps_mode', 'vfr',
                    '-f', 'segment', '-segment_time', '30', '-reset_timestamps', '1',
                    '-segment_list', str(self.folder / 'segments.csv'), '-segment_list_type', 'csv',
                    str(self.folder / 'segment_%05d.mkv')], stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=log, bufsize=0, **popen_options())
                while not self.stopped.is_set() or not self.frames.empty():
                    try:
                        data = memoryview(self.frames.get(timeout=.2))
                    except queue.Empty:
                        if self.process.poll() is not None:
                            raise OSError('Mission video encoder exited early')
                        continue
                    while data:
                        count = self.process.stdin.write(data)
                        if not count:
                            raise OSError('Mission video encoder closed its input')
                        data = data[count:]
                self.process.stdin.close()
                if self.process.wait(timeout=5):
                    raise OSError('Mission video encoder failed; see encoder.log')
        except Exception as exc:  # noqa: BLE001 -- propagate thread failures at the next boundary
            self.error = str(exc)
        finally:
            if self.process and self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=3)

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=7)
        if self.thread.is_alive():
            if self.process:
                self.process.kill()
            self.thread.join(timeout=3)
            self.error = 'Mission recording did not finalize in time'
        if self.error:
            raise ValueError(self.error)


@dataclass(frozen=True)
class MissionProfile(StationProfile):
    mission_streams: tuple = ()

    @property
    def channels(self):
        return tuple(s['id'] for s in self.mission_streams)

    @property
    def channel_identifiers(self):
        return {s['id']: s['id'] for s in self.mission_streams}

    @property
    def channel_kinds(self):
        return {s['id']: s['kind'] for s in self.mission_streams}

    @property
    def channel_labels(self):
        return {s['id']: f"{s['kind'].title()} {s['ordinal']} · {s['vehicleId']}" for s in self.mission_streams}

    @property
    def away_channels(self):
        return {'away' + str(i): tuple(s['id'] for s in self.mission_streams if 'gs' + str(i) in s['receivers'])
                for i in range(1, 5)}

    @property
    def local_channels(self):
        # Base consumes remote streams; a telemetry-only role does not open video inputs.
        return () if self.station == 'base' or self.role != 'video' else self.away_channels[self.station]


class MissionCapture:
    kind, source_mode, error = 'mission', 'simulation', ''

    def __init__(self, stream):
        path = Path(stream['assetPath'])
        raw = path.read_bytes()
        if len(raw) > 8 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != stream['sha256']:
            raise ValueError('Mission slate hash/size mismatch')
        image = QImage.fromData(raw).convertToFormat(QImage.Format.Format_RGB888)
        if image.isNull() or (image.width(), image.height()) != (960, 540):
            raise ValueError('Mission slate must be 960 x 540')
        self.pixels = bytes(image.constBits())
        self.listeners = set()
        self.lock = threading.RLock()
        self.state = None
        self.time_us = -1_000_000
        self.paused = False
        self.last_frame = None
        self.recording_error = ''
        self.noisy = {}
        self.published_frame = None
        self.capture_counter = 0

    def add_frame_listener(self, callback):
        with self.lock:
            self.listeners.add(callback)

    def remove_frame_listener(self, callback):
        with self.lock:
            self.listeners.discard(callback)

    def publish(self, stamp):
        with self.lock:
            if self.state is None or (not self.paused and self.time_us - int(self.state['timeUs']) > 5_000_000):
                return None
            if not self.paused and self.published_frame == self.state["frame"]:
                return None
            self.published_frame = self.state["frame"]
            self.capture_counter += 1
            pixels = self.pixels
            noise = self.state.get('illustrativeAnalogNoise', 0)
            if noise:
                # A deterministic static illustrative interference pattern; never an RF model.
                if noise not in self.noisy:
                    import numpy as np
                    array = np.frombuffer(pixels, dtype=np.uint8).copy()
                    rng = np.random.Generator(np.random.PCG64(int(hashlib.sha256(pixels).hexdigest()[:16], 16)))
                    mask = rng.random(len(array) // 3) < noise
                    array.reshape(-1, 3)[mask] = rng.integers(0, 256, size=(int(mask.sum()), 3), dtype=np.uint8)
                    self.noisy[noise] = array.tobytes()
                pixels = self.noisy[noise]
            self.last_frame = pixels
            for callback in tuple(self.listeners):
                callback(pixels, stamp)
            return {'missionTimeUs': str(self.time_us), 'frame': self.state['frame'],
                        'deliveredUs': self.state['timeUs'], 'hostMonotonic': stamp,
                        'pauseCarrier': self.paused, 'captureCounter': str(self.capture_counter), 'noiseModel': 'illustrative-static-pcg64.v1'}


class MissionVideoPublisher:
    def __init__(self, window, streams, directory, *, record=False):
        self.window = window
        self.sources = {s['id']: MissionCapture(s) for s in streams if s['id'] in window.profile.local_channels}
        self.recordings = {}
        self.error = ''
        if record:
            for key in self.sources:
                folder = window.controller.recorder.path / ('video_' + key)
                self.recordings[key] = MissionRecording(folder)
                window.controller.log('Video recording started', {'directory': folder.name, 'kind': 'mission', 'stream': key})
        self.wall_log = None
        if window.station_network.viewer:
            self.wall_log = (Path(directory) / "wall-timing.jsonl").open("w", encoding="utf-8")
            def received(record):
                self.wall_log.write(json.dumps(record) + "\n")
                self.wall_log.flush()
            window.station_network.viewer.on_received = received
        self.stop_event = threading.Event()
        self.log = (Path(directory) / 'capture-timing.jsonl').open('w', encoding='utf-8')
        for key, source in self.sources.items():
            window.station_network.media.set_source(key, source)
        self.thread = threading.Thread(target=self.run, name='mission-slate-capture', daemon=True)
        self.thread.start()

    def update(self, time_us, states):
        if self.error:
            raise ValueError(self.error)
        for recording in self.recordings.values():
            if recording.error:
                raise ValueError(recording.error)
        by_id = {s['streamId']: s for s in states}
        for key, source in self.sources.items():
            with source.lock:
                source.time_us = time_us
                if key in by_id:
                    source.state = by_id[key]

    def pause(self, paused):
        for source in self.sources.values():
            with source.lock:
                source.paused = paused

    def run(self):
        try:
            while not self.stop_event.wait(1 / 30):
                for key, source in self.sources.items():
                    record = source.publish(time.monotonic())
                    if record:
                        self.log.write(json.dumps(dict(record, streamId=key)) + '\n')
                        if key in self.recordings and not record['pauseCarrier']:
                            self.recordings[key].feed(source.last_frame)
                self.log.flush()
        except Exception as exc:  # noqa: BLE001 -- canonical mission fails rather than dropping recording errors
            self.error = str(exc)

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=3)
        self.log.close()
        failures = []
        for recording in self.recordings.values():
            try:
                recording.close()
            except ValueError as exc:
                failures.append(str(exc))
        if self.wall_log:
            self.window.station_network.viewer.on_received = None
            self.wall_log.close()
        if failures or self.error:
            raise ValueError('; '.join(failures + ([self.error] if self.error else [])))
