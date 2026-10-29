"""OpenRocket-owned, headless Away services. Control is private stdin/stdout JSON.

Public endpoints are the ordinary rt-gs.v1 protocol; no configuration/control
socket or Telemetry UI installation is required by OpenRocket.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from .domain import Sample
from .media import ffmpeg, popen_options
from .network.runtime import NetworkRuntime
from .network.media import MediaService
from .network.observation import observation_from_sample
from .station_profile import StationProfile
from .mission_recording import MissionRecording


@dataclass(frozen=True)
class StreamProfile(StationProfile):
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
    def channel_vehicles(self):
        return {s['id']: s['vehicleId'] for s in self.mission_streams}

    @property
    def channel_labels(self):
        return {s['id']: f"{s['kind'].title()} {s.get('ordinal', i + 1)} · {s['vehicleId']}"
                for i, s in enumerate(self.mission_streams)}

    @property
    def away_channels(self):
        return {'away' + str(i): tuple(s['id'] for s in self.mission_streams if 'gs' + str(i) in s['receivers'])
                for i in range(1, 5)}

    @property
    def local_channels(self):
        return () if self.station == 'base' or self.role != 'video' else self.away_channels[self.station]


class SimulationRuntime(NetworkRuntime):
    def __init__(self, *args, simulation, **kwargs):
        self.simulation = simulation
        super().__init__(*args, **kwargs)

    def _info(self):
        value = {**super()._info(), 'simulation': self.simulation}
        value['capabilities']['pointer_status'] = bool(self.simulation.get('pointerEnabled')) and self.role == 'telem'
        return value


class SlateSource:
    kind, source_mode, error = 'mission', 'simulation', ''

    def __init__(self):
        self.listeners = set()
        self.lock = threading.RLock()
        self.pixels = None
        self.state = None
        self.last_frame = None
        self.paused = False
        self.waiting = True
        self.noise_cache = None

    def load(self, stream):
        path = Path(stream['assetPath'])
        raw = path.read_bytes()
        if len(raw) > 8 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != stream['sha256']:
            raise ValueError('Mission slate hash/size mismatch')
        pixels = subprocess.run([ffmpeg(), '-v', 'error', '-i', 'pipe:0', '-frames:v', '1',
            '-vf', 'scale=960:540', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'], input=raw,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=True,
            **popen_options()).stdout
        if len(pixels) != 960 * 540 * 3:
            raise ValueError('Could not decode mission slate')
        with self.lock:
            self.pixels, self.last_frame, self.state = pixels, None, None
            self.noise_cache = None

    def add_frame_listener(self, callback):
        with self.lock:
            self.listeners.add(callback)

    def remove_frame_listener(self, callback):
        with self.lock:
            self.listeners.discard(callback)

    def publish(self):
        with self.lock:
            if self.pixels is None or self.state is None:
                return
            # A repeated paused frame is a transport carrier, never a logical delivery.
            if not self.paused and self.last_frame == self.state['frame']:
                return
            self.last_frame = self.state['frame']
            pixels = self.pixels
            noise = self.state.get('illustrativeAnalogNoise', 0)
            if noise:
                if self.noise_cache is None or self.noise_cache[0] != noise:
                    data = bytearray(pixels)
                    digest = hashlib.sha256(pixels).digest()
                    for row in range(540):
                        if digest[row % 32] / 255 < noise:
                            data[row * 2880:(row + 1) * 2880] = bytes([digest[(row + 7) % 32]]) * 2880
                    self.noise_cache = noise, bytes(data)
                pixels = self.noise_cache[1]
            for callback in tuple(self.listeners):
                callback(pixels, time.monotonic())
            return (pixels, {**self.state, 'hostMonotonic': time.monotonic(), 'hostUtc': time.time()}) if not self.paused else None


class MissionNetwork:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.services = {}
        self.sources = {}
        self.counters = {}
        self.connections = {}
        self.station_status = {}
        self.rejected = {}
        self.stopped = threading.Event()
        self.recordings = {}
        self.record_logs = {}
        self.record_directory = None
        self.record_enabled = False
        self.frame_lock = threading.RLock()
        self.frame_error = ''
        self.state = 'waiting'
        self.mission_time = '-1000000'
        self.thread = threading.Thread(target=self._frames, name='mission-network-slates', daemon=True)
        self.thread.start()

    def configure(self, mission, *, discovery=True):
        if self.services:
            raise ValueError('Stop the previous mission publisher first')
        config = mission.get('network', {})
        self.record_enabled = mission.get('outputs', {}).get('recordEncodedVideo', False)
        vehicle = config.get('vehicleMode', 'balius')
        streams = tuple({key: stream[key] for key in ('id', 'kind', 'vehicleId', 'receivers')}
                        for stream in mission['video']['streams']) if config.get('video', True) else ()
        sites = [s for s in mission['sites'] if s['id'] in ('gs1', 'gs2', 'gs3', 'gs4') and s['enabled']]
        if not sites:
            raise ValueError('Enable at least one ground station before advertising')
        metadata = {'title': mission['identity']['title'], 'streams': list(streams),
                    'publisher': 'OpenRocket MIT', 'controls': 'OpenRocket Mission Simulation'}
        try:
            for site in sites:
                for role in ('telemetry', 'video'):
                    profile = StreamProfile(station='away' + site['id'][-1], role=role,
                                            vehicle=vehicle, mission_streams=streams)
                    if role == 'video' and not profile.local_channels:
                        continue
                    key = site['id'], role
                    media = None
                    if role == 'video':
                        from .network.media_srt import library
                        library()  # Report missing media support before advertising capabilities.
                        media = MediaService(profile, port_start=55000 + int(site['id'][-1]) * 32,
                            port_end=55031 + int(site['id'][-1]) * 32,
                            bind_host=config.get('bindHost', '0.0.0.0'), allowed_source_modes=('simulation',))
                    runtime = SimulationRuntime(self.directory / ('-'.join(key)), profile,
                        mission['identity']['missionId'], {'network_source_mode': 'simulation',
                        'network_accept_source_modes': [], 'network_port': 0,
                        'network_bind_host': config.get('bindHost', '0.0.0.0'), 'network_discovery': discovery},
                        media_session_backend=media, simulation={**metadata, 'pointerEnabled': any(p.get('enabled') and p['siteId'] == site['id'] for p in mission.get('pointers', []))})
                    self.services[key] = runtime, media
                    self.counters[key] = 0
                    self.connections[key] = str(uuid.uuid4())
                    if media:
                        media.set_context(runtime.info(), runtime.boot_monotonic)
                        media.notify = runtime.update_streams
                        for channel in profile.local_channels:
                            source = SlateSource()
                            self.sources[site['id'], channel] = source
                            media.set_source(channel, source)
                    runtime.start()
            deadline = time.monotonic() + 12
            while any(r.state == 'STARTING' for r, _ in self.services.values()) and time.monotonic() < deadline:
                time.sleep(.02)
            for runtime, _ in self.services.values():
                if runtime.state != 'LISTENING':
                    raise RuntimeError(runtime.error or 'Station network did not start')
            self._status()
            return self.snapshot()
        except BaseException:
            self.close()
            raise

    def begin(self, streams):
        self.finish()
        self.record_directory = self.directory / 'captures' / str(uuid.uuid4()) if self.record_enabled else None
        for stream in streams:
            for (site, channel), source in self.sources.items():
                if channel == stream['id']:
                    source.load(stream)
                    if self.record_directory:
                        self.recordings[site, channel] = MissionRecording(self.record_directory / site / channel)
                        self.record_logs[site, channel] = (self.record_directory / site / channel / 'timing.jsonl').open('w', encoding='utf-8')
        self.state = 'running'
        self._status()
        return self.snapshot()

    def advance(self, time_us, batches, results):
        if self.frame_error:
            raise RuntimeError(self.frame_error)
        self.mission_time = str(time_us)
        by_id = {b['id']: b for b in batches}
        for station in results:
            site = station['id']
            batch = by_id[site]
            service = self.services.get((site, 'telemetry'))
            if service:
                runtime, _ = service
                self.rejected[site] = self.rejected.get(site, 0) + station.get('rejected', 0)
                raw = {d['packetId']: base64.b64decode(d['data'], validate=True) for d in batch['deliveries']}
                for value in station['samples']:
                    sample = Sample.from_dict(value)
                    sample.source = 'SIMULATION'
                    sample.details['missionTimeUs'] = self.mission_time
                    sample.received, sample.utc = time.monotonic(), time.time()
                    packet = raw.get(sample.details.get('packetId'))
                    if packet is None:
                        raise ValueError('Accepted station sample has no corresponding received bytes')
                    key = site, 'telemetry'
                    self.counters[key] += 1
                    payload = observation_from_sample(sample, raw=packet, station=site,
                        node_id=runtime.node_id, boot_id=runtime.boot_id, connection_id=self.connections[key],
                        observation_counter=self.counters[key], boot_monotonic=runtime.boot_monotonic,
                        vehicle_mode=runtime.vehicle_mode, rejected_packets=self.rejected[site])
                    if not runtime.publish_observation(payload):
                        raise RuntimeError(runtime.error or 'Network observation queue unavailable')
                self._status(site, station, batch.get('pointerPose'))
            for state in batch.get('videoState', []):
                source = self.sources.get((site, state['streamId']))
                if source:
                    with source.lock:
                        source.state = state
        for runtime, _ in self.services.values():
            runtime.drain_events()
            if runtime.error or runtime.ingress_dropped:
                raise RuntimeError(runtime.error or 'Network observations lost')
        return {'accepted': True}

    def phase(self, state):
        if state not in ('waiting', 'running', 'paused', 'complete', 'cancelled', 'failed'):
            raise ValueError('Invalid mission network state')
        self.state = state
        for source in self.sources.values():
            with source.lock:
                source.paused = state == 'paused'
                if state in ('cancelled', 'failed', 'complete'):
                    source.state = None
        self._status()
        return self.snapshot()

    def _status(self, site=None, station=None, pose=None):
        if site is not None and station is not None:
            self.station_status[site] = station, pose or {}
        for (identity, role), (runtime, media) in self.services.items():
            if site is not None and site != identity:
                continue
            station, pose = self.station_status.get(identity, ({}, {}))
            runtime.update_status({'application': {'state': self.state, 'source_mode': 'simulation',
                'mission_time_us': self.mission_time, 'simulation': True},
                'telemetry_board': {'availability': 'connected', 'changed_mono_ms': runtime.mono_ms(), 'reason': 'Simulated receiver', 'accepted': str(self.counters.get((identity, 'telemetry'), 0))} if role == 'telemetry' else None,
                'pointer': None if role != 'telemetry' or not runtime.info()['capabilities']['pointer_status'] else {'availability': 'connected', 'changed_mono_ms': runtime.mono_ms(), 'reason': (station or {}).get('reason', 'Simulated pointer'), 'tracking': bool((station or {}).get('tracking')),
                    'pose': pose or {}},
                'video_inputs': media.input_status() if media else None,
                'network': {'ltu_signal_dbm': None}, 'clock': {'quality': 'simulation', 'uncertainty_ms': None}})

    def finish(self):
        with self.frame_lock:
            for source in self.sources.values():
                with source.lock:
                    source.state = None
            errors = []
            for recording in self.recordings.values():
                try:
                    recording.close()
                except ValueError as exc:
                    errors.append(str(exc))
            self.recordings.clear()
            for log in self.record_logs.values():
                log.close()
            self.record_logs.clear()
            result = {'files': [{'path': str(p), 'name': str(p.relative_to(self.record_directory)).replace('\\', '/')}
                             for p in self.record_directory.rglob('*') if p.is_file()]} if self.record_directory else {'files': []}
            result['error'] = '; '.join(errors)
            return result

    def snapshot(self):
        return {'state': self.state, 'timeUs': self.mission_time, 'services': [
            {'station': site, 'role': role, 'endpoint': runtime.local_endpoint,
             'state': runtime.state, 'error': runtime.error, 'info': runtime.info(),
             'addresses': [f'http://{address}:{runtime.port}' for address in (runtime._service_info.parsed_addresses() if runtime._service_info else [])],
             'streams': media.streams() if media else []}
            for (site, role), (runtime, media) in self.services.items()]}

    def _frames(self):
        while not self.stopped.wait(1 / 30):
            try:
                with self.frame_lock:
                    for key, source in list(self.sources.items()):
                        frame = source.publish()
                        if frame is not None and key in self.recordings:
                            self.recordings[key].feed(frame[0])
                            self.record_logs[key].write(json.dumps(frame[1], allow_nan=False) + '\n')
            except Exception as exc:
                self.frame_error = str(exc)

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=2)
        try:
            self.finish()
        except ValueError:
            pass  # The Java runner requests finish explicitly and records any failure.
        def stop(entry):
            runtime, media = entry
            runtime.stop()
            if media:
                media.close()
        with ThreadPoolExecutor(max_workers=8) as closer:
            list(closer.map(stop, self.services.values()))
        self.services.clear()
        self.sources.clear()


def main():
    publisher = MissionNetwork(sys.argv[1])
    try:
        while True:
            line = sys.stdin.buffer.readline(4 * 1024 * 1024 + 1)
            if not line:
                break
            try:
                if len(line) > 4 * 1024 * 1024:
                    raise ValueError('Control message too large')
                request = json.loads(line)
                operation = request['operation']
                if operation == 'configure':
                    result = publisher.configure(request['mission'], discovery=request.get('discovery', True))
                elif operation == 'begin':
                    result = publisher.begin(request['streams'])
                elif operation == 'advance':
                    result = publisher.advance(request['timeUs'], request['batches'], request['stations'])
                elif operation == 'phase':
                    result = publisher.phase(request['state'])
                elif operation == 'finish':
                    result = publisher.finish()
                elif operation == 'status':
                    result = publisher.snapshot()
                elif operation == 'close':
                    break
                else:
                    raise ValueError('Unknown operation')
                response = {'ok': True, 'result': result}
            except Exception as exc:
                response = {'ok': False, 'error': str(exc)}
            print(json.dumps(response, allow_nan=False, separators=(',', ':')), flush=True)
    finally:
        publisher.close()


if __name__ == '__main__':
    main()
