"""Mission stream registry and static capture inputs for the real leased SRT path."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QImage

from .station_profile import StationProfile


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
    def __init__(self, window, streams, directory):
        self.window = window
        self.sources = {s['id']: MissionCapture(s) for s in streams if s['id'] in window.profile.local_channels}
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
        while not self.stop_event.wait(1 / 30):
            for key, source in self.sources.items():
                record = source.publish(time.monotonic())
                if record:
                    self.log.write(json.dumps(dict(record, streamId=key)) + '\n')
            self.log.flush()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=3)
        self.log.close()
        if self.wall_log:
            self.window.station_network.viewer.on_received = None
            self.wall_log.close()
